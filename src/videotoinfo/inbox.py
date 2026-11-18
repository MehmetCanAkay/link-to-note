"""Obsidian gelen kutusu: bekleyen linkler ve işlenen kayıtlar."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from videotoinfo.vault import _gorunen, ayir

GELEN_ADI = "Gelen kutusu.md"
SABLON = """# Gelen kutusu

Linkleri Bekleyenler altına, her satıra bir tane yapıştırın. Sonra terminalde `vti kuyruk` çalıştırın.

## Bekleyenler

## İşlenenler
"""
URL_RE = re.compile(r"https?://[^\s)>\]]+")
HATA_RE = re.compile(r"^\s+-\s+hata:")
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com", "twitch.tv")
DESTEKSIZ_HOSTS = ("twitter.com", "x.com", "t.co")


def hazirla(vault: Path) -> Path:
    yol = vault / GELEN_ADI
    if not yol.exists():
        yol.write_text(SABLON, encoding="utf-8")
    return yol


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_uygun(host: str, adaylar: tuple[str, ...]) -> bool:
    return any(host == ad or host.endswith("." + ad) for ad in adaylar)


def tur_belirle(url: str) -> str:
    """video, yazi veya desteklenmiyor döndürür."""
    parca = urlsplit((url or "").strip())
    if parca.path.lower().rstrip("/").endswith(".pdf"):
        return "desteklenmiyor"
    host = _host(url or "")
    if _host_uygun(host, DESTEKSIZ_HOSTS):
        return "desteklenmiyor"
    if _host_uygun(host, VIDEO_HOSTS):
        return "video"
    if not parca.scheme or not host:
        return "desteklenmiyor"
    return "yazi"


def _baslik_index(satirlar: list[str], ad: str) -> int | None:
    hedef = ad.strip().lower()
    for sira, satir in enumerate(satirlar):
        if satir.strip().lower() == hedef:
            return sira
    return None


def _garanti(metin: str) -> str:
    satirlar = metin.splitlines()
    if _baslik_index(satirlar, "## Bekleyenler") is None:
        metin = metin.rstrip() + "\n\n## Bekleyenler\n\n## İşlenenler\n"
        satirlar = metin.splitlines()
    if _baslik_index(satirlar, "## İşlenenler") is None:
        metin = metin.rstrip() + "\n\n## İşlenenler\n"
    if not metin.endswith("\n"):
        metin += "\n"
    return metin


def _bol(metin: str) -> tuple[list[str], list[str], list[str]]:
    metin = _garanti(metin)
    satirlar = metin.splitlines()
    bek = _baslik_index(satirlar, "## Bekleyenler")
    isl = _baslik_index(satirlar, "## İşlenenler")
    if bek is None or isl is None or isl < bek:
        raise RuntimeError("Gelen kutusu başlıkları okunamadı.")
    return satirlar[: bek + 1], satirlar[bek + 1 : isl], satirlar[isl:]


def _satir_url(satir: str) -> str | None:
    if HATA_RE.match(satir):
        return None
    eslesme = URL_RE.search(satir)
    if not eslesme:
        return None
    return eslesme.group(0).rstrip(".,;")


def _sonraki_hata(satirlar: list[str], index: int) -> int | None:
    sira = index + 1
    while sira < len(satirlar) and satirlar[sira].strip() == "":
        sira += 1
    if sira < len(satirlar) and HATA_RE.match(satirlar[sira]):
        return sira
    return None


def _birlestir(once: list[str], bekleyen: list[str], islenen: list[str]) -> str:
    def blok(satirlar: list[str]) -> str:
        return "\n".join(satirlar).strip("\n")

    parcalar = [blok(once), blok(bekleyen), blok(islenen)]
    return "\n\n".join(parca for parca in parcalar if parca) + "\n"


def _oku(yol: Path) -> str:
    if not yol.exists():
        return SABLON
    return yol.read_text(encoding="utf-8")


def bekleyen_urller(yol: Path) -> list[str]:
    if not yol.exists():
        return []
    _, bekleyen, _ = _bol(yol.read_text(encoding="utf-8"))
    urller: list[str] = []
    for satir in bekleyen:
        url = _satir_url(satir)
        if url and url not in urller:
            urller.append(url)
    return urller


def _url_satiri(satirlar: list[str], url: str) -> int | None:
    for sira, satir in enumerate(satirlar):
        if _satir_url(satir) == url:
            return sira
    return None


def _hata_metni(mesaj: str) -> str:
    temiz = " ".join(str(mesaj).split())
    if len(temiz) > 300:
        temiz = temiz[:300] + "…"
    return f"  - hata: {temiz}"


def hata_yaz(yol: Path, url: str, mesaj: str) -> None:
    once, bekleyen, islenen = _bol(_oku(yol))
    satir = _hata_metni(mesaj)
    yer = _url_satiri(bekleyen, url)
    if yer is None:
        bekleyen.extend(["", f"- [ ] {url}", satir])
    else:
        hata_yeri = _sonraki_hata(bekleyen, yer)
        if hata_yeri is None:
            bekleyen.insert(yer + 1, satir)
        else:
            bekleyen[hata_yeri] = satir
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(_birlestir(once, bekleyen, islenen), encoding="utf-8")


def tamamla(yol: Path, url: str, sonuc: str) -> None:
    once, bekleyen, islenen = _bol(_oku(yol))
    yer = _url_satiri(bekleyen, url)
    if yer is not None:
        sil = {yer}
        hata_yeri = _sonraki_hata(bekleyen, yer)
        if hata_yeri is not None:
            sil.add(hata_yeri)
        bekleyen = [satir for sira, satir in enumerate(bekleyen) if sira not in sil]
    kayit = sonuc.strip()
    if kayit and kayit not in "\n".join(islenen):
        islenen.insert(1, kayit)
    yol.write_text(_birlestir(once, bekleyen, islenen), encoding="utf-8")


def sonuc_satiri(baglanti: str, dogrulama: str, duzeltme: int, url: str) -> str:
    ek = f", {duzeltme} düzeltme" if duzeltme else ""
    return f"- [x] {baglanti} · {dogrulama}{ek} · [kaynak]({url})"


def _not_ozeti(yol: Path) -> tuple[str, str, int]:
    fm, govde = ayir(yol.read_text(encoding="utf-8"))
    baslik = _gorunen(str(fm.get("baslik") or yol.stem))
    bag = f"[[{yol.parent.name}/{yol.stem}|{baslik}]]"
    durum = str(fm.get("dogrulama") or "yapilmadi")
    sayi = govde.count("[!warning] Düzeltme")
    if sayi == 0:
        sayi = govde.count("- **Doğrusu:**")
    return bag, durum, sayi


def kuyrugu_isle(settings, *, dogrula_iste: bool = True, bastan: bool = False, log=print) -> int:
    from videotoinfo.pipeline import isle, yazi_isle
    from videotoinfo.vault import prepare_vault

    prepare_vault(settings.vault)
    yol = hazirla(settings.vault)
    urller = bekleyen_urller(yol)
    if not urller:
        log("Bekleyen bağlantı yok")
        return 0
    hatali = 0
    for url in urller:
        if url not in bekleyen_urller(yol):
            continue
        log(f"İşleniyor: {url}")
        try:
            tur = tur_belirle(url)
            if tur == "desteklenmiyor":
                raise RuntimeError("Twitter/X ve PDF henüz desteklenmiyor.")
            if tur == "video":
                not_yolu = isle(
                    url,
                    settings,
                    dogrula_iste=dogrula_iste,
                    ses=False,
                    bastan=bastan,
                    log=log,
                )
            else:
                not_yolu = yazi_isle(
                    url,
                    settings,
                    dogrula_iste=dogrula_iste,
                    bastan=bastan,
                    log=log,
                )
            bag, durum, sayi = _not_ozeti(not_yolu)
            tamamla(yol, url, sonuc_satiri(bag, durum, sayi, url))
            log(f"İşlendi: {not_yolu}")
        except Exception as hata:
            hatali += 1
            hata_yaz(yol, url, str(hata))
            log(f"Hata: {url}: {hata}")
    return hatali
