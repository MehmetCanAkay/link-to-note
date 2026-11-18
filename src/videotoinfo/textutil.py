"""Metin, zaman ve JSON yardımcıları."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit


def saniye_yaz(saniye: float) -> str:
    toplam = max(0, int(saniye))
    saat, kalan = divmod(toplam, 3600)
    dakika, sn = divmod(kalan, 60)
    return f"{saat:02d}:{dakika:02d}:{sn:02d}"


def sure_dk(saniye: int) -> int:
    if saniye <= 0:
        return 0
    return int(round(saniye / 60))


def doldur(sablon: str, **alanlar: str) -> str:
    sonuc = sablon
    for anahtar, deger in alanlar.items():
        sonuc = sonuc.replace("{" + anahtar + "}", deger)
    return sonuc


def think_temizle(metin: str) -> str:
    return re.sub(r"<think>.*?</think>", "", metin, flags=re.DOTALL | re.IGNORECASE).strip()


def json_cikar(metin: str) -> dict:
    temiz = think_temizle(metin).strip()
    temiz = re.sub(r"^```(?:json)?\s*", "", temiz)
    temiz = re.sub(r"\s*```$", "", temiz)
    bas = temiz.find("{")
    if bas < 0:
        raise ValueError("JSON bulunamadı")
    veri, _ = json.JSONDecoder().raw_decode(temiz[bas:])
    if not isinstance(veri, dict):
        raise ValueError("JSON nesne değil")
    return veri


def guvenli_id(deger: str) -> str:
    temiz = re.sub(r"[^A-Za-z0-9_-]+", "_", deger).strip("_")
    return (temiz[:80] or "video")


def dosya_koku(tarih: str, baslik: str) -> str:
    temiz = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", baslik)
    temiz = re.sub(r"\s+", " ", temiz).strip().rstrip(".")
    if len(temiz) > 80:
        temiz = temiz[:80].rstrip()
    return f"{tarih} {temiz}".strip()


def konu_adi(ad: str) -> str:
    ad = ad.strip().strip("[]")
    ad = re.sub(r"\s+", " ", ad)
    ad = re.sub(r'[\\/:*?"<>|#^\[\]]+', "", ad).strip().rstrip(".")
    if not ad or len(ad) > 60:
        return ""
    return ad


def etiket_temizle(etiket: str) -> str:
    etiket = str(etiket).strip().lower().lstrip("#")
    etiket = re.sub(r"\s+", "-", etiket)
    etiket = re.sub(r"[^\w\-]", "", etiket, flags=re.UNICODE)
    etiket = etiket.strip("-_")
    if not etiket or etiket in {"video", "yazi"}:
        return ""
    return etiket[:40]


def zaman_linki(url: str, saniye: float, platform: str) -> str:
    if platform != "youtube" or not url:
        return url
    taban = url.split("#", 1)[0]
    ayirac = "&" if "?" in taban else "?"
    return f"{taban}{ayirac}t={int(saniye)}s"


TARAYICI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# Tarayıcı taklidini reddeden siteler (ör. Vikipedi 403 verir) için kendini tanıtan istemci adı.
ISTEMCI = "VideoToInfo/0.1 (local note-taking tool; python-httpx)"


def norm_url(url: str, sorgu_at: bool = False) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parca = urlsplit(url)
    if not parca.scheme or not parca.netloc:
        return ""
    netloc = parca.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    yol = parca.path.rstrip("/")
    sorgu = "" if sorgu_at else parca.query
    return urlunsplit((parca.scheme.lower(), netloc, yol, sorgu, ""))


_TAKIP = {
    "source", "gi", "sk", "srsltid", "fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid",
    "_hsenc", "_hsmi", "igshid", "si", "trk", "cmpid",
}


def temiz_url(url: str) -> str:
    """Takip parametrelerini (utm_*, Medium'un source/gi vb.) atar; diğer sorgu parametreleri kalır."""
    url = (url or "").strip()
    parca = urlsplit(url)
    if not parca.scheme or not parca.netloc:
        return url
    kalan = [
        p for p in parca.query.split("&")
        if p and not p.split("=", 1)[0].lower().startswith("utm_") and p.split("=", 1)[0].lower() not in _TAKIP
    ]
    return urlunsplit((parca.scheme, parca.netloc, parca.path, "&".join(kalan), parca.fragment))


def wikilink_gecir(metin: str, adlar: list[str]) -> str:
    adaylar = [ad for ad in sorted(set(adlar), key=len, reverse=True) if len(ad) >= 3]
    if not adaylar or not metin:
        return metin
    parcalar = re.split(r"(\[\[[^\]]+\]\]|\[[^\]]+\]\([^)]+\))", metin)
    for i, parca in enumerate(parcalar):
        if parca.startswith("["):
            continue
        for ad in adaylar:
            parcalar[i] = re.sub(
                rf"(?<!\w){re.escape(ad)}(?!\w)",
                f"[[{ad}]]",
                parcalar[i],
            )
    return "".join(parcalar)


def konusma_metni(md: str) -> str:
    satirlar: list[str] = []
    for satir in md.splitlines():
        satir = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", satir)
        satir = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", satir)
        satir = re.sub(r"\[\[([^\]]+)\]\]", r"\1", satir)
        satir = satir.replace("**", "").replace("`", "")
        satir = re.sub(r"^[#>*\s]+", "", satir)
        satir = re.sub(r"^[-•]\s*", "", satir).strip()
        if satir:
            satirlar.append(satir)
    return "\n".join(satirlar)


def turkce_gorunuyor(metin: str) -> bool:
    kucuk = metin.lower()
    if any(harf in kucuk for harf in "çğıöşü"):
        return True
    kelimeler = set(re.findall(r"\w+", kucuk, flags=re.UNICODE))
    yaygin = {"ve", "bir", "bu", "için", "ile", "olan", "olarak", "da", "de", "mi", "ne", "çok"}
    return len(kelimeler & yaygin) >= 2


_INGILIZCE = {
    "the", "and", "of", "to", "is", "in", "that", "it", "for", "you", "with", "this",
    "are", "on", "as", "be", "was", "have", "not", "but", "they", "what", "can",
}
_TURKCE = {"ve", "bir", "bu", "için", "ile", "olan", "olarak", "da", "de", "mi", "ne", "çok", "gibi", "daha", "ama"}


def dil_tespit(metin: str) -> str:
    """Kaba dil tespiti: 'tr', 'en' veya 'diger'."""
    ornek = (metin or "")[:6000].lower()
    kelimeler = re.findall(r"\w+", ornek, flags=re.UNICODE)
    if not kelimeler:
        return "diger"
    tr = sum(1 for k in kelimeler if k in _TURKCE) + len(re.findall(r"[çğışöü]", ornek)) / 4
    en = sum(1 for k in kelimeler if k in _INGILIZCE)
    oran = len(kelimeler)
    if tr >= 2 and tr / oran > 0.04 and tr >= en:
        return "tr"
    if en / oran > 0.08:
        return "en"
    return "diger"


def dil_kodu(kod: str) -> str:
    """Whisper veya serbest dil kodunu 'tr'/'en'/'diger' biçimine indirger."""
    kod = (kod or "").strip().lower()
    if kod in {"tr", "turkish", "türkçe"}:
        return "tr"
    if kod in {"en", "english", "ingilizce"}:
        return "en"
    return "diger"


def cikti_dili(ayar: str, kaynak: str) -> str:
    """Özetin dili: 'tr' veya 'en'."""
    ayar = (ayar or "kaynak").strip().lower()
    if ayar in {"tr", "en"}:
        return ayar
    return "en" if kaynak == "en" else "tr"


DIL_ADI = {"tr": "Türkçe", "en": "İngilizce (English)"}


def ingilizce_gorunuyor(metin: str) -> bool:
    kucuk = metin.lower()
    if len(re.findall(r"[çğış]", kucuk)) > 3:
        return False
    kelimeler = set(re.findall(r"\w+", kucuk, flags=re.UNICODE))
    return len(kelimeler & _INGILIZCE) >= 2


def dilde_gorunuyor(metin: str, dil: str) -> bool:
    return ingilizce_gorunuyor(metin) if dil == "en" else turkce_gorunuyor(metin)


def yaml_olcek(deger: object) -> str:
    if deger is None:
        return "null"
    if isinstance(deger, bool):
        return "true" if deger else "false"
    if isinstance(deger, int) and not isinstance(deger, bool):
        return str(deger)
    metin = str(deger)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", metin):
        return metin
    return json.dumps(metin, ensure_ascii=False)


_ANLATICI = re.compile(
    r"(^|(?<=[.!?]\s)|(?<=^- ))(?:The|This) (?:section|article|text|post) "
    r"(highlights|notes|emphasizes|explains|states|shows|suggests|argues|stresses|points out|concludes|"
    r"indicates|asserts|claims|observes) that\s+(\w)",
    re.MULTILINE,
)
# Bu fiiller sav bildirir; özne silinirse yazarın görüşü olgu gibi okunur.
_SAV_FIILLERI = {"suggests", "argues", "asserts", "claims"}


def anlatici_temizle(metin: str) -> str:
    """"The section highlights that X" -> "X"; "The article argues that X" -> "The author argues that X"."""

    def degistir(e: re.Match) -> str:
        if e.group(2) in _SAV_FIILLERI:
            return f"{e.group(1)}The author {e.group(2)} that {e.group(3)}"
        return e.group(1) + e.group(3).upper()

    return _ANLATICI.sub(degistir, metin or "")


def madde_metni(metin: str, en_fazla: int = 8) -> str:
    temiz = think_temizle(metin)
    satirlar = [s.strip() for s in temiz.splitlines() if s.strip()]
    maddeler = []
    for satir in satirlar:
        if satir.startswith("* "):
            satir = "- " + satir[2:]
        if satir.startswith("- "):
            maddeler.append(satir)
    if len(maddeler) >= 3:
        return "\n".join(maddeler[:en_fazla])
    return "\n".join(satirlar).strip()
