"""Arama sağlayıcıları. Varsayılan ddgs anahtar istemez."""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from videotoinfo.config import Settings


@dataclass
class SearchResult:
    baslik: str
    url: str
    ozet: str
    # Doğrulanan yazının kendi verdiği bağlantı (arama sonucu değil).
    yazidan: bool = False


def baslik_temizle(baslik: str) -> str:
    """Bazı motorlar birkaç başlığı "...Başlık2...Başlık3" diye yapıştırır; ilki tutulur."""
    baslik = " ".join((baslik or "").split())
    ilk = baslik
    if len(baslik) > 100:
        parca = re.split(r"(?:\.\.\.|…)(?=[A-Z0-9\[])", baslik, maxsplit=1)[0]
        if len(parca) >= 25 and parca != baslik:
            ilk = parca.rstrip() + "…"
    return ilk if len(ilk) <= 160 else ilk[:157].rstrip() + "…"


class Arama:
    def ara(self, sorgu: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class DdgsArama(Arama):
    """Ücretsiz motorlar kararsız: bir motor bazen 1 sn'de yanıt verir, bazen zaman aşımına düşer.
    Bu yüzden motorlar aynı anda sorgulanır, yeterli sonuç veren ilk yanıt kullanılır."""

    MOTOR_SURESI = 7
    TOPLAM_SURE = 12

    def __init__(self, bolge: str, backend: str) -> None:
        self.bolge = bolge
        self.backend = backend

    def _motor(self, backend: str, sorgu: str, limit: int) -> list[SearchResult]:
        from ddgs import DDGS

        try:
            istemci = DDGS(timeout=self.MOTOR_SURESI)
        except TypeError:
            istemci = DDGS()
        try:
            return _sonuclar(istemci.text(sorgu, region=self.bolge, max_results=limit, backend=backend))
        except Exception:
            return []

    def _yaris(self, istenen: list[str], sorgu: str, limit: int, sure: float) -> list[SearchResult]:
        yeterli = min(3, limit)
        havuz = ThreadPoolExecutor(max_workers=len(istenen))
        isler = [havuz.submit(self._motor, b, sorgu, limit) for b in istenen]
        bulunan: list[SearchResult] = []
        gorulen: set[str] = set()
        try:
            for gelecek in as_completed(isler, timeout=sure):
                for sonuc in gelecek.result():
                    if sonuc.url not in gorulen:
                        gorulen.add(sonuc.url)
                        bulunan.append(sonuc)
                if len(bulunan) >= yeterli:
                    break
        except FuturesTimeout:
            pass
        finally:
            havuz.shutdown(wait=False, cancel_futures=True)
        return bulunan[:limit]

    def ara(self, sorgu: str, limit: int) -> list[SearchResult]:
        istenen = [parca.strip() for parca in self.backend.split(",") if parca.strip()]
        if not istenen or istenen == ["auto"]:
            istenen = ["bing", "google", "yahoo"]
        bas = time.monotonic()
        bulunan = self._yaris(istenen, sorgu, limit, self.TOPLAM_SURE)
        kalan = self.TOPLAM_SURE - (time.monotonic() - bas)
        if not bulunan and kalan > 3:
            bulunan = self._yaris(istenen, sorgu, limit, kalan)
        return bulunan


class BraveArama(Arama):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def ara(self, sorgu: str, limit: int) -> list[SearchResult]:
        import httpx

        try:
            yanit = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": sorgu, "count": limit},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout=20,
            )
            yanit.raise_for_status()
            web = (yanit.json().get("web") or {}).get("results") or []
        except Exception:
            return []
        cikti: list[SearchResult] = []
        for oge in web:
            url = str(oge.get("url") or "")
            if not url:
                continue
            cikti.append(
                SearchResult(
                    baslik=baslik_temizle(str(oge.get("title") or url)),
                    url=url,
                    ozet=str(oge.get("description") or ""),
                )
            )
        return cikti


def _sonuclar(ham: object) -> list[SearchResult]:
    cikti: list[SearchResult] = []
    for oge in ham or []:
        if not isinstance(oge, dict):
            continue
        url = str(oge.get("href") or oge.get("url") or "")
        if not url:
            continue
        cikti.append(
            SearchResult(
                baslik=baslik_temizle(str(oge.get("title") or url)),
                url=url,
                ozet=str(oge.get("body") or oge.get("description") or ""),
            )
        )
    return cikti


def make_searcher(settings: Settings) -> Arama:
    saglayici = settings.search_provider.strip().lower()
    if saglayici in {"ddgs", "duckduckgo"}:
        return DdgsArama(settings.search_region, settings.search_backend)
    if saglayici == "brave":
        anahtar = os.environ.get("BRAVE_API_KEY", "").strip()
        if not anahtar:
            raise RuntimeError("Brave araması için BRAVE_API_KEY ortam değişkeni gerekli.")
        return BraveArama(anahtar)
    raise RuntimeError(f"Bilinmeyen arama sağlayıcısı: {settings.search_provider}")
