"""Bir kayıt hakkında soru-cevap: transkript veya yazı metni üzerinden yerel modelle sohbet."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.llm import _kaydi_kapat, _langfuse, think_degeri
from videotoinfo.textutil import think_temizle, zaman_linki

DOSYA = "sohbet.json"
NOT_BAS = "<!-- vti:sohbet:baslangic -->"
NOT_SON = "<!-- vti:sohbet:bitis -->"
_ZAMAN = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\](?!\()")
GECMIS_MESAJ = 8
PARCA_KARAKTER = 1200
KARAKTER_TOKEN = 2.5  # Türkçe metinde temkinli tahmin

SISTEM = (
    "Sen bir videonun ya da yazının içeriği hakkında soruları yanıtlayan Türkçe bir asistansın. "
    "Yanıtlarını öncelikle aşağıda verilen kaynak metne dayandır. Kaynakta olmayan bir şeyi "
    "kaynakta varmış gibi söyleme; genel bilgi ekliyorsan bunu açıkça belirt. "
    "Videolarda ilgili yerin zamanını [dk:sn] biçiminde gösterebilirsin. "
    "Kısa, net ve Türkçe yanıt ver."
)

_KELIME = re.compile(r"\w{3,}", re.UNICODE)


def _oku(yol: Path):
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _zaman(saniye: float) -> str:
    s = int(saniye)
    return f"{s // 60}:{s % 60:02d}"


def kaynak_parcalari(klasor: Path) -> list[str]:
    """Transkripti (zaman damgalı) ya da yazı metnini yaklaşık eşit parçalara böler."""
    parcalar: list[str] = []
    trans = _oku(klasor / "transcript.json")
    if isinstance(trans, dict) and trans.get("segmentler"):
        tampon, bas = "", None
        for seg in trans["segmentler"]:
            metin = str(seg.get("text") or "").strip()
            if not metin:
                continue
            if bas is None:
                bas = float(seg.get("start") or 0)
            tampon += " " + metin
            if len(tampon) >= PARCA_KARAKTER:
                parcalar.append(f"[{_zaman(bas)}]{tampon}")
                tampon, bas = "", None
        if tampon:
            parcalar.append(f"[{_zaman(bas or 0)}]{tampon}")
        return parcalar
    yazi = _oku(klasor / "metin.json")
    if isinstance(yazi, dict):
        for bolum in yazi.get("bolumler") or []:
            baslik = str(bolum.get("baslik") or "").strip()
            metin = str(bolum.get("metin") or "").strip()
            for i in range(0, len(metin), PARCA_KARAKTER):
                on = f"## {baslik}\n" if baslik and i == 0 else ""
                parcalar.append(on + metin[i : i + PARCA_KARAKTER])
    return parcalar


def _kelimeler(metin: str) -> set[str]:
    return {k.lower() for k in _KELIME.findall(metin)}


def parca_sec(parcalar: list[str], sorgu: str, butce: int) -> list[str]:
    """Hepsi sığarsa hepsini, sığmazsa soruyla en çok kelime paylaşanları özgün sırasıyla döndürür."""
    if sum(len(p) for p in parcalar) <= butce:
        return parcalar
    hedef = _kelimeler(sorgu)
    puanli = sorted(
        range(len(parcalar)),
        key=lambda i: (-len(hedef & _kelimeler(parcalar[i])), i),
    )
    secilen: list[int] = []
    toplam = 0
    for i in puanli:
        if toplam + len(parcalar[i]) > butce:
            continue
        secilen.append(i)
        toplam += len(parcalar[i])
    return [parcalar[i] for i in sorted(secilen)]


def baglam_metni(klasor: Path, sorgu: str, butce: int) -> str:
    meta = _oku(klasor / "meta.json") or {}
    ozet = _oku(klasor / "summary.json") or {}
    satirlar = [f"Başlık: {meta.get('baslik') or 'Başlıksız'}"]
    kaynak = meta.get("kanal") or meta.get("yazar") or meta.get("site")
    if kaynak:
        satirlar.append(f"Kaynak: {kaynak}")
    if ozet.get("kisa"):
        satirlar.append("\nKısa özet:\n" + str(ozet["kisa"]).strip())
    bolumler = ozet.get("bolumler") or []
    if bolumler:
        satirlar.append("\nBölüm özetleri:")
        for b in bolumler:
            on = f"[{_zaman(b['baslangic'])}] " if "baslangic" in b else (f"{b.get('baslik')}: " if b.get("baslik") else "")
            satirlar.append(f"- {on}{b.get('ozet', '')}")
    bas = "\n".join(satirlar)
    parcalar = kaynak_parcalari(klasor)
    secilen = parca_sec(parcalar, sorgu, max(butce - len(bas), 2000))
    if secilen:
        not_ = "" if len(secilen) == len(parcalar) else " (yalnızca soruyla ilgili görünen kısımlar)"
        bas += f"\n\nKaynak metin{not_}:\n" + "\n\n".join(secilen)
    return bas


def gecmis_oku(klasor: Path) -> list[dict]:
    veri = _oku(klasor / DOSYA)
    return veri if isinstance(veri, list) else []


def gecmis_yaz(klasor: Path, mesajlar: list[dict]) -> None:
    (klasor / DOSYA).write_text(json.dumps(mesajlar, ensure_ascii=False, indent=2), encoding="utf-8")


def gecmis_sil(klasor: Path) -> None:
    (klasor / DOSYA).unlink(missing_ok=True)


def _zamanlari_bagla(metin: str, meta: dict) -> str:
    url, platform = meta.get("url") or "", meta.get("platform") or ""
    if platform != "youtube" or not url:
        return metin

    def bagla(m: re.Match) -> str:
        saniye = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        return f"[{m.group(0)[1:-1]}]({zaman_linki(url, saniye, platform)})"

    return _ZAMAN.sub(bagla, metin)


def sohbet_md(gecmis: list[dict], meta: dict) -> str:
    parcalar = ["## Soru-cevap", "_Web arayüzündeki soru-cevaplardan otomatik yazılır; bu bölümdeki elle düzenlemeler sonraki soruda silinir._"]
    for m in gecmis:
        if m.get("rol") == "user":
            zaman = time.strftime("%d.%m.%Y %H:%M", time.localtime(m.get("zaman") or 0))
            soru = "\n".join(f"> {s}".rstrip() for s in str(m.get("icerik", "")).strip().splitlines())
            parcalar.append(f"> [!question] Soru · {zaman}\n{soru}")
        else:
            parcalar.append(_zamanlari_bagla(str(m.get("icerik", "")).strip(), meta))
    return "\n\n".join(parcalar)


def not_bul(settings: Settings, klasor: Path) -> Path | None:
    from videotoinfo.vault import find_note

    meta = _oku(klasor / "meta.json") or {}
    if meta.get("video_id"):
        return find_note(settings.vault, "Videolar", "video_id", meta["video_id"])
    if meta.get("yazi_id"):
        return find_note(settings.vault, "Yazilar", "yazi_id", meta["yazi_id"])
    return None


def nota_yaz(settings: Settings, klasor: Path) -> Path | None:
    """Sohbet geçmişini Obsidian notundaki Soru-cevap bölümüne yansıtır; geçmiş boşsa bölümü kaldırır."""
    yol = not_bul(settings, klasor)
    if yol is None:
        return None
    metin = yol.read_text(encoding="utf-8")
    if NOT_BAS in metin and NOT_SON in metin:
        on, _, kalan = metin.partition(NOT_BAS)
        _, _, geri = kalan.partition(NOT_SON)
        metin = on.rstrip() + "\n" + (("\n" + geri.strip() + "\n") if geri.strip() else "")
    gecmis = gecmis_oku(klasor)
    if gecmis:
        blok = f"{NOT_BAS}\n{sohbet_md(gecmis, _oku(klasor / 'meta.json') or {})}\n{NOT_SON}\n"
        metin = metin.rstrip() + "\n\n" + blok
    yol.write_text(metin, encoding="utf-8")
    return yol


def mesajlari_kur(settings: Settings, klasor: Path, soru: str, gecmis: list[dict]) -> list[dict]:
    son = gecmis[-GECMIS_MESAJ:]
    gecmis_karakter = sum(len(m.get("icerik", "")) for m in son)
    token_butce = settings.num_ctx - settings.num_predict - 800
    butce = int(token_butce * KARAKTER_TOKEN) - gecmis_karakter - len(soru)
    sorgu = " ".join([m["icerik"] for m in son if m.get("rol") == "user"][-2:] + [soru])
    mesajlar = [{"role": "system", "content": SISTEM + "\n\n" + baglam_metni(klasor, sorgu, butce)}]
    for m in son:
        mesajlar.append({"role": "assistant" if m.get("rol") == "assistant" else "user", "content": m.get("icerik", "")})
    mesajlar.append({"role": "user", "content": soru})
    return mesajlar


def _sohbet_kaydi(settings: Settings, kimlik: str, mesajlar: list[dict]) -> object | None:
    """Kayıt yield'den önce açılır. propagate_attributes hemen kapanır; bağlam akışa taşınmaz."""
    istemci = _langfuse()
    if istemci is None:
        return None
    try:
        from langfuse import propagate_attributes

        with propagate_attributes(session_id=kimlik, trace_name="sohbet"):
            return istemci.start_observation(
                as_type="generation",
                name="sohbet",
                model=settings.model,
                input=mesajlar,
                model_parameters={
                    "num_ctx": settings.num_ctx,
                    "temperature": settings.temperature,
                    "num_predict": settings.num_predict,
                    "think": think_degeri(settings.think),
                },
            )
    except Exception:
        return None


def sor(settings: Settings, klasor: Path, soru: str) -> Iterator[str]:
    """Yanıtı parça parça üretir; bitince soru ve yanıtı sohbet geçmişine ekler."""
    import ollama

    settings = settings.gorev("sohbet")
    gecmis = gecmis_oku(klasor)
    mesajlar = mesajlari_kur(settings, klasor, soru, gecmis)
    ortak = {
        "model": settings.model,
        "messages": mesajlar,
        "options": {"num_ctx": settings.num_ctx, "temperature": settings.temperature, "num_predict": settings.num_predict},
        "keep_alive": settings.keep_alive,
        "stream": True,
    }
    gen = _sohbet_kaydi(settings, klasor.name, mesajlar)
    yanit = ""
    seviye = None
    durum = None
    ilk = None
    son = None
    try:
        try:
            akis = ollama.chat(**ortak, think=think_degeri(settings.think))
        except TypeError:
            akis = ollama.chat(**ortak)
        for parca in akis:
            metin = parca.message.content or ""
            if metin:
                if ilk is None:
                    ilk = datetime.now(timezone.utc)
                yanit += metin
                yield metin
            if getattr(parca, "done", None):
                son = parca
    except GeneratorExit:
        seviye, durum = "WARNING", "iptal"
        raise
    except Exception as hata:  # noqa: BLE001 - kullanıcıya akış içinde gösterilir
        seviye, durum = "ERROR", str(hata)
        yield f"\n\n[Hata: {hata}]"
        return
    else:
        yanit = think_temizle(yanit)
        if yanit:
            zaman = time.time()
            gecmis += [
                {"rol": "user", "icerik": soru, "zaman": zaman},
                {"rol": "assistant", "icerik": yanit, "zaman": zaman},
            ]
            gecmis_yaz(klasor, gecmis)
            try:
                nota_yaz(settings, klasor)
            except OSError:
                pass
    finally:
        _kaydi_kapat(
            gen,
            yanit=son,
            cikti=think_temizle(yanit) or None,
            seviye=seviye,
            durum=durum,
            ilk_token=ilk,
        )
