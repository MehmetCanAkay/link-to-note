"""Ollama sohbet çağrıları. Bağlam uzunluğu her çağrıda açıkça verilir."""

from __future__ import annotations

import functools
import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from videotoinfo.config import Settings
from videotoinfo.textutil import think_temizle

SYSTEM = (
    "Sen dikkatli bir editörsün. Yalnızca verilen metne dayanırsın. "
    "Çıktıyı istemde belirtilen dilde yazarsın. "
    "Bilgi uydurmazsın. İstenen biçimin dışına çıkmazsın. "
    "Düşünce sürecini yanıta yazmazsın."
)


class BaglamAsildi(RuntimeError):
    pass


def think_degeri(ayar: str) -> bool | str:
    ham = str(ayar).strip().lower()
    if ham in {"false", "0", "yok", "kapali", "off", "hayir"}:
        return False
    if ham in {"true", "1", "on", "evet"}:
        return True
    return ham


def model_degistir(onceki: Settings, sonraki: Settings) -> None:
    """İki görev farklı model kullanıyorsa öncekini bellekten çıkarır (36 GB'ta ikisi sığmaz)."""
    if onceki.model != sonraki.model:
        unload_model(onceki.model)


def unload_model(model: str) -> None:
    subprocess.run(["ollama", "stop", model], capture_output=True, text=True, check=False)


def _langfuse() -> Any:
    """İki anahtar da doluysa istemci. Biri eksikse veya istemci açılmazsa None; import edilmez."""
    if not os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip() or not os.environ.get("LANGFUSE_SECRET_KEY", "").strip():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        return None


def _alan(nesne: Any, ad: str) -> Any:
    if nesne is None:
        return None
    if isinstance(nesne, dict):
        return nesne.get(ad)
    return getattr(nesne, ad, None)


def _olcum(yanit: Any) -> tuple[dict[str, int], dict[str, Any]]:
    kullanim: dict[str, int] = {}
    girdi = _alan(yanit, "prompt_eval_count")
    cikti = _alan(yanit, "eval_count")
    if isinstance(girdi, int):
        kullanim["input"] = girdi
    if isinstance(cikti, int):
        kullanim["output"] = cikti
    meta: dict[str, Any] = {}
    for ad in ("load_duration", "prompt_eval_duration", "eval_duration"):
        ns = _alan(yanit, ad)
        if isinstance(ns, (int, float)):
            meta[ad] = round(ns / 1_000_000, 1)
    eval_ns = _alan(yanit, "eval_duration")
    if isinstance(cikti, int) and isinstance(eval_ns, (int, float)) and eval_ns > 0:
        meta["token_sn"] = round(cikti / (eval_ns / 1e9), 1)
    thinking = _alan(_alan(yanit, "message"), "thinking")
    if isinstance(thinking, str) and thinking.strip():
        meta["thinking"] = thinking
    return kullanim, meta


def _kaydi_kapat(
    gen: Any,
    *,
    yanit: Any = None,
    cikti: str | None = None,
    seviye: str | None = None,
    durum: str | None = None,
    ilk_token: Any = None,
) -> None:
    """Langfuse hatası model çağrısına sızmaz."""
    if gen is None:
        return
    try:
        guncel: dict[str, Any] = {}
        if cikti is not None:
            guncel["output"] = cikti
        if yanit is not None:
            kullanim, meta = _olcum(yanit)
            if kullanim:
                guncel["usage_details"] = kullanim
            if meta:
                guncel["metadata"] = meta
        if seviye:
            guncel["level"] = seviye
        if durum:
            guncel["status_message"] = durum
        if ilk_token is not None:
            guncel["completion_start_time"] = ilk_token
        if guncel:
            gen.update(**guncel)
    except Exception:
        pass
    try:
        gen.end()
    except Exception:
        pass


@functools.cache
def istem_surumu() -> str:
    """prompts/*.txt içeriğinin özeti; Langfuse'ta prompt değişikliği öncesi/sonrası ayrılsın diye."""
    ozet = hashlib.sha256()
    for yol in sorted((Path(__file__).parent / "prompts").glob("*.txt")):
        ozet.update(yol.name.encode() + yol.read_bytes())
    return ozet.hexdigest()[:8]


def _generation_ac(ad: str, model: str, mesajlar: list[dict[str, str]], parametreler: dict[str, Any]) -> Any:
    istemci = _langfuse()
    if istemci is None:
        return None
    try:
        return istemci.start_observation(
            as_type="generation",
            name=ad,
            model=model,
            input=mesajlar,
            model_parameters=parametreler,
            version=istem_surumu(),
        )
    except Exception:
        return None


def _cm_kapat(cm: Any, exc: tuple | None = None) -> None:
    if cm is None:
        return
    try:
        cm.__exit__(*(exc or (None, None, None)))
    except Exception:
        pass


def _flush(istemci: Any) -> None:
    try:
        istemci.flush()
    except Exception:
        pass


@contextmanager
def is_izi(ad: str, kimlik: str, girdi: dict[str, Any]):
    """İş span'ı. Kayıt açılamazsa gövde yine bir kez çalışır; bitince flush."""
    istemci = _langfuse()
    if istemci is None:
        yield
        return
    ozellik = None
    span = None
    ozellik_acik = False
    span_acik = False
    try:
        from langfuse import propagate_attributes

        ozellik = propagate_attributes(session_id=kimlik, trace_name=ad)
        ozellik.__enter__()
        ozellik_acik = True
        span = istemci.start_as_current_observation(name=ad, as_type="span", input=girdi)
        span.__enter__()
        span_acik = True
    except Exception:
        # Kayıt açılamadı: gövde kayıtsız çalışır; yield except dışında kalsın ki iş hatasına Langfuse hatası zincirlenmesin.
        if span_acik:
            _cm_kapat(span)
        if ozellik_acik:
            _cm_kapat(ozellik)
        span = ozellik = None
    try:
        yield
    except BaseException:
        bilgi = sys.exc_info()
        _cm_kapat(span, bilgi)
        _cm_kapat(ozellik, bilgi)
        raise
    else:
        _cm_kapat(span)
        _cm_kapat(ozellik)
    finally:
        _flush(istemci)


def _icerik(yanit: Any) -> tuple[str, int]:
    mesaj = getattr(yanit, "message", None)
    if mesaj is None and isinstance(yanit, dict):
        mesaj = yanit.get("message")
    if mesaj is None:
        raise RuntimeError("Model yanıtı boş")
    if hasattr(mesaj, "content"):
        metin = mesaj.content or ""
    else:
        metin = (mesaj.get("content") or "") if isinstance(mesaj, dict) else ""
    token = getattr(yanit, "prompt_eval_count", None)
    if token is None and isinstance(yanit, dict):
        token = yanit.get("prompt_eval_count")
    return think_temizle(metin), int(token or 0)


def chat(
    settings: Settings,
    kullanici: str,
    *,
    sistem: str | None = None,
    temperature: float | None = None,
    raise_on_truncate: bool = True,
    num_predict: int | None = None,
    json_bicim: bool = False,
    iz: str = "chat",
) -> tuple[str, int]:
    import ollama

    mesajlar: list[dict[str, str]] = []
    if sistem is not None:
        mesajlar.append({"role": "system", "content": sistem})
    mesajlar.append({"role": "user", "content": kullanici})
    secenekler = {
        "num_ctx": settings.num_ctx,
        "temperature": settings.temperature if temperature is None else temperature,
        "num_predict": settings.num_predict if num_predict is None else num_predict,
        "seed": 42,
    }
    ortak = {
        "model": settings.model,
        "messages": mesajlar,
        "options": secenekler,
        "keep_alive": settings.keep_alive,
    }
    if json_bicim:
        ortak["format"] = "json"
    parametreler: dict[str, Any] = {
        "num_ctx": secenekler["num_ctx"],
        "temperature": secenekler["temperature"],
        "num_predict": secenekler["num_predict"],
        "think": think_degeri(settings.think),
    }
    if json_bicim:
        parametreler["format"] = "json"
    gen = _generation_ac(iz, settings.model, mesajlar, parametreler)
    try:
        try:
            yanit = ollama.chat(**ortak, think=think_degeri(settings.think))
        except TypeError:
            yanit = ollama.chat(**ortak)
        metin, token = _icerik(yanit)
    except Exception as hata:
        _kaydi_kapat(gen, seviye="ERROR", durum=str(hata))
        raise
    if raise_on_truncate and token >= max(settings.num_ctx - 32, 1) and token > 0:
        mesaj = f"İstem bağlama sığmadı ({token} token, sınır {settings.num_ctx})."
        _kaydi_kapat(gen, yanit=yanit, cikti=metin, seviye="ERROR", durum=mesaj)
        raise BaglamAsildi(mesaj)
    if not metin:
        _kaydi_kapat(gen, yanit=yanit, cikti=metin, seviye="ERROR", durum="Model boş yanıt verdi")
        raise RuntimeError("Model boş yanıt verdi")
    _kaydi_kapat(gen, yanit=yanit, cikti=metin)
    return metin, token


def ensure_model(settings: Settings) -> None:
    import ollama

    try:
        liste = ollama.list()
    except Exception as hata:
        raise RuntimeError(
            "Ollama'ya ulaşılamadı. Uygulamanın açık olduğundan emin olun."
        ) from hata
    modeller = getattr(liste, "models", None)
    if modeller is None and isinstance(liste, dict):
        modeller = liste.get("models") or []
    adlar = []
    for model in modeller or []:
        ad = getattr(model, "model", None) or (model.get("model") if isinstance(model, dict) else None)
        if not ad:
            ad = getattr(model, "name", None) or (model.get("name") if isinstance(model, dict) else None)
        if ad:
            adlar.append(ad)
    if settings.model not in adlar:
        raise RuntimeError(
            f"Model bulunamadı: {settings.model}. Kurulu modeller: {', '.join(adlar) or 'yok'}"
        )


def measure_context(settings: Settings, hedef_token: int = 6000) -> int:
    """Uzun bir istem gönderip prompt_eval_count döndürür.

    Ollama varsayılanı genelde 4096'dır. Dönüş bunun üstündeyse num_ctx uygulanmıştır.
    """
    ensure_model(settings)
    govde = "kelime " * (hedef_token * 2)
    metin, token = chat(
        settings,
        "Aşağıdaki metni okuma. Yalnızca tamam yaz.\n\n" + govde,
        sistem="Tek kelimeyle yanıt ver.",
        temperature=0,
        raise_on_truncate=False,
        iz="olcum",
    )
    if token <= 0:
        raise RuntimeError(f"Bağlam ölçülemedi. Model yanıtı: {metin[:200]}")
    return token
