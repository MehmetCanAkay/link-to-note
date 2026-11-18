"""Bölüm bölüm Türkçe özet."""

from __future__ import annotations

import json
import re
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.llm import SYSTEM, BaglamAsildi, chat, ensure_model
from videotoinfo.models import (
    SectionSummary,
    Segment,
    Summary,
    Transcript,
    VideoMeta,
    YaziBolum,
    YaziBolumOzet,
    YaziMeta,
    YaziOge,
    YaziOzet,
)
from videotoinfo.textutil import (
    DIL_ADI,
    anlatici_temizle,
    cikti_dili,
    dil_kodu,
    dil_tespit,
    dilde_gorunuyor,
    doldur,
    etiket_temizle,
    json_cikar,
    konu_adi,
    madde_metni,
    saniye_yaz,
    zaman_linki,
)

DOSYA = "summary.json"
ISTEM_KOK = Path(__file__).parent / "prompts"


def istem(ad: str) -> str:
    return (ISTEM_KOK / ad).read_text(encoding="utf-8")


def ozet_yukle(klasor: Path) -> Summary | None:
    yol = klasor / DOSYA
    if not yol.exists():
        return None
    return Summary.from_dict(json.loads(yol.read_text(encoding="utf-8")))


def ozet_kaydet(klasor: Path, ozet: Summary) -> None:
    (klasor / DOSYA).write_text(
        json.dumps(ozet.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def bolumlere_ayir(segmentler: list[Segment], saniye: int) -> list[list[Segment]]:
    dolu = [seg for seg in segmentler if seg.text.strip()]
    if not dolu:
        return []
    if saniye <= 0:
        return [dolu]
    bolumler: list[list[Segment]] = []
    kova: list[Segment] = []
    baslangic = dolu[0].start
    for seg in dolu:
        if kova and seg.start - baslangic >= saniye:
            bolumler.append(kova)
            kova = []
            baslangic = seg.start
        kova.append(seg)
    if kova:
        bolumler.append(kova)
    return bolumler


def _bolum_metni(segmentler: list[Segment]) -> str:
    return "\n".join(f"[{saniye_yaz(seg.start)}] {seg.text}" for seg in segmentler)


def _sigdir(metin: str, settings: Settings) -> list[str]:
    """Yaklaşık token sınırı aşılırsa metni ikiye böler."""
    sinir = max(2000, int(settings.num_ctx * 2.2))
    if len(metin) <= sinir:
        return [metin]
    orta = len(metin) // 2
    kirilma = metin.rfind("\n", 0, orta)
    if kirilma < sinir // 4:
        kirilma = orta
    return [metin[:kirilma].strip(), metin[kirilma:].strip()]


def _sohbet(settings: Settings, kullanici: str, temperature: float, *, iz: str = "chat", **ek) -> tuple[str, int]:
    try:
        return chat(settings, kullanici, sistem=SYSTEM, temperature=temperature, iz=iz, **ek)
    except BaglamAsildi:
        raise
    except RuntimeError:
        return chat(
            settings,
            kullanici + "\n\nYanıtı istenen dilde ve biçimde, düşünce yazmadan ver.",
            sistem=SYSTEM,
            temperature=temperature,
            iz=f"{iz}-tekrar",
            **ek,
        )


def _cevir(settings: Settings, metin: str, dil: str, madde: bool = False) -> tuple[str, int]:
    ek = " Madde biçimini koru." if madde else ""
    return _sohbet(
        settings,
        f"Şu metni doğal {DIL_ADI[dil]} diline çevir.{ek} Bilgi ekleme, yalnızca çeviriyi yaz.\n\n" + metin,
        0.2,
        iz="ceviri",
    )


def _bolum_ozetle(
    settings: Settings,
    meta: VideoMeta,
    segmentler: list[Segment],
    dil: str = "tr",
) -> tuple[str, int]:
    parcalar = _sigdir(_bolum_metni(segmentler), settings)
    ozetler: list[str] = []
    token = 0
    for parca in parcalar:
        if not parca:
            continue
        istem_metni = doldur(
            istem("bolum.txt"),
            baslik=meta.baslik,
            kanal=meta.kanal or "bilinmiyor",
            baslangic=saniye_yaz(segmentler[0].start),
            bitis=saniye_yaz(segmentler[-1].end),
            metin=parca,
            dil=DIL_ADI[dil],
        )
        try:
            metin, sayi = _sohbet(settings, istem_metni, settings.temperature, iz="bolum-ozet")
        except BaglamAsildi:
            daha = _sigdir(parca, Settings(**{**settings.__dict__, "num_ctx": max(4096, settings.num_ctx // 2)}))
            if len(daha) == 1:
                raise
            alt = []
            for alt_parca in daha:
                alt_istem = doldur(
                    istem("bolum.txt"),
                    baslik=meta.baslik,
                    kanal=meta.kanal or "bilinmiyor",
                    baslangic=saniye_yaz(segmentler[0].start),
                    bitis=saniye_yaz(segmentler[-1].end),
                    metin=alt_parca,
                    dil=DIL_ADI[dil],
                )
                alt_metin, sayi = _sohbet(settings, alt_istem, settings.temperature, iz="bolum-alt")
                token = max(token, sayi)
                alt.append(alt_metin)
            metin = "\n\n".join(alt)
            sayi = token
        token = max(token, sayi)
        ozetler.append(metin.strip())
    birlesik = "\n\n".join(ozetler).strip()
    if birlesik and not dilde_gorunuyor(birlesik, dil):
        metin, sayi = _cevir(settings, birlesik, dil)
        token = max(token, sayi)
        if dilde_gorunuyor(metin, dil):
            birlesik = metin.strip()
    return birlesik, token


def _kisa_ozet(
    settings: Settings,
    baslik: str,
    kanal: str,
    bolum_metni: str,
    *,
    sablon: str = "kisa.txt",
    dil: str = "tr",
) -> tuple[str, int]:
    istem_metni = doldur(
        istem(sablon),
        baslik=baslik,
        kanal=kanal,
        yazar=kanal,
        bolumler=bolum_metni,
        dil=DIL_ADI[dil],
    )
    metin, token = _sohbet(settings, istem_metni, settings.temperature, iz="kisa-ozet")
    kisa = madde_metni(metin)
    if not dilde_gorunuyor(kisa, dil):
        metin, sayi = _cevir(settings, kisa, dil, madde=True)
        token = max(token, sayi)
        if dilde_gorunuyor(metin, dil):
            kisa = madde_metni(metin)
    return kisa, token


def _listeden(ham: object, sinir: int, temizleyici) -> list[str]:
    if not isinstance(ham, list):
        return []
    sonuc: list[str] = []
    for oge in ham:
        if not isinstance(oge, str):
            continue
        ad = temizleyici(oge)
        if ad and ad not in sonuc:
            sonuc.append(ad)
        if len(sonuc) >= sinir:
            break
    return sonuc


def _konulari_cikar(
    settings: Settings,
    baslik: str,
    kanal: str,
    kisa: str,
    detay: str,
) -> tuple[list[str], list[str], list[str]]:
    ornek = (kisa + "\n\n" + detay)[:8000]
    istem_metni = doldur(
        istem("konular.txt"),
        baslik=baslik,
        kanal=kanal,
        ozet=ornek,
    )
    try:
        metin, _ = _sohbet(settings, istem_metni, 0.1, iz="konular")
        veri = json_cikar(metin)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return [], [], []
    return (
        _listeden(veri.get("konular"), 5, konu_adi),
        _listeden(veri.get("kisiler"), 5, konu_adi),
        _listeden(veri.get("etiketler"), 5, etiket_temizle),
    )


def detayli_metin(ozet: Summary, meta: VideoMeta) -> str:
    parcalar: list[str] = []
    for bolum in ozet.bolumler:
        bas = saniye_yaz(bolum.baslangic)
        bit = saniye_yaz(bolum.bitis)
        link = zaman_linki(meta.url, bolum.baslangic, meta.platform)
        if link:
            baslik = f"### [{bas}]({link}) – {bit}"
        else:
            baslik = f"### {bas} – {bit}"
        parcalar.append(f"{baslik}\n\n{bolum.ozet.strip()}")
    return "\n\n".join(parcalar)


def ozetle(
    transcript: Transcript,
    meta: VideoMeta,
    settings: Settings,
    *,
    ilerleme=None,
) -> Summary:
    settings = settings.gorev("ozet")
    ensure_model(settings)
    kaynak = dil_kodu(transcript.dil)
    if kaynak == "diger":
        kaynak = dil_tespit(transcript.metin)
    dil = cikti_dili(settings.summary_lang, kaynak)
    gruplar = bolumlere_ayir(transcript.segmentler, settings.section_seconds)
    if not gruplar:
        raise RuntimeError("Özetlenecek transkript yok.")
    bolumler: list[SectionSummary] = []
    en_buyuk = 0
    toplam = len(gruplar)
    for sira, grup in enumerate(gruplar, start=1):
        if ilerleme:
            ilerleme(f"Özetleniyor: bölüm {sira}/{toplam}")
        metin, token = _bolum_ozetle(settings, meta, grup, dil)
        en_buyuk = max(en_buyuk, token)
        bolumler.append(SectionSummary(baslangic=grup[0].start, bitis=grup[-1].end, ozet=metin))
    if ilerleme:
        ilerleme("Kısa özet birleştiriliyor")
    govde = "\n\n".join(
        f"[{saniye_yaz(b.baslangic)} – {saniye_yaz(b.bitis)}]\n{b.ozet}" for b in bolumler
    )
    kisa, token = _kisa_ozet(settings, meta.baslik, meta.kanal or "bilinmiyor", govde, dil=dil)
    en_buyuk = max(en_buyuk, token)
    taslak = Summary(kisa=kisa, bolumler=bolumler, baglam_token=en_buyuk, num_ctx=settings.num_ctx, dil=dil)
    if ilerleme:
        ilerleme("Konular çıkarılıyor")
    konular, kisiler, etiketler = _konulari_cikar(
        settings,
        meta.baslik,
        meta.kanal or "bilinmiyor",
        kisa,
        detayli_metin(taslak, meta),
    )
    taslak.konular = konular
    taslak.kisiler = kisiler
    taslak.etiketler = etiketler
    return taslak


def yazi_ozet_yukle(klasor: Path) -> YaziOzet | None:
    yol = klasor / DOSYA
    if not yol.exists():
        return None
    return YaziOzet.from_dict(json.loads(yol.read_text(encoding="utf-8")))


def yazi_ozet_kaydet(klasor: Path, ozet: YaziOzet) -> None:
    (klasor / DOSYA).write_text(
        json.dumps(ozet.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _yazi_bolum_ozetle(settings: Settings, meta: YaziMeta, bolum: YaziBolum, dil: str = "tr") -> tuple[str, int]:
    yazar = meta.yazar or meta.site or "bilinmiyor"
    parcalar = _sigdir(bolum.metin, settings)
    ozetler: list[str] = []
    token = 0
    for parca in parcalar:
        if not parca:
            continue
        istem_metni = doldur(
            istem("yazi_bolum.txt"),
            baslik=meta.baslik,
            yazar=yazar,
            bolum=bolum.baslik,
            metin=parca,
            dil=DIL_ADI[dil],
        )
        try:
            metin, sayi = _sohbet(settings, istem_metni, settings.temperature, iz="yazi-bolum")
        except BaglamAsildi:
            daha = _sigdir(
                parca,
                Settings(**{**settings.__dict__, "num_ctx": max(4096, settings.num_ctx // 2)}),
            )
            if len(daha) == 1:
                raise
            alt = []
            for alt_parca in daha:
                alt_istem = doldur(
                    istem("yazi_bolum.txt"),
                    baslik=meta.baslik,
                    yazar=yazar,
                    bolum=bolum.baslik,
                    metin=alt_parca,
                    dil=DIL_ADI[dil],
                )
                alt_metin, sayi = _sohbet(settings, alt_istem, settings.temperature, iz="yazi-bolum-alt")
                token = max(token, sayi)
                alt.append(alt_metin)
            metin = "\n\n".join(alt)
            sayi = token
        token = max(token, sayi)
        ozetler.append(metin.strip())
    birlesik = "\n\n".join(ozetler).strip()
    if birlesik and not dilde_gorunuyor(birlesik, dil):
        metin, sayi = _cevir(settings, birlesik, dil)
        token = max(token, sayi)
        if dilde_gorunuyor(metin, dil):
            birlesik = metin.strip()
    return birlesik, token


def yazi_detay_metni(ozet: YaziOzet) -> str:
    return "\n\n".join(f"### {bolum.baslik}\n\n{bolum.ozet.strip()}" for bolum in ozet.bolumler)


def paketle(bolumler: list[YaziBolum], sinir: int) -> list[list[tuple[int, YaziBolum]]]:
    """Ardışık bölümleri, toplam uzunluk sınırı aşılmayacak şekilde paketler."""
    paketler: list[list[tuple[int, YaziBolum]]] = []
    kova: list[tuple[int, YaziBolum]] = []
    boyut = 0
    for sira, bolum in enumerate(bolumler):
        uzunluk = len(bolum.metin) + len(bolum.baslik) + 10
        if kova and boyut + uzunluk > sinir:
            paketler.append(kova)
            kova, boyut = [], 0
        kova.append((sira, bolum))
        boyut += uzunluk
    if kova:
        paketler.append(kova)
    return paketler


def _paket_metni(paket: list[tuple[int, YaziBolum]]) -> str:
    return "\n\n".join(f"[B{sira + 1}] {bolum.baslik}\n{bolum.metin}" for sira, bolum in paket)


def _no_ozetleri(veri: dict) -> dict[int, str]:
    """Modelin {"no": n, "ozet": ...} listesini 0 tabanlı sıra -> özet haritasına çevirir."""
    cikti: dict[int, str] = {}
    for oge in veri.get("bolumler") or []:
        if not isinstance(oge, dict):
            continue
        try:
            no = int(str(oge.get("no")).strip().lstrip("Bb"))
        except (TypeError, ValueError):
            continue
        ozet = str(oge.get("ozet") or "").strip()
        if ozet:
            cikti[no - 1] = ozet
    return cikti


def _madde_listesi(ham: object) -> str:
    if isinstance(ham, list):
        maddeler = [" ".join(str(m).split()).lstrip("-• ").strip() for m in ham if str(m).strip()]
        return "\n".join(f"- {m}" for m in maddeler[:8])
    return madde_metni(str(ham or ""))


def _dile_getir(settings: Settings, maddeler: list[str], dil: str) -> list[str]:
    """Madde listesi istenen dilde değilse çevirir; çeviri tutmazsa olduğu gibi bırakır."""
    metin = "\n".join(f"- {m}" for m in maddeler)
    if not maddeler or dilde_gorunuyor(metin, dil):
        return maddeler
    try:
        cevrilmis, _ = _cevir(settings, metin, dil, madde=True)
    except RuntimeError:
        return maddeler
    yeni = [m.lstrip("-• ").strip() for m in madde_metni(cevrilmis, len(maddeler)).splitlines() if m.strip()]
    return yeni if len(yeni) == len(maddeler) and dilde_gorunuyor(cevrilmis, dil) else maddeler


YAZI_TURLERI = {"liste", "kavram", "rehber", "yol_haritasi", "gorus", "haber", "inceleme", "diger"}


def _tur(ham: object) -> str:
    tur = str(ham or "").strip().lower().replace(" ", "_").replace("ı", "i").replace("ö", "o").replace("ü", "u")
    return tur if tur in YAZI_TURLERI else "diger"


def _dizi(ham: object, sinir: int) -> list[str]:
    if not isinstance(ham, list):
        return []
    return [m for m in (" ".join(str(oge).split()).lstrip("-•[] ").strip() for oge in ham) if m][:sinir]


def _ad_norm(metin: str) -> str:
    metin = re.sub(r"^\s*\d{1,2}[.)]\s*", "", metin or "")
    return re.sub(r"\W+", " ", metin.lower()).strip()


def oge_baglantisi(ad: str, bolumler: list[YaziBolum]) -> str:
    """Öğeyi anlatan bölümün ilk bağlantısı; yoksa yazısı öğe adını içeren ilk bağlantı."""
    hedef = _ad_norm(ad)
    if not hedef:
        return ""
    for bolum in bolumler:
        baslik = _ad_norm(bolum.baslik)
        if bolum.baglantilar and baslik and (hedef == baslik or hedef in baslik or baslik in hedef):
            return bolum.baglantilar[0].url
    for bolum in bolumler:
        for bag in bolum.baglantilar:
            if hedef in _ad_norm(bag.metin):
                return bag.url
    return ""


def _ogeler(ham: object, bolumler: list[YaziBolum]) -> list[YaziOge]:
    if not isinstance(ham, list):
        return []
    cikti: list[YaziOge] = []
    for oge in ham:
        if not isinstance(oge, dict):
            continue
        ad = " ".join(str(oge.get("ad") or "").split()).strip()
        aciklama = " ".join(str(oge.get("aciklama") or "").split()).strip()
        if ad:
            cikti.append(YaziOge(ad=ad, aciklama=aciklama, url=oge_baglantisi(ad, bolumler)))
    return cikti[:20]


GORUS_KURALI = (
    "Yalnızca tur gorus ise:\n"
    "- tez: yazarın savunduğu ana tez ve önerdiği çözüm, tek cümle, {dil}. Diğer türlerde boş metin.\n"
    "- kisa önce tezi ve önerilen çözümü versin, sonra gerekçeleri; sorunu anlatan giriş maddeleri en fazla 2 olsun. "
    "Yazarın tartışmalı savlarını kanıtlanmış gerçek gibi değil, yazarın savı olarak yaz.\n"
    "- dayanak: 2-4 madde, {dil}. Yazarın tezini neyle desteklediği (araştırma, veri, örnek) ve nerede zayıf kaldığı: "
    "başlıktaki iddianın metinde kanıtlanmaması, kaynağı gösterilmeyen sayılar, yazarın kendi önerdiği terimi veya "
    "yöntemi yerleşik bir kavram gibi sunması, yalnızca oyuncak örneğe dayanma, yazarın çıkarı. "
    "Yalnızca metinde görülebilenleri yaz; dış bilgi ekleme. Diğer türlerde boş liste."
)


KISMI_NOTU = {
    "tr": "Metin kesik (üyelere özel). kisa'nın ilk maddesi yalnızca görünen kısmın özetlendiğini ve neyin eksik olduğunu söylesin.",
    "en": "The text is truncated (members-only). The first kisa bullet must say only the visible part was summarized and what is missing.",
}


def yazi_ozetle(
    meta: YaziMeta,
    bolumler: list[YaziBolum],
    settings: Settings,
    *,
    ilerleme=None,
) -> YaziOzet:
    """Bölümleri paketler; her paket tek JSON çağrısıyla özetlenir. Tek paketse kısa özet ve konular da aynı çağrıda gelir."""
    settings = settings.gorev("ozet")
    ensure_model(settings)
    bolumler = [b for b in bolumler if b.metin.strip()]
    if not bolumler:
        raise RuntimeError("Özetlenecek yazı metni yok.")
    kaynak = meta.dil or dil_tespit("\n".join(b.metin for b in bolumler))
    dil = cikti_dili(settings.summary_lang, kaynak)
    yazar = meta.yazar or meta.site or "bilinmiyor"
    kismi = KISMI_NOTU[dil] if meta.kismi else ""
    gorus = GORUS_KURALI.replace("{dil}", DIL_ADI[dil])
    paketler = paketle(bolumler, settings.pack_chars)
    cikti_sinir = max(settings.num_predict, 4500)
    ozet_metin: dict[int, str] = {}
    son_veri: dict = {}
    en_buyuk = 0
    for sira, paket in enumerate(paketler, start=1):
        if ilerleme:
            ilerleme(f"Özetleniyor: paket {sira}/{len(paketler)} ({len(paket)} bölüm)")
        tek = len(paketler) == 1
        istem_metni = doldur(
            istem("yazi_tek.txt" if tek else "yazi_paket.txt"),
            baslik=meta.baslik,
            yazar=yazar,
            dil=DIL_ADI[dil],
            kismi=kismi,
            gorus=gorus,
            metin=_paket_metni(paket),
        )
        try:
            metin, token = _sohbet(
                settings, istem_metni, settings.temperature, num_predict=cikti_sinir, json_bicim=True, iz="yazi-paket"
            )
            veri = json_cikar(metin)
            en_buyuk = max(en_buyuk, token)
        except BaglamAsildi:
            raise
        except (RuntimeError, ValueError, json.JSONDecodeError):
            veri = {}
        paket_sira = {i for i, _ in paket}
        ozet_metin.update({no: o for no, o in _no_ozetleri(veri).items() if no in paket_sira})
        if tek:
            son_veri = veri
        # Modelin atladığı bölümler tek tek özetlenir.
        for i, bolum in paket:
            if i not in ozet_metin:
                metin, token = _yazi_bolum_ozetle(settings, meta, bolum, dil)
                en_buyuk = max(en_buyuk, token)
                ozet_metin[i] = metin
    ozetler = [
        YaziBolumOzet(baslik=b.baslik, ozet=ozet_metin.get(i, ""), baglantilar=list(b.baglantilar))
        for i, b in enumerate(bolumler)
    ]
    for oz in ozetler:
        if len(oz.ozet) > 80 and not dilde_gorunuyor(oz.ozet, dil):
            metin, _ = _cevir(settings, oz.ozet, dil)
            if dilde_gorunuyor(metin, dil):
                oz.ozet = metin.strip()
        oz.ozet = anlatici_temizle(oz.ozet)

    govde = "\n\n".join(f"[{bolum.baslik}]\n{bolum.ozet}" for bolum in ozetler)
    if not son_veri.get("kisa"):
        if ilerleme:
            ilerleme("Kısa özet ve konular çıkarılıyor")
        istem_metni = doldur(
            istem("yazi_son.txt"),
            baslik=meta.baslik,
            yazar=yazar,
            dil=DIL_ADI[dil],
            kismi=kismi,
            gorus=gorus,
            bolumler=govde,
        )
        try:
            metin, token = _sohbet(
                settings, istem_metni, settings.temperature, num_predict=2400, json_bicim=True, iz="yazi-son"
            )
            son_veri = json_cikar(metin)
            en_buyuk = max(en_buyuk, token)
        except (RuntimeError, ValueError, json.JSONDecodeError):
            son_veri = {}
    kisa = _madde_listesi(son_veri.get("kisa"))
    if not kisa:
        kisa, token = _kisa_ozet(settings, meta.baslik, yazar, govde, sablon="yazi_kisa.txt", dil=dil)
        en_buyuk = max(en_buyuk, token)
    elif not dilde_gorunuyor(kisa, dil):
        metin, _ = _cevir(settings, kisa, dil, madde=True)
        if dilde_gorunuyor(metin, dil):
            kisa = madde_metni(metin)
    kisa = anlatici_temizle(kisa)
    tur = _tur(son_veri.get("tur"))
    cikarimlar = _dile_getir(settings, _dizi(son_veri.get("cikarimlar"), 5), dil)
    adimlar = _dizi(son_veri.get("adimlar"), 12) if tur in {"rehber", "yol_haritasi"} else []
    adimlar = _dile_getir(settings, adimlar, dil)
    ogeler = _ogeler(son_veri.get("ogeler"), bolumler) if tur in {"liste", "kavram"} else []
    if ogeler and all(oge.aciklama for oge in ogeler):
        aciklamalar = _dile_getir(settings, [oge.aciklama for oge in ogeler], dil)
        for oge, aciklama in zip(ogeler, aciklamalar):
            oge.aciklama = aciklama
    tez = " ".join(str(son_veri.get("tez") or "").split()) if tur == "gorus" else ""
    tez = (_dile_getir(settings, [tez], dil) if tez else [""])[0]
    dayanak = _dile_getir(settings, _dizi(son_veri.get("dayanak"), 4) if tur == "gorus" else [], dil)
    taslak = YaziOzet(
        kisa=kisa,
        bolumler=ozetler,
        baglam_token=en_buyuk,
        num_ctx=settings.num_ctx,
        dil=dil,
        tur=tur,
        ogeler=ogeler,
        adimlar=adimlar,
        cikarimlar=cikarimlar,
        tez=tez,
        dayanak=dayanak,
    )
    konular = _listeden(son_veri.get("konular"), 5, konu_adi)
    kisiler = _listeden(son_veri.get("kisiler"), 5, konu_adi)
    etiketler = _listeden(son_veri.get("etiketler"), 5, etiket_temizle)
    if not (konular or kisiler or etiketler):
        if ilerleme:
            ilerleme("Konular çıkarılıyor")
        konular, kisiler, etiketler = _konulari_cikar(settings, meta.baslik, yazar, kisa, yazi_detay_metni(taslak))
    taslak.konular = konular
    taslak.kisiler = kisiler
    taslak.etiketler = etiketler
    return taslak
