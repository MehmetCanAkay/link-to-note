"""Sesi indirir ve video üst bilgisini alır."""

from __future__ import annotations

import json
import re
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.models import VideoMeta
from videotoinfo.textutil import guvenli_id

META_ADI = "meta.json"


class IndirmeHatasi(RuntimeError):
    pass


def video_klasoru(settings: Settings, video_id: str) -> Path:
    klasor = settings.data_dir / video_id
    klasor.mkdir(parents=True, exist_ok=True)
    return klasor


def meta_yukle(klasor: Path) -> VideoMeta | None:
    yol = klasor / META_ADI
    if not yol.exists():
        return None
    return VideoMeta.from_dict(json.loads(yol.read_text(encoding="utf-8")))


def meta_kaydet(klasor: Path, meta: VideoMeta) -> None:
    (klasor / META_ADI).write_text(
        json.dumps(meta.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _tarih(upload_date: str | None) -> str | None:
    if not upload_date or not re.fullmatch(r"\d{8}", upload_date):
        return None
    return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def _platform(extractor: str | None) -> str:
    return (extractor or "diger").strip().lower() or "diger"


def _ses_dosyasi(klasor: Path) -> Path | None:
    adaylar = [
        yol
        for yol in klasor.glob("ses.*")
        if yol.suffix.lower() not in {".json", ".part", ".ytdl"} and yol.stat().st_size > 0
    ]
    if not adaylar:
        return None
    return max(adaylar, key=lambda yol: yol.stat().st_mtime)


def _meta_from_info(info: dict, ses_adi: str) -> VideoMeta:
    kaynak_id = str(info.get("id") or "video")
    platform = _platform(info.get("extractor_key") or info.get("extractor"))
    baslik = re.sub(r"\s+", " ", str(info.get("title") or "Başlıksız")).strip()
    kanal = str(info.get("channel") or info.get("uploader") or info.get("creator") or "").strip()
    return VideoMeta(
        video_id=guvenli_id(f"{platform}_{kaynak_id}"),
        kaynak_id=kaynak_id,
        url=str(info.get("webpage_url") or info.get("original_url") or ""),
        baslik=baslik,
        kanal=kanal,
        platform=platform,
        yayin_tarihi=_tarih(info.get("upload_date")),
        sure_saniye=int(info.get("duration") or 0),
        ses_dosyasi=ses_adi,
        aciklama=str(info.get("description") or "").strip(),
        kapak=str(info.get("thumbnail") or ""),
    )


def indir(url: str, settings: Settings) -> VideoMeta:
    import yt_dlp

    on = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(on) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as hata:
        raise IndirmeHatasi(f"Video bilgisi alınamadı: {hata}") from hata
    if not info:
        raise IndirmeHatasi("Video bilgisi boş döndü.")
    if info.get("_type") == "playlist":
        girdiler = [g for g in (info.get("entries") or []) if g]
        if not girdiler:
            raise IndirmeHatasi("Oynatma listesinde video bulunamadı.")
        info = girdiler[0]
    if info.get("is_live"):
        raise IndirmeHatasi("Canlı yayın indirilmiyor.")

    taslak = _meta_from_info(info, "")
    klasor = video_klasoru(settings, taslak.video_id)
    mevcut_ses = _ses_dosyasi(klasor)
    if mevcut_ses is not None:
        kayitli = meta_yukle(klasor)
        if kayitli is not None:
            if (not kayitli.aciklama and taslak.aciklama) or (not kayitli.kapak and taslak.kapak):
                kayitli.aciklama = kayitli.aciklama or taslak.aciklama
                kayitli.kapak = kayitli.kapak or taslak.kapak
                meta_kaydet(klasor, kayitli)
            return kayitli
        taslak.ses_dosyasi = mevcut_ses.name
        meta_kaydet(klasor, taslak)
        return taslak

    secenekler = {
        "format": "bestaudio/best",
        "outtmpl": str(klasor / "ses.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": True,
        "retries": 3,
        "concurrent_fragment_downloads": 4,
    }
    try:
        with yt_dlp.YoutubeDL(secenekler) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as hata:
        raise IndirmeHatasi(f"Ses indirilemedi: {hata}") from hata

    ses = _ses_dosyasi(klasor)
    if ses is None:
        raise IndirmeHatasi("İndirme bitti ama ses dosyası bulunamadı.")
    taslak.ses_dosyasi = ses.name
    if not taslak.url:
        taslak.url = url
    meta_kaydet(klasor, taslak)
    return taslak
