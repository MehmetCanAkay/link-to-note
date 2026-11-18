"""Yazı sayfasını indirir, üst bilgisini çıkarır ve bölümlere ayırır."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from videotoinfo.config import Settings
from videotoinfo.models import Baglanti, YaziBolum, YaziMeta
from videotoinfo.textutil import ISTEMCI, TARAYICI, dil_tespit, norm_url, temiz_url

META_ADI = "meta.json"
METIN_ADI = "metin.json"
EN_AZ_KARAKTER = 600
# Bundan kısa bölüm ayrı özetlenmez, komşu bölüme katılır.
KISA_BOLUM = 300
_BASLIK = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Medium alt başlıkları çoğu zaman "**4. Optimization**" gibi numaralı kalın satırdır.
_KALIN_NUMARALI = re.compile(r"^\s*\*\*\s*(\d{1,2}[.)]\s+[^*\n]{2,80}?)\s*\*\*\s*$")
_BAGLANTI = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\s*\)")
_KISMI = re.compile(r"member-only story|members-only|isPaywalled", re.IGNORECASE)
_TARIH = re.compile(r"\d{4}-\d{2}-\d{2}")
# Medium ve benzerlerinde gövdeye karışan arayüz satırları.
_GURULTU = re.compile(
    r"^\s*(member-only story|listen|share|follow|--|\d+|sign up|sign in|open in app|"
    r"\d+ min read|press enter or click to view image in full size)\s*$",
    re.IGNORECASE,
)
_VIKI_DUZENLE = re.compile(r"\\?\[(?:değiştir|edit)(?: \| kaynağı değiştir)?\\?\]", re.IGNORECASE)
# Yazarın kendi ürün, hizmet veya diğer yazılarına yönlendirdiği bölümler.
_TANITIM_BASLIK = re.compile(
    r"how i can help|work with me|hire me|more (articles|stories|from)|you(?:’|')ll love|keep building|"
    r"read next|related (posts|articles|stories)|follow me|subscribe|newsletter|support (me|my work)|"
    r"buy me a coffee|about the author|before you go|thanks? (you )?for reading|go beyond reading|"
    r"abone ol|beni takip|diğer yazılar|yazarı destekle",
    re.IGNORECASE,
)
# Yayın veya platformun yazı sonuna eklediği kalıp paragraflar; yalnızca metnin sonundaysa atılır.
_ALTBILGI = re.compile(
    r"this (story|article|post) (is|was) (originally )?published|connect with us|follow us on|"
    r"a message from|thank you for being a part of|clap(ping)? (for|and follow)|"
    r"subscribe to (our|my)|join (our|my) (newsletter|community)",
    re.IGNORECASE,
)
_TANITIM_ALAN = ("gumroad.com", "gum.co", "buymeacoffee.com", "ko-fi.com", "patreon.com", "lemonsqueezy.com")
# Paywall aşan aynalar tam metin kaynağı olarak kullanılmaz.
_ENGELLI = ("freedium", "archive.ph", "archive.today", "archive.is", "12ft.io", "removepaywall", "smry.ai", "medium.com")


class YaziHatasi(RuntimeError):
    pass


def yazi_id_uret(url: str) -> str:
    anahtar = norm_url(url, sorgu_at=True) or url.strip()
    return hashlib.sha1(anahtar.encode("utf-8")).hexdigest()[:12]


def yazi_klasoru(settings: Settings, yazi_id: str) -> Path:
    klasor = settings.data_dir / yazi_id
    klasor.mkdir(parents=True, exist_ok=True)
    return klasor


_KILIT = re.compile(r'"isLockedPreviewOnly"\s*:\s*(true|false)')


def kismi_mi(html: str) -> bool:
    """Sayfa yalnızca önizleme mi? Medium abonelere de "Member-only story" etiketini gösterir;
    bu yüzden önce sayfanın kendi kilit bilgisine bakılır."""
    html = html or ""
    kilit = _KILIT.findall(html)
    if kilit:
        return "true" in kilit
    return bool(_KISMI.search(html))


def _paragraf_bol(metin: str, limit: int) -> list[str]:
    if len(metin) <= limit:
        return [metin]
    paragraflar = [p.strip() for p in re.split(r"\n\s*\n", metin) if p.strip()]
    if not paragraflar:
        return [metin]
    parcalar: list[str] = []
    kova: list[str] = []
    boyut = 0
    for paragraf in paragraflar:
        if len(paragraf) > limit:
            if kova:
                parcalar.append("\n\n".join(kova))
                kova, boyut = [], 0
            parcalar.extend(_sert_bol(paragraf, limit))
            continue
        ek = len(paragraf) if not kova else boyut + 2 + len(paragraf)
        if kova and ek > limit:
            parcalar.append("\n\n".join(kova))
            kova, boyut = [paragraf], len(paragraf)
        else:
            kova.append(paragraf)
            boyut = ek
    if kova:
        parcalar.append("\n\n".join(kova))
    return [parca for parca in parcalar if parca.strip()]


def _sert_bol(metin: str, limit: int) -> list[str]:
    parcalar: list[str] = []
    bas = 0
    while bas < len(metin):
        if bas + limit >= len(metin):
            parca = metin[bas:].strip()
            if parca:
                parcalar.append(parca)
            break
        kirilma = metin.rfind(" ", bas, bas + limit)
        if kirilma <= bas:
            kirilma = bas + limit
        parca = metin[bas:kirilma].strip()
        if parca:
            parcalar.append(parca)
        bas = kirilma
    return parcalar


def _ic_baglanti(url: str, sayfa_url: str) -> bool:
    """Yazarın profili, etiket sayfası, oturum açma gibi içerik dışı bağlantılar."""
    yol = urlsplit(url).path.lower()
    if "/@" in yol or yol.startswith(("/tag/", "/m/signin", "/plans")) or "/followers" in yol:
        return True
    return bool(sayfa_url) and norm_url(url, sorgu_at=True) == norm_url(sayfa_url, sorgu_at=True)


def baglanti_ayikla(metin: str, sayfa_url: str = "") -> tuple[str, list[Baglanti]]:
    """Markdown bağlantılarını yazısına indirger, dış bağlantıları ayrı liste olarak döndürür. Görseller atılır."""
    baglar: list[Baglanti] = []
    gorulen: set[str] = set()

    def degistir(eslesme: re.Match) -> str:
        if eslesme.group(1):
            return ""
        yazi = " ".join(eslesme.group(2).replace("**", "").replace("__", "").split()).strip("*_ ")
        url = eslesme.group(3).strip("<>")
        if url.startswith(("http://", "https://")) and not _ic_baglanti(url, sayfa_url):
            temiz = temiz_url(url)
            anahtar = norm_url(temiz)
            if anahtar and anahtar not in gorulen:
                gorulen.add(anahtar)
                # Kelimenin ortasından başlayan bağlantı: "P[ortfolio](url)"
                bas = re.search(r"\w+$", eslesme.string[: eslesme.start()])
                tam = (bas.group(0) if bas and yazi[:1].isalpha() else "") + yazi
                baglar.append(Baglanti(metin=tam or temiz, url=temiz))
        return yazi

    return _BAGLANTI.sub(degistir, metin or ""), baglar


def _duz_uzunluk(metin: str) -> int:
    return len(baglanti_ayikla(metin)[0].strip())


@dataclass
class _Parca:
    seviye: int  # 0: ilk başlıktan önceki metin
    baslik: str
    ham_baslik: str
    satirlar: list[str]

    @property
    def govde(self) -> str:
        return "\n".join(self.satirlar).strip()


def _baslik_metni(ham: str) -> str:
    return baglanti_ayikla(ham)[0].strip().strip("*_").strip()


def _basliklara_ayir(metin: str, varsayilan: str) -> list[_Parca]:
    parcalar = [_Parca(0, varsayilan, "", [])]
    son_seviye = 1
    for satir in metin.split("\n"):
        eslesme = _BASLIK.match(satir)
        if eslesme:
            seviye, ham = len(eslesme.group(1)), eslesme.group(2)
            son_seviye = seviye
        else:
            eslesme = _KALIN_NUMARALI.match(satir)
            if not eslesme:
                parcalar[-1].satirlar.append(satir)
                continue
            seviye, ham = son_seviye + 1, eslesme.group(1)
        baslik = _baslik_metni(ham)
        if not baslik:
            parcalar[-1].satirlar.append(satir)
            continue
        parcalar.append(_Parca(seviye, baslik, ham, []))
    # Sayfanın en üstündeki tek H1 yazının adıdır, bölüm değildir.
    if (
        len(parcalar) > 1
        and parcalar[1].seviye == 1
        and not parcalar[0].govde
        and sum(p.seviye == 1 for p in parcalar) == 1
    ):
        ad = parcalar[1].baslik
        parcalar[1] = _Parca(0, "Giriş" if _norm(ad) == _norm(varsayilan) else ad, "", parcalar[1].satirlar)
        del parcalar[0]
    return parcalar


def _tanitim_url(url: str) -> bool:
    kucuk = url.lower()
    return any(alan in kucuk for alan in _TANITIM_ALAN) or bool(re.search(r"utm_\w+=[^&]*cta", kucuk))


def _tanitim_paragrafi(paragraf: str) -> bool:
    return any(_tanitim_url(e.group(3)) for e in _BAGLANTI.finditer(paragraf) if not e.group(1))


def _tanitim_mi(parca: _Parca) -> bool:
    if _TANITIM_BASLIK.search(parca.baslik):
        return True
    ham = parca.ham_baslik + "\n" + parca.govde
    urller = [e.group(3) for e in _BAGLANTI.finditer(ham) if not e.group(1)]
    if not urller or _duz_uzunluk(ham) > 1200:
        return False
    return sum(_tanitim_url(u) for u in urller) * 2 >= len(urller)


def _tanitimi_at(parcalar: list[_Parca]) -> list[_Parca]:
    """Tanıtım bölümlerini atar. Yazının son kısmında başlayan tanıtımdan sonrası tümüyle atılır."""
    toplam = sum(len(p.govde) for p in parcalar) or 1
    birikim = 0
    kalan: list[_Parca] = []
    for parca in parcalar:
        if parca.seviye > 0 and _tanitim_mi(parca):
            if birikim >= toplam * 0.7:
                break
        else:
            kalan.append(parca)
        birikim += len(parca.govde)
    return kalan


@dataclass
class _Grup:
    baslik: str
    ham_baslik: str
    bloklar: list[str]

    @property
    def govde(self) -> str:
        return "\n\n".join(b for b in self.bloklar if b.strip()).strip()


def _grupla(parcalar: list[_Parca], limit: int, varsayilan: str = "") -> list[_Grup]:
    """Ana başlık düzeyinde gruplar; alt başlıklar grubun içinde kalır. Uzun grup alt başlık sınırından bölünür."""
    seviyeler = [p.seviye for p in parcalar if p.seviye > 0]
    # Tek kez geçen üst başlık (ör. Medium alt başlığı) ana düzey sayılmaz.
    tekrarli = [s for s in set(seviyeler) if seviyeler.count(s) > 1]
    ana = min(tekrarli or seviyeler) if seviyeler else 0
    gruplar: list[_Grup] = []
    for parca in parcalar:
        if parca.seviye == 0 or parca.seviye <= ana or not gruplar:
            gruplar.append(_Grup(parca.baslik, parca.ham_baslik, [parca.govde]))
        else:
            gruplar[-1].bloklar.append(f"{'#' * parca.seviye} {parca.ham_baslik}\n\n{parca.govde}".strip())
    # Medium alt başlığı: başlıktan hemen sonra gelen cümle biçimli ilk ara başlık aslında giriş cümlesidir.
    if len(gruplar) > 1 and _duz_uzunluk(gruplar[0].govde) < 200 and _cumle_mi(gruplar[1].baslik):
        alt = gruplar[1]
        gruplar[1] = _Grup("Giriş", "", [gruplar[0].govde, alt.baslik, *alt.bloklar])
        del gruplar[0]
    elif len(gruplar) > 1 and gruplar[0].baslik == varsayilan:
        gruplar[0].baslik = "Giriş"

    bolunmus: list[_Grup] = []
    for grup in gruplar:
        if _duz_uzunluk(grup.govde) <= limit or len(grup.bloklar) == 1:
            bolunmus.append(grup)
            continue
        kova: list[str] = []
        ilk = True
        for blok in grup.bloklar:
            if kova and _duz_uzunluk("\n\n".join([*kova, blok])) > limit:
                bolunmus.append(_alt_grup(grup, kova, ilk))
                kova, ilk = [], False
            kova.append(blok)
        if kova:
            bolunmus.append(_alt_grup(grup, kova, ilk))

    birlesik: list[_Grup] = []
    for grup in bolunmus:
        if not grup.govde.strip():
            continue
        if birlesik and _duz_uzunluk(grup.govde) < KISA_BOLUM:
            onceki = birlesik[-1]
            if _duz_uzunluk(onceki.govde) + _duz_uzunluk(grup.govde) <= limit:
                onceki.bloklar.append(f"{'#' * (ana + 1)} {grup.ham_baslik or grup.baslik}\n\n{grup.govde}")
                continue
        birlesik.append(grup)
    # Çok kısa ilk bölüm sonrakine katılır.
    if len(birlesik) > 1 and _duz_uzunluk(birlesik[0].govde) < KISA_BOLUM:
        ilk = birlesik.pop(0)
        birlesik[0].bloklar.insert(0, ilk.govde)
    return birlesik


def _cumle_mi(baslik: str) -> bool:
    return len(baslik) > 90 or (len(baslik) > 40 and baslik.rstrip().endswith((".", "!", "?")))


_GENEL_ALT = re.compile(r"^\W*(resources?|core (topics|concepts)|kaynaklar|further reading|references)\b", re.IGNORECASE)


def _alt_grup(grup: _Grup, bloklar: list[str], ilk: bool) -> _Grup:
    if ilk:
        return _Grup(grup.baslik, grup.ham_baslik, list(bloklar))
    alt = ""
    for blok in bloklar:
        eslesme = _BASLIK.match(blok.split("\n", 1)[0])
        aday = _baslik_metni(eslesme.group(2)) if eslesme else ""
        if aday and not _GENEL_ALT.search(aday):
            alt = aday
            break
    return _Grup(f"{grup.baslik} — {alt}" if alt else f"{grup.baslik} (devam)", "", list(bloklar))


def _uzun_bol(baslik: str, metin: str, limit: int, sayfa_url: str = "", ek_baglanti=None) -> list[YaziBolum]:
    duz_boy = max(1, _duz_uzunluk(metin))
    # Bağlantı adresleri sınıra sayılmasın diye ham metin orantılı daha geniş sınırla bölünür.
    parcalar = _paragraf_bol(metin, int(limit * max(1.0, len(metin) / duz_boy)))
    bolumler: list[YaziBolum] = []
    for sira, parca in enumerate(parcalar, start=1):
        duz, baglar = baglanti_ayikla(parca, sayfa_url)
        duz = re.sub(r"\n{3,}", "\n\n", duz).strip()
        if not duz:
            continue
        if sira == 1 and ek_baglanti:
            baglar = [*ek_baglanti, *(b for b in baglar if b.url not in {e.url for e in ek_baglanti})]
        ad = baslik if len(parcalar) == 1 else f"{baslik} ({sira})"
        bolumler.append(YaziBolum(ad, duz, baglar))
    return bolumler


def metni_bol(metin: str, limit: int, varsayilan_baslik: str = "Yazı", sayfa_url: str = "") -> list[YaziBolum]:
    """Ana başlık düzeyinde böler, kısa bölümleri birleştirir, tanıtım bölümlerini atar.

    Başlık yoksa veya bölüm uzunsa paragraftan böler. Bağlantılar bölüm bölüm ayrı tutulur.
    """
    metin = (metin or "").replace("\r\n", "\n").strip()
    if limit <= 0:
        limit = 6000
    varsayilan = varsayilan_baslik.strip() or "Yazı"
    parcalar = _tanitimi_at(_basliklara_ayir(metin, varsayilan))
    bolumler: list[YaziBolum] = []
    for grup in _grupla(parcalar, limit, varsayilan):
        _, baslik_baglari = baglanti_ayikla(grup.ham_baslik, sayfa_url)
        bolumler.extend(_uzun_bol(grup.baslik, grup.govde, limit, sayfa_url, baslik_baglari))
    return bolumler


def gurultu_temizle(metin: str) -> str:
    satirlar = [
        _VIKI_DUZENLE.sub("", satir) for satir in (metin or "").split("\n") if not _GURULTU.match(satir)
    ]
    paragraflar = [
        p
        for p in re.sub(r"\n{3,}", "\n\n", "\n".join(satirlar)).strip().split("\n\n")
        if not (len(p) < 800 and not p.lstrip().startswith("#") and _tanitim_paragrafi(p))
    ]
    while len(paragraflar) > 1:
        son = baglanti_ayikla(paragraflar[-1])[0]
        if len(son) > 400 or not _ALTBILGI.search(son):
            break
        paragraflar.pop()
    return "\n\n".join(paragraflar).strip()


def metin_yeterli(bolumler: list[YaziBolum]) -> bool:
    return sum(len(bolum.metin) for bolum in bolumler) >= EN_AZ_KARAKTER


def yazi_yukle(klasor: Path) -> tuple[YaziMeta, list[YaziBolum]] | None:
    meta_yol = klasor / META_ADI
    metin_yol = klasor / METIN_ADI
    if not meta_yol.exists() or not metin_yol.exists():
        return None
    meta = YaziMeta.from_dict(json.loads(meta_yol.read_text(encoding="utf-8")))
    veri = json.loads(metin_yol.read_text(encoding="utf-8"))
    bolumler = [YaziBolum.from_dict(oge) for oge in veri.get("bolumler") or []]
    if not bolumler:
        return None
    return meta, bolumler


def yazi_kaydet(klasor: Path, meta: YaziMeta, bolumler: list[YaziBolum]) -> None:
    klasor.mkdir(parents=True, exist_ok=True)
    (klasor / META_ADI).write_text(
        json.dumps(meta.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (klasor / METIN_ADI).write_text(
        json.dumps({"bolumler": [b.as_dict() for b in bolumler]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _yazar(deger: object) -> str:
    if isinstance(deger, list):
        return ", ".join(str(oge).strip() for oge in deger if str(oge).strip())
    return str(deger or "").strip()


def _tarih(deger: object) -> str | None:
    if not deger:
        return None
    eslesme = _TARIH.search(str(deger))
    return eslesme.group(0) if eslesme else None


def _site(ust: dict) -> str:
    site = str(ust.get("sitename") or "").strip()
    if site:
        return site
    host = str(ust.get("hostname") or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


@dataclass
class _Sayfa:
    url: str
    baslik: str
    yazar: str
    site: str
    tarih: str | None
    govde: str
    kismi: bool


def _cerezler(settings: Settings, url: str, log=None):
    """Kullanıcı açıkça bir tarayıcı seçtiyse o tarayıcının bu alan adına ait çerezleri (ör. Medium üyeliği).

    Ayar biçimi: "chrome" veya profil ile "chrome:Profile 1". Çerezler yalnızca yazının alan adı için
    okunur ve yalnızca o sayfa isteğinde kullanılır.
    """
    ayar = (settings.cookie_browser or "").strip()
    if not ayar:
        return None
    tarayici, _, profil = ayar.partition(":")
    tarayici = tarayici.strip().lower()
    try:
        from urllib.parse import urlsplit

        import httpx
        from yt_dlp.cookies import extract_cookies_from_browser

        alan = urlsplit(url).netloc.lower().removeprefix("www.")
        kok = ".".join(alan.split(".")[-2:])
        kavanoz = extract_cookies_from_browser(tarayici, profil.strip() or None)
        cerez = httpx.Cookies()
        adlar = set()
        for c in kavanoz:
            if c.domain.lstrip(".").endswith(kok):
                cerez.set(c.name, c.value, domain=c.domain, path=c.path)
                adlar.add(c.name)
    except Exception as hata:
        if log:
            log(f"Uyarı: {tarayici} çerezleri okunamadı ({type(hata).__name__}: {str(hata)[:120]})")
        return None
    if log:
        if not adlar:
            log(f"Uyarı: {tarayici} içinde {kok} çerezi yok; tarayıcıda bu siteye giriş yapılmamış olabilir")
        elif kok == "medium.com" and "sid" not in adlar:
            log("Uyarı: Medium çerezleri var ama oturum çerezi (sid) yok; Chrome'da Medium'a giriş yapın")
        else:
            log(f"{tarayici} çerezleri kullanılıyor ({len(adlar)} çerez, {kok})")
    return cerez if adlar else None


def akis_birlestir(html: str) -> str:
    """React akışlı çizimi: gizli <div hidden id="S:n"> içeriğini <template id="P:n"> yerine taşır.

    Tarayıcı bunu JavaScript ile yapar; yapılmazsa metin çıkarıcı yazının sonunu görmez.
    """
    if 'hidden id="S:' not in html or 'template id="P:' not in html:
        return html
    try:
        import lxml.html

        kok = lxml.html.document_fromstring(html)
    except Exception:
        return html
    gizliler = {el.get("id")[2:]: el for el in kok.xpath('//div[@hidden][starts-with(@id, "S:")]')}
    # İç içe yer tutucular olabileceği için birkaç tur dönülür.
    for _ in range(5):
        degisti = False
        for sablon in kok.xpath('//template[starts-with(@id, "P:")]'):
            gizli = gizliler.get(sablon.get("id")[2:])
            if gizli is None or sablon.getparent() is None:
                continue
            ebeveyn = sablon.getparent()
            yer = ebeveyn.index(sablon)
            for sira, cocuk in enumerate(list(gizli)):
                ebeveyn.insert(yer + sira, cocuk)
            ebeveyn.remove(sablon)
            degisti = True
        if not degisti:
            break
    for gizli in gizliler.values():
        if gizli.getparent() is not None and len(gizli) == 0:
            gizli.getparent().remove(gizli)
    return lxml.html.tostring(kok, encoding="unicode")


def html_hazirla(html: str, taban_url: str = "") -> str:
    """Metin çıkarıcının attığı içerikleri kurtarır ve görsel altyazılarını atar.

    Medium bağlantı kartları (<a><h2>başlık</h2><h3>açıklama</h3></a>) düz paragrafa çevrilir;
    yoksa yazıdaki kaynak listeleri boş kalır.
    """
    html = akis_birlestir(html)
    if not any(iz in html for iz in ("<figcaption", "<h3", "<img")):
        return html
    try:
        import lxml.html
        from lxml.etree import SubElement

        kok = lxml.html.document_fromstring(html)
    except Exception:
        return html
    for kart in kok.xpath("//a[@href][.//h2][.//h3]"):
        ust = kart.getparent()
        if ust is None:
            continue
        baslik = " ".join(kart.xpath("string(.//h2)").split())
        aciklama = " ".join(kart.xpath("string(.//h3)").split())
        if not baslik:
            continue
        paragraf = lxml.html.Element("p")
        bag = SubElement(paragraf, "a")
        bag.set("href", urljoin(taban_url, kart.get("href")))
        bag.text = baslik
        # Bağlantı dışı metin olmazsa çıkarıcı paragrafı menü sanıp atıyor.
        bag.tail = f": {aciklama}" if aciklama else " (bağlantı)"
        hedef = ust if len(ust) == 1 and ust.getparent() is not None else kart
        hedef.getparent().replace(hedef, paragraf)
    for altyazi in kok.xpath("//figcaption"):
        altyazi.drop_tree()
    # Görselin hemen altındaki kısa, cümle olmayan paragraf da altyazıdır.
    for gorsel in kok.xpath("//p[img][not(normalize-space())]"):
        sonraki = gorsel.getnext()
        if sonraki is None or sonraki.tag != "p" or sonraki.xpath(".//img"):
            continue
        yazi = " ".join(sonraki.text_content().split())
        if yazi and len(yazi) < 100 and not re.search(r"[.!?:]$", yazi):
            sonraki.drop_tree()
    return lxml.html.tostring(kok, encoding="unicode")


def _sayfa_oku(url: str, *, cerez=None, zaman_asimi: float = 30) -> _Sayfa:
    import httpx
    import trafilatura

    try:
        with httpx.Client(
            timeout=zaman_asimi, follow_redirects=True, headers={"User-Agent": TARAYICI}, cookies=cerez
        ) as istemci:
            yanit = istemci.get(url)
            if yanit.status_code in (401, 403, 429):
                # Bazı siteler tarayıcı taklidini reddeder ama kendini tanıtan istemciye açıktır.
                yanit = istemci.get(url, headers={"User-Agent": ISTEMCI})
    except Exception as hata:
        raise YaziHatasi(f"Sayfa açılamadı: {hata}") from hata
    if yanit.status_code >= 400 or not yanit.text:
        raise YaziHatasi(f"Sayfa okunamadı ({yanit.status_code}).")
    html = html_hazirla(yanit.text, str(yanit.url))
    try:
        belge = trafilatura.bare_extraction(html, url=str(yanit.url), with_metadata=True, include_comments=False)
    except Exception as hata:
        raise YaziHatasi(f"Yazı metni çıkarılamadı: {hata}") from hata
    if belge is None:
        raise YaziHatasi("Sayfada yazı metni bulunamadı.")
    ust = belge.as_dict() if hasattr(belge, "as_dict") else belge
    if not isinstance(ust, dict):
        raise YaziHatasi("Sayfada yazı metni bulunamadı.")
    duz = str(ust.get("text") or "").strip()
    try:
        markdown = trafilatura.extract(
            html,
            url=str(yanit.url),
            output_format="markdown",
            include_comments=False,
            include_links=True,
        ) or ""
    except Exception:
        markdown = ""
    govde = gurultu_temizle(markdown.strip() if _duz_uzunluk(markdown) >= 200 else duz)
    return _Sayfa(
        url=str(yanit.url),
        baslik=str(ust.get("title") or "").strip() or "Başlıksız",
        yazar=_yazar(ust.get("author")),
        site=_site(ust),
        tarih=_tarih(ust.get("date")),
        govde=govde,
        kismi=kismi_mi(yanit.text),
    )


def _norm(metin: str) -> str:
    return re.sub(r"\W+", " ", (metin or "").lower()).strip()


def kopya_uygun(asil: _Sayfa, aday: _Sayfa) -> bool:
    """Aday sayfa aynı yazının daha uzun bir kopyası mı? Başlık, yazar ve görünen metin eşleşmeli."""
    asil_duz, aday_duz = baglanti_ayikla(asil.govde)[0], baglanti_ayikla(aday.govde)[0]
    if len(aday_duz) < max(len(asil_duz) * 1.3, len(asil_duz) + 500):
        return False
    b1, b2 = _norm(asil.baslik), _norm(aday.baslik)
    if not b1 or not (b1 == b2 or b1 in b2 or b2 in b1):
        return False
    # Yalnızca yazarın kendi yayımladığı kopya: yazar biliniyorsa adayda da aynı yazar görünmeli.
    if asil.yazar:
        y1, y2 = _norm(asil.yazar), _norm(aday.yazar)
        if not y2 or (y1 not in y2 and y2 not in y1):
            return False
    # Görünen kısmın başı adayda da geçmeli.
    paragraflar = [p for p in asil_duz.split("\n") if len(p.strip()) > 60 and not p.lstrip().startswith("#")]
    if paragraflar:
        parmak = _norm(paragraflar[0])[:80]
        if parmak and parmak not in _norm(aday_duz):
            return False
    return True


def tam_metin_ara(asil: _Sayfa, settings: Settings) -> _Sayfa | None:
    """Kesik yazının tamamını, yazarın kendi yayımladığı ücretsiz bir kopyada arar."""
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from urllib.parse import urlsplit

    from videotoinfo.search import make_searcher

    try:
        arama = make_searcher(replace(settings, search_region="wt-wt"))
        sonuclar = arama.ara(f'"{asil.baslik}"', 8)
    except Exception:
        return None
    asil_alan = urlsplit(asil.url).netloc.lower()
    adaylar = []
    for sonuc in sonuclar:
        alan = urlsplit(sonuc.url).netloc.lower()
        if alan == asil_alan or any(e in alan for e in _ENGELLI):
            continue
        b1, b2 = _norm(asil.baslik), _norm(sonuc.baslik)
        if b1 and (b1 in b2 or b2.rstrip(" .") in b1):
            adaylar.append(sonuc.url)
    if not adaylar:
        return None

    def oku(url: str) -> _Sayfa | None:
        try:
            return _sayfa_oku(url, zaman_asimi=12)
        except YaziHatasi:
            return None

    with ThreadPoolExecutor(max_workers=4) as havuz:
        sayfalar = [s for s in havuz.map(oku, adaylar[:5]) if s is not None]
    uygun = [s for s in sayfalar if not s.kismi and kopya_uygun(asil, s)]
    return max(uygun, key=lambda s: len(s.govde)) if uygun else None


def _indir(url: str, settings: Settings, yazi_id: str, log=None) -> tuple[YaziMeta, list[YaziBolum]]:
    sayfa = _sayfa_oku(url, cerez=_cerezler(settings, url, log))
    if log and settings.cookie_browser.strip():
        log("Yazı abonelikle tam alındı" if not sayfa.kismi else "Uyarı: çerezle de yalnızca önizleme geldi")
    tam_url = ""
    if (sayfa.kismi or len(sayfa.govde) < EN_AZ_KARAKTER) and settings.alt_source:
        if log:
            log("Yazı kesik; yazarın ücretsiz bir kopyası aranıyor")
        kopya = tam_metin_ara(sayfa, settings)
        if kopya is not None:
            if log:
                log(f"Tam metin bulundu: {kopya.url}")
            tam_url = kopya.url
            sayfa = _Sayfa(
                url=sayfa.url,
                baslik=sayfa.baslik,
                yazar=sayfa.yazar or kopya.yazar,
                site=sayfa.site,
                tarih=sayfa.tarih or kopya.tarih,
                govde=kopya.govde,
                kismi=False,
            )
        elif log:
            log("Ücretsiz tam metin bulunamadı")
    bolumler = metni_bol(sayfa.govde, settings.section_chars, sayfa.baslik, tam_url or sayfa.url)
    if not metin_yeterli(bolumler):
        raise YaziHatasi("Yazı çok kısa veya sayfa metni alınamadı.")
    duz = "\n\n".join(bolum.metin for bolum in bolumler)
    kelime = len(re.findall(r"\S+", duz))
    meta = YaziMeta(
        yazi_id=yazi_id,
        url=temiz_url(url),
        baslik=sayfa.baslik,
        yazar=sayfa.yazar,
        site=sayfa.site,
        yayin_tarihi=sayfa.tarih,
        kelime=kelime,
        kismi=sayfa.kismi,
        tam_metin_url=tam_url,
        dil=dil_tespit(duz),
    )
    return meta, bolumler


def getir(url: str, settings: Settings, *, bastan: bool = False, log=None) -> tuple[YaziMeta, list[YaziBolum]]:
    kimlik = yazi_id_uret(url)
    klasor = yazi_klasoru(settings, kimlik)
    yeniden = False
    if not bastan:
        yuklu = yazi_yukle(klasor)
        if yuklu is not None:
            # Önceden kesik alınmış yazı için tam metin bir kez daha aranır.
            if not (yuklu[0].kismi and settings.alt_source):
                return yuklu
            yeniden = True
    meta, bolumler = _indir(url, settings, kimlik, log)
    if yeniden and meta.kismi:
        return yuklu
    yazi_kaydet(klasor, meta, bolumler)
    if bastan or yeniden:
        for ad in ("summary.json", "factcheck.json"):
            yol = klasor / ad
            if yol.exists():
                yol.unlink()
    return meta, bolumler
