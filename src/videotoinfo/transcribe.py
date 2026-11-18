"""mlx-whisper ile zaman damgalı transkript."""

from __future__ import annotations

import json
from pathlib import Path

from videotoinfo.config import Settings
from videotoinfo.models import Segment, Transcript

DOSYA = "transcript.json"


def transkript_yolu(klasor: Path) -> Path:
    return klasor / DOSYA


def transkript_yukle(klasor: Path) -> Transcript | None:
    yol = transkript_yolu(klasor)
    if not yol.exists():
        return None
    return Transcript.from_dict(json.loads(yol.read_text(encoding="utf-8")))


def transkript_kaydet(klasor: Path, transcript: Transcript) -> None:
    transkript_yolu(klasor).write_text(
        json.dumps(transcript.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def yazilastir(ses: Path, settings: Settings) -> Transcript:
    import mlx_whisper

    if not ses.exists() or ses.stat().st_size == 0:
        raise RuntimeError(f"Ses dosyası yok: {ses}")
    sonuc = mlx_whisper.transcribe(
        str(ses),
        path_or_hf_repo=settings.whisper_model,
        verbose=False,
        condition_on_previous_text=False,
    )
    segmentler: list[Segment] = []
    for seg in sonuc.get("segments") or []:
        metin = str(seg.get("text") or "").strip()
        if not metin:
            continue
        segmentler.append(
            Segment(start=float(seg.get("start") or 0), end=float(seg.get("end") or 0), text=metin)
        )
    if not segmentler:
        raise RuntimeError("Transkript boş. Videoda konuşma olmayabilir.")
    return Transcript(dil=str(sonuc.get("language") or ""), segmentler=segmentler)
