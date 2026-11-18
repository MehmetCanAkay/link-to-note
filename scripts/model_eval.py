"""Dil modellerini proje görevlerinde karşılaştırır (seslendirme hariç).

Kullanım:
    PYTHONPATH=src uv run --no-sync python scripts/model_eval.py qwen3.8:27b-mlx gemma4:26b-mlx [--cikti klasor]

Vault'a ve data/ altındaki kayıtlara yazmaz. Her model için:
  - üretim hızı (token/sn) ve istem işleme hızı
  - yazı özeti (İngilizce kaynak -> İngilizce ve Türkçe), video özeti, Türkçe yazı özeti
  - iddia çıkarma
  - hüküm: tüm modeller AYNI iddiaları AYNI kaynaklarla değerlendirir (kaynaklar bir kez toplanır)
Sonuçlar <cikti>/sonuc.json ve <cikti>/rapor.md dosyalarına yazılır.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from videotoinfo.config import load_settings
from videotoinfo.factcheck import Haric, Iddia, _kanit_topla, hukum_ver, iddialari_cikar
from videotoinfo.llm import unload_model
from videotoinfo.models import Transcript, VideoMeta, YaziBolum, YaziMeta
from videotoinfo.search import SearchResult, make_searcher
from videotoinfo.summarize import detayli_metin, ozetle, yazi_detay_metni, yazi_ozetle
from videotoinfo.textutil import dilde_gorunuyor
from videotoinfo.yazi import _sayfa_oku, metni_bol

KOK = Path(__file__).resolve().parent.parent
YAZI_EN = KOK / "data" / "85c42207c05c"
VIDEO_EN = KOK / "data" / "youtube_Ib5GBkD555M"
YAZI_TR_URL = "https://tr.wikipedia.org/wiki/Apollo_11"
# İngilizce yazıdaki 8 terim: kısa özetin kapsamını ölçmek için.
TERIMLER = ["harness", "agent", "mcp", "loop", "skill", "geo", "computer", "agi"]


def zaman(fn, *a, **k):
    bas = time.time()
    sonuc = fn(*a, **k)
    return sonuc, round(time.time() - bas, 1)


def hiz_olc(model: str) -> dict:
    import ollama

    ollama.chat(model=model, messages=[{"role": "user", "content": "Merhaba"}], options={"num_predict": 5}, think=False)
    istem = "Explain in detail how a transistor works. " + ("Context sentence about electronics. " * 300)
    y = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": istem}],
        options={"num_predict": 300, "num_ctx": 16384, "temperature": 0},
        think=False,
    )
    return {
        "uretim_tok_sn": round(y.eval_count / (y.eval_duration / 1e9), 1),
        "istem_tok_sn": round(y.prompt_eval_count / (y.prompt_eval_duration / 1e9), 1),
    }


def yazi_tr_hazirla(klasor: Path) -> tuple[YaziMeta, list[YaziBolum]]:
    yol = klasor / "yazi_tr.json"
    if yol.exists():
        veri = json.loads(yol.read_text(encoding="utf-8"))
        return YaziMeta.from_dict(veri["meta"]), [YaziBolum.from_dict(b) for b in veri["bolumler"]]
    sayfa = _sayfa_oku(YAZI_TR_URL)
    govde = sayfa.govde[:14000]
    meta = YaziMeta("eval_tr", YAZI_TR_URL, sayfa.baslik, sayfa.yazar, sayfa.site, sayfa.tarih, len(govde.split()), False, dil="tr")
    bolumler = metni_bol(govde, 6000, sayfa.baslik)
    yol.write_text(
        json.dumps({"meta": meta.as_dict(), "bolumler": [b.as_dict() for b in bolumler]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta, bolumler


def yazi_yukle(klasor: Path) -> tuple[YaziMeta, list[YaziBolum]]:
    meta = YaziMeta.from_dict(json.loads((klasor / "meta.json").read_text(encoding="utf-8")))
    bolumler = [YaziBolum.from_dict(b) for b in json.loads((klasor / "metin.json").read_text(encoding="utf-8"))["bolumler"]]
    return meta, bolumler


def ozet_kaydi(ozet, sure: float, dil: str, detay: str) -> dict:
    bos = sum(1 for b in ozet.bolumler if not b.ozet.strip())
    return {
        "sure_sn": sure,
        "dil_dogru": dilde_gorunuyor(ozet.kisa, dil) and dilde_gorunuyor(detay, dil),
        "bos_bolum": bos,
        "kisa_madde": ozet.kisa.count("\n- ") + (1 if ozet.kisa.startswith("- ") else 0),
        "detay_karakter": len(detay),
        "konular": ozet.konular,
        "kisiler": ozet.kisiler,
        "etiketler": ozet.etiketler,
        "kisa": ozet.kisa,
        "detay": detay,
    }


def kanit_hazirla(klasor: Path, iddia_setleri: dict[str, list[Iddia]], ayar, dil: dict[str, str]) -> dict:
    """Her iddia için arama ve sayfa okuma bir kez yapılır, diske yazılır."""
    yol = klasor / "kanit.json"
    if yol.exists():
        return json.loads(yol.read_text(encoding="utf-8"))
    kanit: dict = {}
    for ad, iddialar in iddia_setleri.items():
        bolge = replace(ayar, search_region="us-en" if dil[ad] == "en" else ayar.search_region)
        arama = make_searcher(bolge)
        haric = Haric(baslik="")
        kanit[ad] = []
        for iddia in iddialar:
            bulunan, okunan = _kanit_topla(iddia, ayar, arama, None, haric)
            kanit[ad].append(
                {
                    "iddia": asdict(iddia),
                    "bulunan": [asdict(s) for s in bulunan],
                    "okunan": [[asdict(s), m] for s, m in okunan],
                }
            )
    bos = sum(1 for liste in kanit.values() for k in liste if not k["bulunan"])
    toplam = sum(len(liste) for liste in kanit.values())
    print(f"Kanıt: {toplam - bos}/{toplam} iddia için kaynak bulundu", flush=True)
    if bos > toplam // 3:
        raise SystemExit("Çok fazla boş arama (ağ sorunu?). Kanıt kaydedilmedi; daha sonra tekrar çalıştırın.")
    yol.write_text(json.dumps(kanit, ensure_ascii=False, indent=2), encoding="utf-8")
    return kanit


def calistir(model: str, ayar, klasor: Path, iddia_setleri: dict | None, kanit: dict | None) -> dict:
    print(f"\n=== {model} ===", flush=True)
    m = replace(ayar, model=model, model_ozet="", model_iddia="", model_hukum="", model_sohbet="", think_hukum="")
    sonuc: dict = {"model": model}
    sonuc["hiz"] = hiz_olc(model)
    print("hız:", sonuc["hiz"], flush=True)

    meta_en, bol_en = yazi_yukle(YAZI_EN)
    meta_tr, bol_tr = yazi_tr_hazirla(klasor)
    vmeta = VideoMeta.from_dict(json.loads((VIDEO_EN / "meta.json").read_text(encoding="utf-8")))
    transcript = Transcript.from_dict(json.loads((VIDEO_EN / "transcript.json").read_text(encoding="utf-8")))

    gorevler = {}
    oz, s = zaman(yazi_ozetle, meta_en, bol_en, replace(m, summary_lang="en"))
    gorevler["yazi_en_en"] = ozet_kaydi(oz, s, "en", yazi_detay_metni(oz))
    kisa = oz.kisa.lower()
    gorevler["yazi_en_en"]["terim_kapsami"] = f"{sum(t in kisa for t in TERIMLER)}/{len(TERIMLER)}"
    print("yazı en->en", s, "sn", flush=True)

    oz, s = zaman(yazi_ozetle, meta_en, bol_en, replace(m, summary_lang="tr"))
    gorevler["yazi_en_tr"] = ozet_kaydi(oz, s, "tr", yazi_detay_metni(oz))
    kisa = oz.kisa.lower()
    gorevler["yazi_en_tr"]["terim_kapsami"] = f"{sum(t in kisa for t in TERIMLER)}/{len(TERIMLER)}"
    print("yazı en->tr", s, "sn", flush=True)

    oz, s = zaman(yazi_ozetle, meta_tr, bol_tr, replace(m, summary_lang="kaynak"))
    gorevler["yazi_tr_tr"] = ozet_kaydi(oz, s, "tr", yazi_detay_metni(oz))
    print("yazı tr->tr", s, "sn", flush=True)

    oz, s = zaman(ozetle, transcript, vmeta, replace(m, summary_lang="kaynak"))
    gorevler["video_en_en"] = ozet_kaydi(oz, s, "en", detayli_metin(oz, vmeta))
    print("video en->en", s, "sn", flush=True)
    sonuc["ozet"] = gorevler

    cikan = {}
    for ad, parcalar, dil in (
        ("yazi_en", [b.metin for b in bol_en], "en"),
        ("yazi_tr", [b.metin for b in bol_tr], "tr"),
        ("video_en", [transcript.metin], "en"),
    ):
        liste, s = zaman(iddialari_cikar, parcalar, m, dil)
        cikan[ad] = {"sure_sn": s, "iddialar": [asdict(i) for i in liste]}
        print(f"iddia {ad}: {len(liste)} adet, {s} sn", flush=True)
    sonuc["iddia"] = cikan

    if kanit is not None:
        hukumler = {}
        for ad, liste in kanit.items():
            cikti = "tr" if ad == "yazi_tr" else "en"
            satirlar = []
            bas = time.time()
            for k in liste:
                iddia = Iddia(**k["iddia"])
                bulunan = [SearchResult(**b) for b in k["bulunan"]]
                okunan = [(SearchResult(**s), metin) for s, metin in k["okunan"]]
                r = hukum_ver(iddia, bulunan, okunan, m, cikti=cikti)
                satirlar.append({"iddia": r.iddia, "hukum": r.hukum, "gerekce": r.gerekce, "duzeltme": r.duzeltme})
            hukumler[ad] = {"sure_sn": round(time.time() - bas, 1), "sonuclar": satirlar}
            print(f"hüküm {ad}: {hukumler[ad]['sure_sn']} sn", flush=True)
        sonuc["hukum"] = hukumler
    unload_model(model)
    return sonuc


def rapor(sonuclar: list[dict]) -> str:
    satir = ["# Model karşılaştırması", ""]
    satir += ["| | " + " | ".join(s["model"] for s in sonuclar) + " |", "|---|" + "---|" * len(sonuclar)]

    def ekle(ad, f):
        satir.append(f"| {ad} | " + " | ".join(str(f(s)) for s in sonuclar) + " |")

    ekle("Üretim hızı (token/sn)", lambda s: s["hiz"]["uretim_tok_sn"])
    ekle("İstem işleme (token/sn)", lambda s: s["hiz"]["istem_tok_sn"])
    for g in ("yazi_en_en", "yazi_en_tr", "yazi_tr_tr", "video_en_en"):
        ekle(f"{g} süre (sn)", lambda s, g=g: s["ozet"][g]["sure_sn"])
        ekle(f"{g} dil doğru / boş bölüm", lambda s, g=g: f'{s["ozet"][g]["dil_dogru"]} / {s["ozet"][g]["bos_bolum"]}')
    ekle("yazi_en_en 8 terim kapsamı", lambda s: s["ozet"]["yazi_en_en"]["terim_kapsami"])
    ekle("yazi_en_tr 8 terim kapsamı", lambda s: s["ozet"]["yazi_en_tr"]["terim_kapsami"])
    for ad in ("yazi_en", "yazi_tr", "video_en"):
        ekle(f"iddia {ad} (adet, sn)", lambda s, ad=ad: f'{len(s["iddia"][ad]["iddialar"])}, {s["iddia"][ad]["sure_sn"]}')
    if all("hukum" in s for s in sonuclar):
        for ad in sonuclar[0]["hukum"]:
            ekle(f"hüküm {ad} süre (sn)", lambda s, ad=ad: s["hukum"][ad]["sure_sn"])
        ilk = sonuclar[0]["hukum"]
        for diger in sonuclar[1:]:
            toplam = ayni = 0
            for ad in ilk:
                for a, b in zip(ilk[ad]["sonuclar"], diger["hukum"][ad]["sonuclar"]):
                    toplam += 1
                    ayni += a["hukum"] == b["hukum"]
            satir.append(f"\nHüküm uyumu ({sonuclar[0]['model']} vs {diger['model']}): {ayni}/{toplam}")
    for s in sonuclar:
        satir += ["", f"## {s['model']}"]
        for g, v in s["ozet"].items():
            satir += ["", f"### {g} · kısa özet", "", v["kisa"], "", f"Konular: {v['konular']} · Kişiler: {v['kisiler']}"]
            satir += ["", f"<details><summary>{g} · detay</summary>", "", v["detay"], "", "</details>"]
        for ad, v in s["iddia"].items():
            satir += ["", f"### İddialar · {ad}", ""] + [f"- {i['metin']}  `{i['sorgu']}`" for i in v["iddialar"]]
        if "hukum" in s:
            for ad, v in s["hukum"].items():
                satir += ["", f"### Hükümler · {ad}", ""] + [
                    f"- **{r['hukum']}** · {r['iddia']} — {r['gerekce']}" for r in v["sonuclar"]
                ]
    return "\n".join(satir) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modeller", nargs="+")
    ap.add_argument("--cikti", default=str(KOK / "eval"))
    args = ap.parse_args()
    klasor = Path(args.cikti)
    klasor.mkdir(parents=True, exist_ok=True)
    ayar = load_settings(KOK / "config.toml")
    for model in args.modeller:
        unload_model(model)

    def onbellek(model: str) -> Path:
        return klasor / (model.replace(":", "_").replace("/", "_") + ".json")

    sonuclar = []
    # İlk model hem kendi iddialarını çıkarır hem de ortak hüküm setini belirler.
    if onbellek(args.modeller[0]).exists():
        ilk = json.loads(onbellek(args.modeller[0]).read_text(encoding="utf-8"))
        print(f"{args.modeller[0]} önbellekten", flush=True)
        sonuclar.append(ilk)
        yol = klasor / "kanit.json"
        kanit = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else None
    else:
        ilk = calistir(args.modeller[0], ayar, klasor, None, None)
        sonuclar.append(ilk)
        kanit = None
    if kanit is None:
        setler = {ad: [Iddia(**i) for i in v["iddialar"]] for ad, v in ilk["iddia"].items()}
        print("\nKaynaklar toplanıyor (bir kez)", flush=True)
        kanit = kanit_hazirla(klasor, setler, ayar, {"yazi_en": "en", "yazi_tr": "tr", "video_en": "en"})
    m0 = replace(ayar, model=args.modeller[0])
    hukumler = {} if "hukum" not in ilk else None
    for ad, liste in (kanit.items() if hukumler is not None else []):
        cikti = "tr" if ad == "yazi_tr" else "en"
        bas = time.time()
        satirlar = []
        for k in liste:
            r = hukum_ver(
                Iddia(**k["iddia"]),
                [SearchResult(**b) for b in k["bulunan"]],
                [(SearchResult(**s), metin) for s, metin in k["okunan"]],
                m0,
                cikti=cikti,
            )
            satirlar.append({"iddia": r.iddia, "hukum": r.hukum, "gerekce": r.gerekce, "duzeltme": r.duzeltme})
        hukumler[ad] = {"sure_sn": round(time.time() - bas, 1), "sonuclar": satirlar}
        print(f"hüküm {ad}: {hukumler[ad]['sure_sn']} sn", flush=True)
    if hukumler is not None:
        ilk["hukum"] = hukumler
        unload_model(args.modeller[0])
        onbellek(args.modeller[0]).write_text(json.dumps(ilk, ensure_ascii=False, indent=2), encoding="utf-8")
    for model in args.modeller[1:]:
        if onbellek(model).exists():
            sonuclar.append(json.loads(onbellek(model).read_text(encoding="utf-8")))
            continue
        sonuc = calistir(model, ayar, klasor, None, kanit)
        onbellek(model).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
        sonuclar.append(sonuc)

    (klasor / "sonuc.json").write_text(json.dumps(sonuclar, ensure_ascii=False, indent=2), encoding="utf-8")
    (klasor / "rapor.md").write_text(rapor(sonuclar), encoding="utf-8")
    print(f"\nRapor: {klasor / 'rapor.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
