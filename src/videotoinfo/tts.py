"""Kısa özeti macOS sesiyle, özetin dilinde okur."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.textutil import konusma_metni

DOSYA = "ses.json"


def ozet_hash(kisa: str) -> str:
    return hashlib.sha256(kisa.encode("utf-8")).hexdigest()[:16]


def ses_kaydi(klasor: Path) -> dict | None:
    yol = klasor / DOSYA
    if not yol.exists():
        return None
    return json.loads(yol.read_text(encoding="utf-8"))


def ses_kaydet(klasor: Path, kayit: dict) -> None:
    (klasor / DOSYA).write_text(json.dumps(kayit, ensure_ascii=False, indent=2), encoding="utf-8")


GIRIS = {"tr": "Kısa özet.", "en": "Summary."}


def seslendir(metin: str, hedef: Path, ses: str, dil: str = "tr") -> Path:
    duz = konusma_metni(metin).strip()
    if not duz:
        raise RuntimeError("Seslendirilecek özet boş.")
    hedef.parent.mkdir(parents=True, exist_ok=True)
    okunan = GIRIS.get(dil, GIRIS["tr"]) + "\n" + duz
    with tempfile.TemporaryDirectory() as tmp:
        kok = Path(tmp)
        txt = kok / "ozet.txt"
        aiff = kok / "ozet.aiff"
        txt.write_text(okunan, encoding="utf-8")
        say = subprocess.run(
            ["say", "-v", ses, "-o", str(aiff), "-f", str(txt)],
            capture_output=True,
            text=True,
        )
        if say.returncode != 0:
            raise RuntimeError(
                f"'{ses}' sesi kullanılamadı. Yüklü sesler: say -v '?'\n{say.stderr.strip()}"
            )
        ff = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-c:a", "aac", "-b:a", "96k", str(hedef)],
            capture_output=True,
            text=True,
        )
        if ff.returncode != 0 or not hedef.exists():
            raise RuntimeError(f"Ses dosyası oluşturulamadı: {ff.stderr.strip()}")
    return hedef


def ses_hazirla(
    kisa: str,
    hedef: Path,
    klasor: Path,
    settings: Settings,
    *,
    zorla: bool = False,
    dil: str = "tr",
) -> Path:
    ozet = ozet_hash(kisa)
    kayit = ses_kaydi(klasor)
    if (
        not zorla
        and kayit
        and kayit.get("hash") == ozet
        and hedef.exists()
        and hedef.stat().st_size > 0
    ):
        return hedef
    seslendir(kisa, hedef, settings.voice_en if dil == "en" else settings.voice, dil)
    ses_kaydet(klasor, {"hash": ozet, "dosya": hedef.name})
    return hedef
