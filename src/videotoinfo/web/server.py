"""Yerel web arayüzü: mevcut işlem hattını HTTP üzerinden sürer."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from videotoinfo.config import Settings, config_path, load_settings
from videotoinfo.inbox import bekleyen_urller, hazirla, kuyrugu_isle, tur_belirle
from videotoinfo.llm import measure_context
from videotoinfo.sohbet import gecmis_oku, gecmis_sil, nota_yaz, sor
from videotoinfo.vault import find_note, listeleri_guncelle

STATIC = Path(__file__).parent / "static"
KIMLIK_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ADIMLAR = ["Video bilgisi ve ses", "Yazıya dökme", "Özetleme", "İddiaları doğrulama", "Seslendirme", "Obsidian notu"]

def _evet(deger: object) -> bool:
    if isinstance(deger, bool):
        return deger
    return str(deger).strip().lower() in {"true", "1", "evet", "on", "yes"}


# (bölüm, anahtar) -> tür. Ayarlar ekranı yalnızca bunları yazabilir.
ALANLAR: dict[tuple[str, str], object] = {
    ("genel", "ozet_dili"): str,
    ("genel", "veri_klasoru"): str,
    ("genel", "vault"): str,
    ("ollama", "model"): str,
    ("ollama", "num_ctx"): int,
    ("ollama", "temperature"): float,
    ("ollama", "num_predict"): int,
    ("ollama", "think"): str,
    ("ollama", "keep_alive"): str,
    ("ollama", "model_ozet"): str,
    ("ollama", "model_iddia"): str,
    ("ollama", "model_hukum"): str,
    ("ollama", "think_hukum"): str,
    ("ollama", "model_sohbet"): str,
    ("whisper", "model"): str,
    ("whisper", "bolum_saniye"): int,
    ("arama", "saglayici"): str,
    ("arama", "bolge"): str,
    ("arama", "backend"): str,
    ("arama", "max_sonuc"): int,
    ("arama", "max_iddia"): int,
    ("arama", "sayfa_karakter"): int,
    ("yazi", "bolum_karakter"): int,
    ("yazi", "paket_karakter"): int,
    ("yazi", "alternatif_kaynak"): _evet,
    ("yazi", "cerez_tarayici"): str,
    ("ses", "ses"): str,
    ("ses", "ses_en"): str,
}
THINK = {"false", "low", "medium", "high", "xhigh"}


class Soru(BaseModel):
    soru: str


class IsIstegi(BaseModel):
    url: str
    dogrula: bool = True
    ses: bool = False
    bastan: bool = False


def adim_bul(mesaj: str, simdiki: int) -> int:
    m = mesaj.lower()
    if m.startswith("hata") or m.startswith("uyarı"):
        return simdiki
    if "not yazıldı" in m:
        return 5
    if "seslendir" in m:
        return 4
    if "doğrula" in m or "iddia" in m:
        return max(simdiki, 3)
    if "özet" in m and "önbellek" not in m:
        return max(simdiki, 2)
    if "özet" in m:
        return max(simdiki, 2)
    if "yazıya dök" in m or "transkript" in m:
        return max(simdiki, 1)
    return simdiki


class Isler:
    """Tek işçi iş parçacığı: işlemler sırayla çalışır, çünkü model ve Whisper bellek ister."""

    def __init__(self, ayar_getir) -> None:
        self.ayar_getir = ayar_getir
        self.isler: dict[str, dict] = {}
        self.sira: queue.Queue[str] = queue.Queue()
        self.kilit = threading.Lock()
        threading.Thread(target=self._calis, daemon=True).start()

    def ekle(self, tur: str, url: str, secenek: dict) -> dict:
        is_id = uuid.uuid4().hex[:10]
        is_ = {
            "id": is_id,
            "tur": tur,
            "url": url,
            "baslik": "",
            "durum": "bekliyor",
            "adim": 0,
            "adimlar": ADIMLAR,
            "log": [],
            "hata": "",
            "sonuc": "",
            "secenek": secenek,
            "olusturma": time.time(),
        }
        with self.kilit:
            self.isler[is_id] = is_
        self.sira.put(is_id)
        return is_

    def liste(self) -> list[dict]:
        with self.kilit:
            return sorted((self._ozet(i) for i in self.isler.values()), key=lambda i: -i["olusturma"])

    def aktif_url_var(self, url: str) -> bool:
        with self.kilit:
            return any(i["url"] == url and i["durum"] in ("bekliyor", "calisiyor") for i in self.isler.values())

    def getir(self, is_id: str) -> dict | None:
        with self.kilit:
            is_ = self.isler.get(is_id)
            return dict(is_, log=list(is_["log"])) if is_ else None

    @staticmethod
    def _ozet(is_: dict) -> dict:
        return {k: is_[k] for k in ("id", "tur", "url", "baslik", "durum", "adim", "hata", "olusturma")}

    def _log(self, is_: dict, mesaj: str) -> None:
        with self.kilit:
            is_["log"].append({"t": time.strftime("%H:%M:%S"), "m": str(mesaj)})
            is_["adim"] = adim_bul(str(mesaj), is_["adim"])
            if str(mesaj).startswith("Yazıya dökülüyor: "):
                is_["baslik"] = str(mesaj).split(": ", 1)[1]

    def _calis(self) -> None:
        from videotoinfo.pipeline import isle, yazi_isle

        while True:
            is_ = self.isler[self.sira.get()]
            ayar = self.ayar_getir()
            is_["durum"] = "calisiyor"
            log = lambda m: self._log(is_, m)  # noqa: E731
            sec = is_["secenek"]
            try:
                if is_["tur"] == "kuyruk":
                    hatali = kuyrugu_isle(ayar, dogrula_iste=sec["dogrula"], bastan=sec["bastan"], log=log)
                    if hatali:
                        raise RuntimeError(f"{hatali} bağlantı işlenemedi")
                    yol = ""
                elif tur_belirle(is_["url"]) == "video":
                    yol = isle(is_["url"], ayar, dogrula_iste=sec["dogrula"], ses=sec["ses"], bastan=sec["bastan"], log=log)
                else:
                    yol = yazi_isle(is_["url"], ayar, dogrula_iste=sec["dogrula"], bastan=sec["bastan"], log=log)
                is_["sonuc"] = str(yol)
                is_["adim"] = 6
                is_["durum"] = "bitti"
            except Exception as hata:  # noqa: BLE001 - hata kullanıcıya gösterilir
                is_["hata"] = str(hata)
                is_["durum"] = "hata"
                self._log(is_, f"Hata: {hata}")


def _oku_json(yol: Path):
    if not yol.exists():
        return None
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ses_yolu(ayar: Settings, klasor: Path) -> Path | None:
    kayit = _oku_json(klasor / "ses.json")
    if not kayit or not kayit.get("dosya"):
        return None
    ad = Path(str(kayit["dosya"])).name
    yol = ayar.vault / "Sesler" / ad
    return yol if yol.is_file() else None


def kayit_ozeti(ayar: Settings, klasor: Path) -> dict | None:
    meta = _oku_json(klasor / "meta.json")
    if not isinstance(meta, dict):
        return None
    ozet = _oku_json(klasor / "summary.json") or {}
    iddialar = (_oku_json(klasor / "factcheck.json") or {}).get("iddialar") or []
    video = "video_id" in meta
    ilk = (ozet.get("kisa") or "").strip().splitlines()
    return {
        "id": klasor.name,
        "tur": "video" if video else "yazi",
        "baslik": meta.get("baslik") or "Başlıksız",
        "kanal": meta.get("kanal") or meta.get("yazar") or meta.get("site") or "",
        "platform": meta.get("platform") or meta.get("site") or "",
        "tarih": meta.get("yayin_tarihi") or "",
        "sure": int(meta.get("sure_saniye") or 0),
        "url": meta.get("url") or "",
        "ozet": ilk[0].lstrip("-• ").strip() if ilk else "",
        "etiketler": ozet.get("etiketler") or [],
        "bolum": len(ozet.get("bolumler") or []),
        "ozetli": bool(ozet),
        "iddia": len(iddialar),
        "ses": _ses_yolu(ayar, klasor) is not None,
        "zaman": klasor.stat().st_mtime,
    }


def _not_yolu(ayar: Settings, kimlik: str, tur: str) -> Path | None:
    if tur == "video":
        return find_note(ayar.vault, "Videolar", "video_id", kimlik)
    return find_note(ayar.vault, "Yazilar", "yazi_id", kimlik)


def kayit_sil(ayar: Settings, kimlik: str) -> list[str]:
    """Veri klasörünü, vault notunu, transkript notunu ve sesli özeti siler. Silinenleri döndürür."""
    klasor = ayar.data_dir / kimlik
    ozet = kayit_ozeti(ayar, klasor)
    silinen: list[str] = []
    if ozet:
        not_yolu = _not_yolu(ayar, kimlik, ozet["tur"])
        ses = _ses_yolu(ayar, klasor)
        adaylar = [not_yolu, ses]
        if not_yolu is not None and ozet["tur"] == "video":
            adaylar.append(ayar.vault / "Transkriptler" / not_yolu.name)
        for yol in adaylar:
            if yol is not None and yol.is_file():
                yol.unlink()
                silinen.append(str(yol))
    if klasor.is_dir():
        shutil.rmtree(klasor)
        silinen.append(str(klasor))
    if ayar.vault.is_dir():
        listeleri_guncelle(ayar.vault)
    return silinen


def uygulama(config_yolu: Path | None = None) -> FastAPI:
    app = FastAPI(title="VideoToInfo")
    ayar_yol = config_path(config_yolu) or Path("config.toml")

    def ayar() -> Settings:
        return load_settings(ayar_yol if ayar_yol.exists() else None)

    isler = Isler(ayar)

    @app.get("/api/library")
    def kutuphane():
        a = ayar()
        kayitlar = []
        if a.data_dir.is_dir():
            for klasor in a.data_dir.iterdir():
                if klasor.is_dir():
                    k = kayit_ozeti(a, klasor)
                    if k:
                        kayitlar.append(k)
        return sorted(kayitlar, key=lambda k: -k["zaman"])

    @app.get("/api/item/{kimlik}")
    def kayit(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        a = ayar()
        klasor = a.data_dir / kimlik
        ozet = kayit_ozeti(a, klasor) if klasor.is_dir() else None
        if not ozet:
            raise HTTPException(404, "Kayıt bulunamadı")
        not_yolu = _not_yolu(a, kimlik, ozet["tur"])
        return {
            **ozet,
            "ozet_veri": _oku_json(klasor / "summary.json") or {},
            "iddialar": (_oku_json(klasor / "factcheck.json") or {}).get("iddialar") or [],
            "obsidian": f"obsidian://open?path={quote(str(not_yolu))}" if not_yolu else "",
            "transkript": (klasor / "transcript.json").exists(),
            "not_yolu": str(not_yolu) if not_yolu else "",
            "aciklama": str((_oku_json(klasor / "meta.json") or {}).get("aciklama") or ""),
        }

    @app.delete("/api/item/{kimlik}")
    def kayit_sil_uc(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        a = ayar()
        klasor = a.data_dir / kimlik
        if not klasor.is_dir():
            raise HTTPException(404, "Kayıt bulunamadı")
        url = ((_oku_json(klasor / "meta.json") or {}).get("url")) or ""
        if url and isler.aktif_url_var(url):
            raise HTTPException(409, "Bu kayıt şu anda işleniyor. İşlem bitince silin.")
        return {"silinen": kayit_sil(a, kimlik)}

    @app.get("/api/chat/{kimlik}")
    def sohbet_gecmisi(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        return gecmis_oku(ayar().data_dir / kimlik)

    @app.delete("/api/chat/{kimlik}")
    def sohbet_temizle(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        a = ayar()
        gecmis_sil(a.data_dir / kimlik)
        nota_yaz(a, a.data_dir / kimlik)
        return {"tamam": True}

    @app.post("/api/chat/{kimlik}")
    def sohbet_sor(kimlik: str, istek: Soru):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        soru = istek.soru.strip()
        if not soru:
            raise HTTPException(400, "Soru boş")
        a = ayar()
        klasor = a.data_dir / kimlik
        if not (klasor / "meta.json").exists():
            raise HTTPException(404, "Kayıt bulunamadı")
        return StreamingResponse(sor(a, klasor, soru), media_type="text/plain; charset=utf-8")

    @app.get("/api/vault")
    def vault_bilgi():
        a = ayar()
        yol = a.vault.expanduser().resolve()
        return {"yol": str(yol), "var": yol.is_dir(), "obsidian": f"obsidian://open?path={quote(str(yol))}"}

    @app.post("/api/reveal")
    def finder_goster(veri: dict):
        a = ayar()
        vault = a.vault.expanduser().resolve()
        hedef = Path(str(veri.get("yol") or vault)).expanduser().resolve()
        if hedef != vault and vault not in hedef.parents:
            raise HTTPException(400, "Yalnızca vault içindeki dosyalar gösterilebilir")
        if not hedef.exists():
            raise HTTPException(404, "Dosya yok")
        subprocess.run(["open", "-R", str(hedef)] if hedef.is_file() else ["open", str(hedef)], check=False)
        return {"tamam": True}

    @app.get("/api/transcript/{kimlik}")
    def transkript(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        veri = _oku_json(ayar().data_dir / kimlik / "transcript.json")
        if not isinstance(veri, dict):
            raise HTTPException(404, "Transkript yok")
        return [
            {"s": float(s.get("start") or 0), "t": str(s.get("text") or "").strip()}
            for s in veri.get("segmentler") or []
            if str(s.get("text") or "").strip()
        ]

    @app.get("/api/audio/{kimlik}")
    def ses(kimlik: str):
        if not KIMLIK_RE.match(kimlik):
            raise HTTPException(400, "Geçersiz kimlik")
        a = ayar()
        yol = _ses_yolu(a, a.data_dir / kimlik)
        if not yol:
            raise HTTPException(404, "Sesli özet yok")
        return FileResponse(yol, media_type="audio/mp4")

    @app.get("/api/status")
    def durum():
        a = ayar()
        try:
            import ollama

            adlar = [m.model for m in ollama.Client().list().models]
            hazir = any(ad == a.model or ad.split(":")[0] == a.model.split(":")[0] for ad in adlar)
            return {"ollama": True, "model": a.model, "model_kurulu": hazir}
        except Exception:  # noqa: BLE001
            return {"ollama": False, "model": a.model, "model_kurulu": False}

    @app.post("/api/jobs")
    def is_ekle(istek: IsIstegi):
        url = istek.url.strip()
        tur = tur_belirle(url)
        if tur == "desteklenmiyor":
            raise HTTPException(400, "Twitter/X, PDF ve geçersiz bağlantılar desteklenmiyor.")
        sec = {"dogrula": istek.dogrula, "ses": istek.ses and tur == "video", "bastan": istek.bastan}
        return isler.ekle(tur, url, sec)

    @app.get("/api/queue")
    def kuyruk():
        a = ayar()
        yol = a.vault / "Gelen kutusu.md"
        try:
            return {"bekleyen": bekleyen_urller(yol)}
        except RuntimeError:
            return {"bekleyen": []}

    @app.post("/api/queue/run")
    def kuyruk_calistir():
        hazirla_yol = ayar().vault
        hazirla_yol.mkdir(parents=True, exist_ok=True)
        hazirla(hazirla_yol)
        return isler.ekle("kuyruk", "Gelen kutusu", {"dogrula": True, "ses": False, "bastan": False})

    @app.get("/api/jobs")
    def is_liste():
        return isler.liste()

    @app.get("/api/jobs/{is_id}")
    def is_getir(is_id: str):
        is_ = isler.getir(is_id)
        if not is_:
            raise HTTPException(404, "İş bulunamadı")
        return is_

    @app.get("/api/settings")
    def ayarlar_oku():
        yol = ayar_yol
        ham = tomllib.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
        return {f"{b}.{k}": (ham.get(b) or {}).get(k) for (b, k) in ALANLAR}

    @app.put("/api/settings")
    def ayarlar_yaz(veri: dict):
        guncel: dict[tuple[str, str], object] = {}
        for ad, deger in veri.items():
            bolum, _, anahtar = ad.partition(".")
            tur = ALANLAR.get((bolum, anahtar))
            if tur is None:
                raise HTTPException(400, f"Bilinmeyen ayar: {ad}")
            try:
                guncel[(bolum, anahtar)] = tur(deger)
            except (TypeError, ValueError) as hata:
                raise HTTPException(400, f"{ad} geçersiz: {deger!r}") from hata
        if guncel.get(("ollama", "think"), "false") not in THINK:
            raise HTTPException(400, "think değeri geçersiz")
        if guncel.get(("ollama", "think_hukum"), "") not in THINK | {""}:
            raise HTTPException(400, "think_hukum değeri geçersiz")
        if guncel.get(("genel", "ozet_dili"), "kaynak") not in {"kaynak", "tr", "en"}:
            raise HTTPException(400, "ozet_dili kaynak, tr veya en olmalı")
        if ayar_yol.exists():
            satirlar = ayar_yol.read_text(encoding="utf-8").splitlines()
        else:
            satirlar = []
        ayar_yol.write_text(toml_guncelle(satirlar, guncel), encoding="utf-8")
        return {"tamam": True}

    @app.post("/api/context")
    def baglam():
        a = ayar()
        try:
            token = measure_context(a)
        except RuntimeError as hata:
            return JSONResponse({"hata": str(hata)}, status_code=502)
        return {"token": token, "num_ctx": a.num_ctx, "uygulandi": token > 4096}

    @app.get("/")
    def ana():
        return FileResponse(STATIC / "index.html")

    return app


def _toml_deger(deger: object) -> str:
    if isinstance(deger, bool):
        return "true" if deger else "false"
    if isinstance(deger, (int, float)):
        return repr(deger)
    return json.dumps(str(deger), ensure_ascii=False)


def toml_guncelle(satirlar: list[str], guncel: dict[tuple[str, str], object]) -> str:
    """Yorumları koruyarak yalnızca verilen anahtarların değerini değiştirir."""
    cikti: list[str] = []
    bolum = ""
    yazilan: set[tuple[str, str]] = set()
    bolum_sonlari: dict[str, int] = {}
    for satir in satirlar:
        b = re.match(r"^\s*\[([^\]]+)\]\s*$", satir)
        if b:
            bolum = b.group(1)
            bolum_sonlari[bolum] = len(cikti)
        else:
            k = re.match(r"^(\s*)([A-Za-z0-9_]+)\s*=", satir)
            if k and (bolum, k.group(2)) in guncel:
                yazilan.add((bolum, k.group(2)))
                satir = f"{k.group(1)}{k.group(2)} = {_toml_deger(guncel[(bolum, k.group(2))])}"
        cikti.append(satir)
        if bolum and satir.strip():
            bolum_sonlari[bolum] = len(cikti)
    for (b, k), deger in guncel.items():
        if (b, k) in yazilan:
            continue
        yeni = f"{k} = {_toml_deger(deger)}"
        if b in bolum_sonlari:
            cikti.insert(bolum_sonlari[b], yeni)
            bolum_sonlari = {x: (n + 1 if n >= bolum_sonlari[b] else n) for x, n in bolum_sonlari.items()}
        else:
            cikti += ["", f"[{b}]", yeni]
            bolum_sonlari[b] = len(cikti)
    return "\n".join(cikti) + "\n"
