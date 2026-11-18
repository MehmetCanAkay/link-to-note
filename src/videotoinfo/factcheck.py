"""İddia çıkarma, web araması ve kaynaklı hüküm.

Kaynak linkleri yalnızca arama sonuçlarından kabul edilir.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.llm import SYSTEM, chat, ensure_model, model_degistir
from videotoinfo.models import Baglanti, ClaimResult, Source, Transcript
from videotoinfo.search import Arama, SearchResult, make_searcher
from videotoinfo.textutil import (
    DIL_ADI,
    ISTEMCI,
    TARAYICI,
    cikti_dili,
    dil_kodu,
    doldur,
    ingilizce_gorunuyor,
    json_cikar,
    norm_url,
    turkce_gorunuyor,
)

DOSYA = "factcheck.json"
ISTEM_KOK = Path(__file__).parent / "prompts"
DUZELTME_HUKUMLERI = {"yanlis", "eksik", "eskimis"}


def factcheck_yukle(klasor: Path) -> list[ClaimResult] | None:
    yol = klasor / DOSYA
    if not yol.exists():
        return None
    veri = json.loads(yol.read_text(encoding="utf-8"))
    return [ClaimResult.from_dict(oge) for oge in veri.get("iddialar") or []]


def factcheck_kaydet(klasor: Path, iddialar: list[ClaimResult]) -> None:
    (klasor / DOSYA).write_text(
        json.dumps({"iddialar": [i.as_dict() for i in iddialar]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _istem(ad: str) -> str:
    return (ISTEM_KOK / ad).read_text(encoding="utf-8")


def _parcala(metin: str, boyut: int = 6000) -> list[str]:
    metin = metin.strip()
    if len(metin) <= boyut:
        return [metin] if metin else []
    parcalar: list[str] = []
    bas = 0
    while bas < len(metin):
        bit = min(len(metin), bas + boyut)
        if bit < len(metin):
            kirilma = metin.rfind("\n", bas, bit)
            if kirilma > bas + boyut // 2:
                bit = kirilma
        parca = metin[bas:bit].strip()
        if parca:
            parcalar.append(parca)
        bas = bit
    return parcalar


@dataclass
class Iddia:
    metin: str
    sorgu: str
    bolum: int


def _json_iddialar(metin: str) -> list[tuple[str, str]]:
    """(iddia, arama sorgusu) çiftleri. Eski biçim (düz metin listesi) de kabul edilir."""
    veri = json_cikar(metin)
    ham = veri.get("iddialar") or []
    if not isinstance(ham, list):
        return []
    cikti: list[tuple[str, str]] = []
    gorulen: set[str] = set()
    for oge in ham:
        if isinstance(oge, str):
            iddia, sorgu = oge, ""
        elif isinstance(oge, dict):
            iddia = str(oge.get("iddia") or oge.get("claim") or "")
            sorgu = str(oge.get("sorgu") or oge.get("query") or "")
        else:
            continue
        iddia = " ".join(iddia.split()).strip()
        sorgu = " ".join(sorgu.split()).strip()
        if iddia and iddia not in gorulen:
            gorulen.add(iddia)
            cikti.append((iddia, sorgu or iddia))
    return cikti


def _dil_satiri(dil: str) -> str:
    return "İngilizce (English)" if dil == "en" else ("Türkçe" if dil == "tr" else "metnin kendi dilinde")


def _paketler(parcalar: list[str], sinir: int) -> list[list[tuple[int, str]]]:
    """Ardışık parçaları sınırı aşmadan birleştirir; uzun parçalar bölünür. (parça sırası, metin) listeleri."""
    paketler: list[list[tuple[int, str]]] = []
    kova: list[tuple[int, str]] = []
    boyut = 0
    for sira, parca in enumerate(parcalar):
        for alt in _parcala(parca, sinir):
            if kova and boyut + len(alt) > sinir:
                paketler.append(kova)
                kova, boyut = [], 0
            kova.append((sira, alt))
            boyut += len(alt) + 2
    if kova:
        paketler.append(kova)
    return paketler


def _bolum_bul(iddia: str, paket: list[tuple[int, str]]) -> int:
    """İddianın paket içinde en çok kelime paylaştığı parçanın sırası."""
    if len(paket) == 1:
        return paket[0][0]
    kelimeler = {k for k in re.findall(r"\w+", iddia.lower()) if len(k) > 3}
    return max(paket, key=lambda p: len(kelimeler & set(re.findall(r"\w+", p[1].lower()))))[0]


def iddialari_cikar(parcalar: list[str], settings: Settings, dil: str = "tr") -> list[Iddia]:
    """Kontrole değer iddiaları, çıktığı parçanın sırasıyla döndürür. Hiç yoksa boş liste."""
    ensure_model(settings)
    adaylar: list[Iddia] = []
    gorulen: set[str] = set()
    for paket in _paketler(parcalar, max(settings.pack_chars, 6000)):
        metin_paket = "\n\n".join(alt for _, alt in paket)
        en_fazla = min(settings.max_claims, max(3, len(metin_paket) // 2500))
        istem = doldur(_istem("iddia.txt"), max=str(en_fazla), dil=_dil_satiri(dil), metin=metin_paket)
        try:
            metin, _ = chat(settings, istem, sistem=SYSTEM, temperature=0.1, num_predict=900, json_bicim=True, iz="iddia")
        except (RuntimeError, ValueError):
            continue
        try:
            ciftler = _json_iddialar(metin)
        except (ValueError, json.JSONDecodeError):
            continue
        for iddia, sorgu in ciftler[:en_fazla]:
            if iddia not in gorulen:
                gorulen.add(iddia)
                adaylar.append(Iddia(iddia, sorgu, _bolum_bul(iddia, paket)))
    if len(adaylar) <= settings.max_claims:
        return adaylar
    istem = doldur(
        _istem("iddia_sec.txt"),
        max=str(settings.max_claims),
        adaylar="\n".join(f"- {a.metin}" for a in adaylar),
    )
    try:
        metin, _ = chat(settings, istem, sistem=SYSTEM, temperature=0.1, num_predict=700, json_bicim=True, iz="iddia-sec")
        secilen = [iddia for iddia, _ in _json_iddialar(metin)]
    except (RuntimeError, ValueError, json.JSONDecodeError):
        secilen = []
    harita = {a.metin: a for a in adaylar}
    sonuc = [harita[i] for i in secilen if i in harita]
    return (sonuc or adaylar)[: settings.max_claims]


def duzeltme_temizle(hukum: str, duzeltme: str) -> str:
    """Kaynağı olmayan veya doğrulanamayan hükümde düzeltme metni tutulmaz."""
    if hukum not in DUZELTME_HUKUMLERI:
        return ""
    return " ".join((duzeltme or "").split()).strip()


def hukmu_uygula(
    hukum: str,
    gerekce: str,
    belirtilen: list[str],
    bulunan: list[SearchResult],
) -> tuple[str, list[Source]]:
    """Modelin yazdığı link aramada yoksa hükmü düşürür."""
    harita: dict[str, SearchResult] = {}
    harita_sorgu_suz: dict[str, SearchResult] = {}
    for sonuc in bulunan:
        tam = norm_url(sonuc.url)
        kisa = norm_url(sonuc.url, sorgu_at=True)
        if tam:
            harita[tam] = sonuc
        if kisa:
            harita_sorgu_suz[kisa] = sonuc
    secilen: list[Source] = []
    gorulen: set[str] = set()
    for url in belirtilen:
        sonuc = harita.get(norm_url(url)) or harita_sorgu_suz.get(norm_url(url, sorgu_at=True))
        if sonuc is None:
            continue
        anahtar = norm_url(sonuc.url)
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        secilen.append(Source(url=sonuc.url, baslik=sonuc.baslik))
    if hukum != "dogrulanamadi" and not secilen:
        return "dogrulanamadi", []
    return hukum, secilen


def _hukum_adi(ham: str) -> str:
    h = ham.strip().lower()
    for eski, yeni in (("ğ", "g"), ("ı", "i"), ("ş", "s"), ("ç", "c"), ("ö", "o"), ("ü", "u")):
        h = h.replace(eski, yeni)
    h = h.replace(" ", "").replace("_", "").replace("-", "")
    if h in {"dogru", "true", "correct", "supported"}:
        return "dogru"
    if h in {"yanlis", "false", "incorrect", "contradicted"}:
        return "yanlis"
    if h in {"eksik", "partial", "kismen"}:
        return "eksik"
    if h in {"eskimis", "eski", "outdated", "obsolete", "gunceldegil"}:
        return "eskimis"
    return "dogrulanamadi"


# Paywall aşan aynalar ve arşivler kaynak sayılmaz.
ENGELLI_ALAN = ("freedium", "archive.ph", "archive.today", "archive.is", "12ft.io", "removepaywall", "smry.ai")


def _bolge(settings: Settings, dil: str) -> str:
    if dil == "en":
        return "us-en"
    if dil == "tr":
        return settings.search_region
    return "wt-wt"


def _baslik_norm(metin: str) -> str:
    return re.sub(r"\W+", " ", (metin or "").lower()).strip()


_GUCLU_ALAN = (
    ".gov", ".edu", ".mil", "wikipedia.org", "arxiv.org", "github.com", "nature.com", "science.org",
    "reuters.com", "apnews.com", "bbc.co", "bbc.com", "who.int", "europa.eu", "acm.org", "ieee.org", "gov.tr", "edu.tr",
)
_ZAYIF_ALAN = (
    "medium.com", "blogspot.", "substack.com", "quora.com", "reddit.com", "pinterest.", "linkedin.com/pulse",
    "facebook.com", "tiktok.com", "youtube.com",
)


def _kelimeler(metin: str, en_az: int = 3) -> set[str]:
    return {k for k in re.findall(r"\w+", (metin or "").lower()) if len(k) >= en_az}


def kaynak_puani(sonuc: SearchResult, iddia: str) -> int:
    """Kurumun kendi sitesi ve güvenilir alanlar öne, blog ve sosyal medya arkaya."""
    from urllib.parse import urlsplit

    alan = urlsplit(sonuc.url).netloc.lower().removeprefix("www.")
    adres = sonuc.url.lower()
    puan = 3 if sonuc.yazidan else 0
    if _kelimeler(iddia) & set(alan.split(".")[:-1]):
        puan += 3
    if alan.startswith(("docs.", "developer.", "developers.", "learn.")):
        puan += 1
    if any(g in alan for g in _GUCLU_ALAN):
        puan += 2
    if any(z in adres for z in _ZAYIF_ALAN):
        puan -= 2
    return puan


def sirala(sonuclar: list[SearchResult], iddia: str) -> list[SearchResult]:
    return sorted(sonuclar, key=lambda s: -kaynak_puani(s, iddia))


def yazi_baglantilari(iddia: str, baglantilar: list[Baglanti], sinir: int = 2) -> list[SearchResult]:
    """Yazının, iddianın geçtiği bölümde verdiği ve iddiayla ilgili görünen bağlantılar."""
    from urllib.parse import urlsplit

    kelimeler = _kelimeler(iddia)
    puanli = []
    for bag in baglantilar:
        parca = urlsplit(bag.url)
        iz = _kelimeler(bag.metin) | _kelimeler(parca.netloc.replace(".", " ") + " " + parca.path)
        ortak = len(kelimeler & iz)
        if ortak >= 2:
            puanli.append((ortak, bag))
    puanli.sort(key=lambda p: -p[0])
    return [SearchResult(baslik=bag.metin or bag.url, url=bag.url, ozet="", yazidan=True) for _, bag in puanli[:sinir]]


def _ara(
    arama: Arama,
    sorgu: str,
    settings: Settings,
    haric: Haric,
    yedek: Arama | None = None,
) -> list[SearchResult]:
    def temiz(liste: list[SearchResult]) -> list[SearchResult]:
        cikti = []
        gorulen = set()
        for sonuc in liste:
            if haric.dislar(sonuc):
                continue
            anahtar = norm_url(sonuc.url)
            if not anahtar or anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            cikti.append(sonuc)
        return cikti

    bulunan = temiz(arama.ara(sorgu, settings.max_results))
    if len(bulunan) < 2 and yedek is not None:
        bulunan = temiz(bulunan + yedek.ara(sorgu, settings.max_results))
    return bulunan


@dataclass
class Haric:
    """Doğrulanan içeriğin kendisi (ve kopyaları) kaynak olarak kullanılmaz."""

    kaynak_id: str = ""
    urller: tuple[str, ...] = ()
    baslik: str = ""

    def dislar(self, sonuc: SearchResult) -> bool:
        url = sonuc.url or ""
        if self.kaynak_id and self.kaynak_id in url:
            return True
        if any(alan in url.lower() for alan in ENGELLI_ALAN):
            return True
        kisa = norm_url(url, sorgu_at=True)
        if kisa and any(kisa == norm_url(u, sorgu_at=True) for u in self.urller if u):
            return True
        if self.baslik and len(self.baslik) > 15:
            b = _baslik_norm(sonuc.baslik)
            if self.baslik in b:
                return True
        return False


def ilgili_kisim(metin: str, iddia: str, sinir: int) -> str:
    """Sayfadan iddiayla en çok kelime paylaşan paragrafları, sırasını koruyarak seçer."""
    metin = (metin or "").strip()
    if len(metin) <= sinir:
        return metin
    kelimeler = {k for k in re.findall(r"\w+", iddia.lower()) if len(k) > 3}
    paragraflar = [p.strip() for p in re.split(r"\n\s*\n|\n", metin) if len(p.strip()) > 40]
    if not kelimeler or not paragraflar:
        return metin[:sinir]
    puanli = sorted(
        range(len(paragraflar)),
        key=lambda i: -len(kelimeler & set(re.findall(r"\w+", paragraflar[i].lower()))),
    )
    secilen: list[int] = []
    boyut = 0
    for i in puanli:
        if boyut + len(paragraflar[i]) > sinir and secilen:
            continue
        secilen.append(i)
        boyut += len(paragraflar[i]) + 2
        if boyut >= sinir:
            break
    return "\n\n".join(paragraflar[i] for i in sorted(secilen))[:sinir]


def _sayfa_getir(istemci, sonuc: SearchResult) -> str:
    import trafilatura

    try:
        yanit = istemci.get(sonuc.url)
        if yanit.status_code in (401, 403, 429):
            yanit = istemci.get(sonuc.url, headers={"User-Agent": ISTEMCI})
        if yanit.status_code < 400 and yanit.text:
            return trafilatura.extract(yanit.text, include_comments=False, include_tables=False) or ""
    except Exception:
        pass
    return ""


def _sayfalari_oku(
    sonuclar: list[SearchResult], iddia: str, settings: Settings, okunacak: int = 3
) -> list[tuple[SearchResult, str]]:
    import httpx

    secilen = sonuclar[:okunacak]
    with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": TARAYICI}) as istemci:
        with ThreadPoolExecutor(max_workers=max(1, len(secilen))) as havuz:
            metinler = list(havuz.map(lambda s: _sayfa_getir(istemci, s), secilen))
    okunan: list[tuple[SearchResult, str]] = []
    for sonuc, metin in zip(secilen, metinler):
        if len(metin) < 200:
            metin = sonuc.ozet
        okunan.append((sonuc, ilgili_kisim(metin, iddia, settings.page_chars)))
    return okunan


def _kaynak_blogu(okunan: list[tuple[SearchResult, str]]) -> str:
    parcalar = []
    for sira, (sonuc, metin) in enumerate(okunan, start=1):
        isaret = " (yazının kendi bağlantısı)" if sonuc.yazidan else ""
        parcalar.append(
            f"[{sira}]{isaret} {sonuc.baslik}\nURL: {sonuc.url}\nMetin:\n{metin or sonuc.ozet or '(metin alınamadı)'}"
        )
    return "\n\n".join(parcalar)


def _kanit_topla(
    iddia: Iddia,
    settings: Settings,
    arama: Arama,
    yedek: Arama | None,
    haric: Haric,
    baglantilar: list[Baglanti] | None = None,
) -> tuple[list[SearchResult], list[tuple[SearchResult, str]]]:
    yazidan = [s for s in yazi_baglantilari(iddia.metin, baglantilar or []) if not haric.dislar(s)]
    bulunan = _ara(arama, iddia.sorgu, settings, haric, yedek)
    if not bulunan and iddia.sorgu != iddia.metin:
        bulunan = _ara(arama, iddia.metin, settings, haric, yedek)
    gorulen = {norm_url(s.url) for s in yazidan}
    bulunan = yazidan + sirala([s for s in bulunan if norm_url(s.url) not in gorulen], iddia.metin)
    if not bulunan:
        return [], []
    return bulunan, _sayfalari_oku(bulunan, iddia.metin, settings, okunacak=3 + len(yazidan))


def _dile_uydur(settings: Settings, metin: str, dil: str) -> str:
    """Model gerekçeyi kaynakların dilinde yazdıysa notun diline çevirir."""
    yanlis_dil = turkce_gorunuyor(metin) if dil == "en" else (ingilizce_gorunuyor(metin) and not turkce_gorunuyor(metin))
    if not metin or not yanlis_dil:
        return metin
    istem = f"Şu metni doğal {DIL_ADI.get(dil, 'Türkçe')} diline çevir. Bilgi ekleme, yalnızca çeviriyi yaz.\n\n{metin}"
    try:
        cevrilmis, _ = chat(settings, istem, sistem=SYSTEM, temperature=0.1, num_predict=400, iz="ceviri")
    except RuntimeError:
        return metin
    cevrilmis = " ".join(cevrilmis.split())
    return cevrilmis or metin


def hukum_ver(
    iddia: Iddia,
    bulunan: list[SearchResult],
    okunan: list[tuple[SearchResult, str]],
    settings: Settings,
    *,
    yayin_tarihi: str | None = None,
    cikti: str = "tr",
) -> ClaimResult:
    if not bulunan:
        return ClaimResult(iddia.metin, "dogrulanamadi", "Arama sonuç vermedi." if cikti == "tr" else "No search results.", [], bolum=iddia.bolum)
    istem = doldur(
        _istem("hukum.txt"),
        iddia=iddia.metin,
        kaynaklar=_kaynak_blogu(okunan),
        bugun=date.today().isoformat(),
        yayin=yayin_tarihi or "bilinmiyor",
        dil=DIL_ADI.get(cikti, "Türkçe"),
    )
    try:
        metin, _ = chat(settings, istem, sistem=SYSTEM, temperature=0.1, num_predict=600, json_bicim=True, iz="hukum")
        veri = json_cikar(metin)
    except (RuntimeError, ValueError, json.JSONDecodeError) as hata:
        return ClaimResult(iddia.metin, "dogrulanamadi", f"Hüküm okunamadı: {hata}", [], bolum=iddia.bolum)
    hukum = _hukum_adi(str(veri.get("hukum") or veri.get("hüküm") or veri.get("verdict") or ""))
    gerekce = str(veri.get("gerekce") or veri.get("gerekçe") or "").strip()
    duzeltme = str(veri.get("duzeltme") or veri.get("düzeltme") or "").strip()
    ham_kaynak = veri.get("kaynaklar") or []
    belirtilen = [str(u) for u in ham_kaynak if isinstance(u, str)]
    hukum, kaynaklar = hukmu_uygula(hukum, gerekce, belirtilen, bulunan)
    duzeltme = _dile_uydur(settings, duzeltme_temizle(hukum, duzeltme), cikti)
    gerekce = _dile_uydur(settings, gerekce, cikti)
    if hukum == "dogrulanamadi" and not gerekce:
        gerekce = "Kaynaklar bu iddiayı doğrulamaya yetmedi." if cikti == "tr" else "The sources were not enough to verify this claim."
    if hukum == "dogrulanamadi" and belirtilen and not kaynaklar:
        on_ek = "Modelin verdiği bağlantı arama sonuçlarında yoktu. " if cikti == "tr" else "The model cited a link that was not among the search results. "
        gerekce = on_ek + gerekce
    return ClaimResult(iddia.metin, hukum, gerekce, kaynaklar, duzeltme=duzeltme, bolum=iddia.bolum)


def dogrula_parcalari(
    parcalar: list[str],
    settings: Settings,
    *,
    kaynak_id: str = "",
    kaynak_url: str = "",
    haric_urller: tuple[str, ...] = (),
    baslik: str = "",
    yayin_tarihi: str | None = None,
    dil: str = "tr",
    cikti: str = "tr",
    baglantilar: list[list[Baglanti]] | None = None,
    ilerleme=None,
) -> list[ClaimResult]:
    """İddiaları çıkarır; arama ve sayfa okuma paralel yürür, model hükümleri sırayla yazar.

    baglantilar verilirse parça sırasıyla eşleşir; iddianın geçtiği parçadaki ilgili bağlantı önce okunur.
    """
    iddia_ayar = settings.gorev("iddia")
    hukum_ayar = settings.gorev("hukum")
    if ilerleme:
        ilerleme("İddialar çıkarılıyor")
    iddialar = iddialari_cikar(parcalar, iddia_ayar, dil)
    if not iddialar:
        if ilerleme:
            ilerleme("Kontrole değer olgusal iddia bulunamadı")
        return []
    model_degistir(iddia_ayar, hukum_ayar)
    ensure_model(hukum_ayar)
    bolge_ayar = replace(settings, search_region=_bolge(settings, dil))
    arama = make_searcher(bolge_ayar)
    yedek = None
    if bolge_ayar.search_region != "wt-wt":
        yedek = make_searcher(replace(settings, search_region="wt-wt"))
    haric = Haric(kaynak_id=kaynak_id, urller=(kaynak_url, *haric_urller), baslik=_baslik_norm(baslik))
    sonuclar: list[ClaimResult] = []
    # shortcut: havuz contextvars kopyalamaz; thread içine chat() eklenirse copy_context().run gerekir
    with ThreadPoolExecutor(max_workers=3) as havuz:
        isler = [
            havuz.submit(
                _kanit_topla,
                iddia,
                settings,
                arama,
                yedek,
                haric,
                (baglantilar or [])[iddia.bolum] if 0 <= iddia.bolum < len(baglantilar or []) else None,
            )
            for iddia in iddialar
        ]
        for sira, (iddia, gelecek) in enumerate(zip(iddialar, isler), start=1):
            if ilerleme:
                ilerleme(f"Kontrol ediliyor: {sira}/{len(iddialar)}")
            try:
                bulunan, okunan = gelecek.result()
                sonuclar.append(
                    hukum_ver(iddia, bulunan, okunan, hukum_ayar, yayin_tarihi=yayin_tarihi, cikti=cikti)
                )
            except Exception as hata:
                sonuclar.append(
                    ClaimResult(iddia.metin, "dogrulanamadi", f"Kontrol sırasında hata: {hata}", [], bolum=iddia.bolum)
                )
    return sonuclar


def dogrula(
    transcript: Transcript,
    settings: Settings,
    *,
    kaynak_id: str = "",
    kaynak_url: str = "",
    yayin_tarihi: str | None = None,
    baslik: str = "",
    ilerleme=None,
) -> list[ClaimResult]:
    dil = dil_kodu(transcript.dil)
    return dogrula_parcalari(
        _parcala(transcript.metin),
        settings,
        kaynak_id=kaynak_id,
        kaynak_url=kaynak_url,
        baslik=baslik,
        yayin_tarihi=yayin_tarihi,
        dil=dil,
        cikti=cikti_dili(settings.summary_lang, dil),
        ilerleme=ilerleme,
    )
