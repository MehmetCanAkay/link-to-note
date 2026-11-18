"""Paylaşılan veri yapıları."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    dil: str
    segmentler: list[Segment]

    @property
    def metin(self) -> str:
        return "\n".join(seg.text for seg in self.segmentler if seg.text.strip())

    def as_dict(self) -> dict:
        return {
            "dil": self.dil,
            "segmentler": [asdict(seg) for seg in self.segmentler],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        return cls(
            dil=str(data.get("dil") or ""),
            segmentler=[
                Segment(
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    text=str(seg.get("text") or ""),
                )
                for seg in data.get("segmentler") or []
            ],
        )


@dataclass
class VideoMeta:
    video_id: str
    kaynak_id: str
    url: str
    baslik: str
    kanal: str
    platform: str
    yayin_tarihi: str | None
    sure_saniye: int
    ses_dosyasi: str
    aciklama: str = ""
    kapak: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> VideoMeta:
        return cls(
            video_id=str(data["video_id"]),
            kaynak_id=str(data.get("kaynak_id") or ""),
            url=str(data.get("url") or ""),
            baslik=str(data.get("baslik") or "Başlıksız"),
            kanal=str(data.get("kanal") or ""),
            platform=str(data.get("platform") or ""),
            yayin_tarihi=data.get("yayin_tarihi") or None,
            sure_saniye=int(data.get("sure_saniye") or 0),
            ses_dosyasi=str(data.get("ses_dosyasi") or ""),
            aciklama=str(data.get("aciklama") or ""),
            kapak=str(data.get("kapak") or ""),
        )


@dataclass
class SectionSummary:
    baslangic: float
    bitis: float
    ozet: str


@dataclass
class Summary:
    kisa: str
    bolumler: list[SectionSummary]
    konular: list[str] = field(default_factory=list)
    kisiler: list[str] = field(default_factory=list)
    etiketler: list[str] = field(default_factory=list)
    baglam_token: int = 0
    num_ctx: int = 0
    dil: str = "tr"

    def as_dict(self) -> dict:
        return {
            "dil": self.dil,
            "kisa": self.kisa,
            "bolumler": [asdict(b) for b in self.bolumler],
            "konular": self.konular,
            "kisiler": self.kisiler,
            "etiketler": self.etiketler,
            "baglam_token": self.baglam_token,
            "num_ctx": self.num_ctx,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Summary:
        return cls(
            kisa=str(data.get("kisa") or ""),
            bolumler=[
                SectionSummary(
                    baslangic=float(b["baslangic"]),
                    bitis=float(b["bitis"]),
                    ozet=str(b.get("ozet") or ""),
                )
                for b in data.get("bolumler") or []
            ],
            konular=[str(k) for k in data.get("konular") or []],
            kisiler=[str(k) for k in data.get("kisiler") or []],
            etiketler=[str(k) for k in data.get("etiketler") or []],
            baglam_token=int(data.get("baglam_token") or 0),
            num_ctx=int(data.get("num_ctx") or 0),
            dil=str(data.get("dil") or "tr"),
        )


@dataclass
class Source:
    url: str
    baslik: str


@dataclass
class ClaimResult:
    iddia: str
    hukum: str
    gerekce: str
    kaynaklar: list[Source]
    duzeltme: str = ""
    bolum: int | None = None

    def as_dict(self) -> dict:
        return {
            "iddia": self.iddia,
            "hukum": self.hukum,
            "gerekce": self.gerekce,
            "kaynaklar": [asdict(k) for k in self.kaynaklar],
            "duzeltme": self.duzeltme,
            "bolum": self.bolum,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClaimResult:
        bolum = data.get("bolum")
        return cls(
            iddia=str(data.get("iddia") or ""),
            hukum=str(data.get("hukum") or "dogrulanamadi"),
            gerekce=str(data.get("gerekce") or ""),
            kaynaklar=[
                Source(url=str(k.get("url") or ""), baslik=str(k.get("baslik") or k.get("url") or ""))
                for k in data.get("kaynaklar") or []
            ],
            duzeltme=str(data.get("duzeltme") or ""),
            bolum=int(bolum) if isinstance(bolum, int) else None,
        )


@dataclass
class YaziMeta:
    yazi_id: str
    url: str
    baslik: str
    yazar: str
    site: str
    yayin_tarihi: str | None
    kelime: int
    kismi: bool
    # Tam metin yazarın başka bir sayfasından alındıysa o adres.
    tam_metin_url: str = ""
    dil: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> YaziMeta:
        return cls(
            yazi_id=str(data["yazi_id"]),
            url=str(data.get("url") or ""),
            baslik=str(data.get("baslik") or "Başlıksız"),
            yazar=str(data.get("yazar") or ""),
            site=str(data.get("site") or ""),
            yayin_tarihi=data.get("yayin_tarihi") or None,
            kelime=int(data.get("kelime") or 0),
            kismi=bool(data.get("kismi")),
            tam_metin_url=str(data.get("tam_metin_url") or ""),
            dil=str(data.get("dil") or ""),
        )


@dataclass
class Baglanti:
    metin: str
    url: str

    @classmethod
    def listeden(cls, ham: object) -> list[Baglanti]:
        if not isinstance(ham, list):
            return []
        return [
            cls(metin=str(oge.get("metin") or ""), url=str(oge.get("url") or ""))
            for oge in ham
            if isinstance(oge, dict) and oge.get("url")
        ]


@dataclass
class YaziBolum:
    baslik: str
    metin: str
    # Bölümde yazarın verdiği dış bağlantılar; metin içinde yalnızca bağlantı yazısı kalır.
    baglantilar: list[Baglanti] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> YaziBolum:
        return cls(
            baslik=str(data.get("baslik") or ""),
            metin=str(data.get("metin") or ""),
            baglantilar=Baglanti.listeden(data.get("baglantilar")),
        )


@dataclass
class YaziBolumOzet:
    baslik: str
    ozet: str
    baglantilar: list[Baglanti] = field(default_factory=list)


@dataclass
class YaziOge:
    """Liste veya kavram yazısındaki bir öğe (ör. bir araç ya da terim)."""

    ad: str
    aciklama: str
    url: str = ""


@dataclass
class YaziOzet:
    kisa: str
    bolumler: list[YaziBolumOzet]
    konular: list[str] = field(default_factory=list)
    kisiler: list[str] = field(default_factory=list)
    etiketler: list[str] = field(default_factory=list)
    baglam_token: int = 0
    num_ctx: int = 0
    dil: str = "tr"
    tur: str = ""
    ogeler: list[YaziOge] = field(default_factory=list)
    adimlar: list[str] = field(default_factory=list)
    cikarimlar: list[str] = field(default_factory=list)
    tez: str = ""
    dayanak: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dil": self.dil,
            "tur": self.tur,
            "tez": self.tez,
            "kisa": self.kisa,
            "dayanak": self.dayanak,
            "cikarimlar": self.cikarimlar,
            "ogeler": [asdict(o) for o in self.ogeler],
            "adimlar": self.adimlar,
            "bolumler": [asdict(b) for b in self.bolumler],
            "konular": self.konular,
            "kisiler": self.kisiler,
            "etiketler": self.etiketler,
            "baglam_token": self.baglam_token,
            "num_ctx": self.num_ctx,
        }

    @classmethod
    def from_dict(cls, data: dict) -> YaziOzet:
        return cls(
            kisa=str(data.get("kisa") or ""),
            bolumler=[
                YaziBolumOzet(
                    baslik=str(b.get("baslik") or ""),
                    ozet=str(b.get("ozet") or ""),
                    baglantilar=Baglanti.listeden(b.get("baglantilar")),
                )
                for b in data.get("bolumler") or []
            ],
            konular=[str(k) for k in data.get("konular") or []],
            kisiler=[str(k) for k in data.get("kisiler") or []],
            etiketler=[str(k) for k in data.get("etiketler") or []],
            baglam_token=int(data.get("baglam_token") or 0),
            num_ctx=int(data.get("num_ctx") or 0),
            dil=str(data.get("dil") or "tr"),
            tur=str(data.get("tur") or ""),
            ogeler=[
                YaziOge(ad=str(o.get("ad") or ""), aciklama=str(o.get("aciklama") or ""), url=str(o.get("url") or ""))
                for o in data.get("ogeler") or []
                if isinstance(o, dict) and o.get("ad")
            ],
            adimlar=[str(a) for a in data.get("adimlar") or []],
            cikarimlar=[str(c) for c in data.get("cikarimlar") or []],
            tez=str(data.get("tez") or ""),
            dayanak=[str(d) for d in data.get("dayanak") or []],
        )
