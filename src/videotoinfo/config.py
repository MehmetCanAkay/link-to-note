"""config.toml okuma."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    vault: Path
    model: str = "qwen3.8:27b-mlx"
    num_ctx: int = 16384
    temperature: float = 0.3
    num_predict: int = 2048
    think: str = "false"
    keep_alive: str = "30m"
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    section_seconds: int = 600
    section_chars: int = 6000
    search_provider: str = "ddgs"
    search_region: str = "tr-tr"
    search_backend: str = "bing,google,yahoo"
    max_results: int = 5
    max_claims: int = 12
    page_chars: int = 3500
    voice: str = "Yelda"
    voice_en: str = "Samantha"
    # kaynak: İngilizce içerik İngilizce, diğerleri Türkçe özetlenir. tr veya en sabitler.
    summary_lang: str = "kaynak"
    # Görev başına model. Boşsa ana model kullanılır.
    model_ozet: str = ""
    model_iddia: str = ""
    model_hukum: str = ""
    model_sohbet: str = ""
    think_hukum: str = ""
    pack_chars: int = 12000
    alt_source: bool = True
    cookie_browser: str = ""

    def gorev(self, ad: str) -> "Settings":
        """Görev için model (ve hüküm için think) ayarı uygulanmış kopya."""
        from dataclasses import replace

        model = str(getattr(self, f"model_{ad}", "") or "").strip() or self.model
        think = self.think
        if ad == "hukum" and self.think_hukum.strip():
            think = self.think_hukum.strip()
        return replace(self, model=model, think=think)


def default_settings() -> Settings:
    return Settings(
        data_dir=Path("data"),
        vault=Path.home() / "Documents" / "VideoToInfo-Vault",
    )


def _pick(table: dict, key: str, fallback):
    if key in table and table[key] is not None:
        return table[key]
    return fallback


def config_path(explicit: Path | None = None) -> Path | None:
    """Ayar dosyası: verilen yol, yoksa VIDEOTOINFO_CONFIG, yoksa ./config.toml."""
    if explicit is not None:
        return explicit
    env = os.environ.get("VIDEOTOINFO_CONFIG", "").strip()
    if env:
        return Path(env)
    yerel = Path("config.toml")
    return yerel if yerel.exists() else None


def load_settings(path: Path | None = None) -> Settings:
    path = config_path(path)

    settings = default_settings()
    if path is None or not Path(path).exists():
        return _resolve_paths(settings)

    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    genel = data.get("genel") or {}
    ollama = data.get("ollama") or {}
    whisper = data.get("whisper") or {}
    arama = data.get("arama") or {}
    ses = data.get("ses") or {}
    yazi = data.get("yazi") or {}

    settings.data_dir = Path(str(_pick(genel, "veri_klasoru", settings.data_dir)))
    settings.vault = Path(str(_pick(genel, "vault", settings.vault)))
    settings.model = str(_pick(ollama, "model", settings.model))
    settings.num_ctx = int(_pick(ollama, "num_ctx", settings.num_ctx))
    settings.temperature = float(_pick(ollama, "temperature", settings.temperature))
    settings.num_predict = int(_pick(ollama, "num_predict", settings.num_predict))
    settings.think = str(_pick(ollama, "think", settings.think))
    settings.keep_alive = str(_pick(ollama, "keep_alive", settings.keep_alive))
    settings.whisper_model = str(_pick(whisper, "model", settings.whisper_model))
    settings.section_seconds = int(_pick(whisper, "bolum_saniye", settings.section_seconds))
    settings.section_chars = int(_pick(yazi, "bolum_karakter", settings.section_chars))
    settings.search_provider = str(_pick(arama, "saglayici", settings.search_provider))
    settings.search_region = str(_pick(arama, "bolge", settings.search_region))
    settings.search_backend = str(_pick(arama, "backend", settings.search_backend))
    settings.max_results = int(_pick(arama, "max_sonuc", settings.max_results))
    settings.max_claims = int(_pick(arama, "max_iddia", settings.max_claims))
    settings.page_chars = int(_pick(arama, "sayfa_karakter", settings.page_chars))
    settings.voice = str(_pick(ses, "ses", settings.voice))
    settings.voice_en = str(_pick(ses, "ses_en", settings.voice_en))
    settings.summary_lang = str(_pick(genel, "ozet_dili", settings.summary_lang)).strip().lower()
    settings.model_ozet = str(_pick(ollama, "model_ozet", settings.model_ozet))
    settings.model_iddia = str(_pick(ollama, "model_iddia", settings.model_iddia))
    settings.model_hukum = str(_pick(ollama, "model_hukum", settings.model_hukum))
    settings.model_sohbet = str(_pick(ollama, "model_sohbet", settings.model_sohbet))
    settings.think_hukum = str(_pick(ollama, "think_hukum", settings.think_hukum))
    settings.pack_chars = int(_pick(yazi, "paket_karakter", settings.pack_chars))
    settings.alt_source = bool(_pick(yazi, "alternatif_kaynak", settings.alt_source))
    settings.cookie_browser = str(_pick(yazi, "cerez_tarayici", settings.cookie_browser))
    return _resolve_paths(settings)


def _resolve_paths(settings: Settings) -> Settings:
    settings.vault = Path(settings.vault).expanduser()
    data = Path(settings.data_dir).expanduser()
    if not data.is_absolute():
        data = Path.cwd() / data
    settings.data_dir = data
    return settings
