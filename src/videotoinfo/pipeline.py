"""İndir, yazıya dök, özetle, istenirse doğrula ve seslendir, nota yaz."""

from __future__ import annotations

from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.download import indir, meta_kaydet, video_klasoru
from videotoinfo.factcheck import dogrula, dogrula_parcalari, factcheck_kaydet, factcheck_yukle
from videotoinfo.llm import is_izi, model_degistir, unload_model
from videotoinfo.sohbet import nota_yaz
from videotoinfo.summarize import ozet_kaydet, ozet_yukle, ozetle, yazi_ozet_kaydet, yazi_ozet_yukle, yazi_ozetle
from videotoinfo.transcribe import transkript_kaydet, transkript_yukle, yazilastir
from videotoinfo.tts import ses_hazirla
from videotoinfo.textutil import DIL_ADI, dil_kodu, dilde_gorunuyor
from videotoinfo.vault import missing_links, note_stem, prepare_vault, write_archive, write_article
from videotoinfo.yazi import getir, yazi_klasoru

TURETILEN = ("transcript.json", "summary.json", "factcheck.json", "ses.json")


def _temizle(klasor: Path) -> None:
    for ad in TURETILEN:
        yol = klasor / ad
        if yol.exists():
            yol.unlink()


def isle(
    url: str,
    settings: Settings,
    *,
    dogrula_iste: bool = False,
    ses: bool = False,
    bastan: bool = False,
    log=print,
) -> Path:
    log("Video bilgisi alınıyor")
    meta = indir(url, settings)
    klasor = video_klasoru(settings, meta.video_id)
    with is_izi("video", meta.video_id, {"url": url, "dogrula": dogrula_iste, "ses": ses, "bastan": bastan}):
        return _video_govde(meta, klasor, settings, dogrula_iste=dogrula_iste, ses=ses, bastan=bastan, log=log)


def _video_govde(meta, klasor, settings: Settings, *, dogrula_iste: bool, ses: bool, bastan: bool, log) -> Path:
    if bastan:
        _temizle(klasor)
        log("Türetilmiş çıktılar silindi, ses dosyası duruyor")

    transcript = None if bastan else transkript_yukle(klasor)
    if transcript is None:
        log("Yazıya dökmeden önce dil modeli bellekten çıkarılıyor")
        for model in {settings.gorev(g).model for g in ("ozet", "iddia", "hukum", "sohbet")}:
            unload_model(model)
        log(f"Yazıya dökülüyor: {meta.baslik}")
        transcript = yazilastir(klasor / meta.ses_dosyasi, settings)
        transkript_kaydet(klasor, transcript)
        log(f"Transkript hazır ({transcript.dil}, {len(transcript.segmentler)} parça)")
    else:
        log("Transkript önbellekten kullanıldı")

    if meta.sure_saniye <= 0 and transcript.segmentler:
        meta.sure_saniye = int(round(transcript.segmentler[-1].end))
        meta_kaydet(klasor, meta)

    ozet = None if bastan else ozet_yukle(klasor)
    if ozet is None:
        ozet = ozetle(transcript, meta, settings, ilerleme=log)
        ozet_kaydet(klasor, ozet)
        log(f"Özet hazır ({DIL_ADI.get(ozet.dil, ozet.dil)}). Bağlam: {ozet.baglam_token}/{ozet.num_ctx} token")
        if not dilde_gorunuyor(ozet.kisa, ozet.dil):
            log(f"Uyarı: kısa özet {DIL_ADI.get(ozet.dil, ozet.dil)} görünmüyor.")
    else:
        log("Özet önbellekten kullanıldı")

    if dogrula_iste:
        model_degistir(settings.gorev("ozet"), settings.gorev("iddia"))
        iddialar = dogrula(
            transcript,
            settings,
            kaynak_id=meta.kaynak_id,
            kaynak_url=meta.url,
            yayin_tarihi=meta.yayin_tarihi,
            baslik=meta.baslik,
            ilerleme=log,
        )
        factcheck_kaydet(klasor, iddialar)
        log(f"Doğrulama bitti ({len(iddialar)} iddia)")
    else:
        iddialar = factcheck_yukle(klasor)

    prepare_vault(settings.vault)
    kok = note_stem(settings.vault, meta)
    ses_yolu = settings.vault / "Sesler" / f"{kok}.m4a"
    if ses:
        log(f"Kısa özet seslendiriliyor ({DIL_ADI.get(ozet.dil, ozet.dil)})")
        ses_hazirla(ozet.kisa, ses_yolu, klasor, settings, zorla=bastan, dil=ozet.dil)
    if not ses_yolu.exists():
        ses_yolu = None

    not_yolu = write_archive(settings, meta, transcript, ozet, iddialar, ses_yolu)
    eksik = missing_links(settings.vault)
    if eksik:
        log("Eksik bağlantılar: " + ", ".join(eksik))
    nota_yaz(settings, klasor)
    log(f"Not yazıldı: {not_yolu}")
    return not_yolu


def yazi_isle(
    url: str,
    settings: Settings,
    *,
    dogrula_iste: bool = True,
    bastan: bool = False,
    log=print,
) -> Path:
    log("Yazı alınıyor")
    meta, bolumler = getir(url, settings, bastan=bastan, log=log)
    klasor = yazi_klasoru(settings, meta.yazi_id)
    with is_izi("yazi", meta.yazi_id, {"url": url, "dogrula": dogrula_iste, "bastan": bastan}):
        return _yazi_govde(meta, bolumler, klasor, settings, dogrula_iste=dogrula_iste, bastan=bastan, log=log)


def _yazi_govde(meta, bolumler, klasor, settings: Settings, *, dogrula_iste: bool, bastan: bool, log) -> Path:
    if meta.kismi:
        log("Uyarı: yazının yalnızca görünen kısmı alındı")

    ozet = None if bastan else yazi_ozet_yukle(klasor)
    if ozet is None:
        ozet = yazi_ozetle(meta, bolumler, settings, ilerleme=log)
        yazi_ozet_kaydet(klasor, ozet)
        log(f"Özet hazır ({DIL_ADI.get(ozet.dil, ozet.dil)}). Bağlam: {ozet.baglam_token}/{ozet.num_ctx} token")
        if not dilde_gorunuyor(ozet.kisa, ozet.dil):
            log(f"Uyarı: kısa özet {DIL_ADI.get(ozet.dil, ozet.dil)} görünmüyor.")
    else:
        log("Özet önbellekten kullanıldı")

    if dogrula_iste:
        model_degistir(settings.gorev("ozet"), settings.gorev("iddia"))
        iddialar = dogrula_parcalari(
            [bolum.metin for bolum in bolumler],
            settings,
            kaynak_url=meta.url,
            haric_urller=(meta.tam_metin_url,) if meta.tam_metin_url else (),
            baslik=meta.baslik,
            yayin_tarihi=meta.yayin_tarihi,
            dil=dil_kodu(meta.dil) if meta.dil else "tr",
            cikti=ozet.dil,
            baglantilar=[bolum.baglantilar for bolum in bolumler],
            ilerleme=log,
        )
        factcheck_kaydet(klasor, iddialar)
        log(f"Doğrulama bitti ({len(iddialar)} iddia)")
    else:
        iddialar = factcheck_yukle(klasor)

    prepare_vault(settings.vault)
    not_yolu = write_article(settings, meta, ozet, iddialar)
    eksik = missing_links(settings.vault)
    if eksik:
        log("Eksik bağlantılar: " + ", ".join(eksik))
    nota_yaz(settings, klasor)
    log(f"Not yazıldı: {not_yolu}")
    return not_yolu
