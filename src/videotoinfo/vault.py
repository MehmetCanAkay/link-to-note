"""Obsidian vault notları, konu sayfaları ve panolar."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from videotoinfo.config import Settings
from videotoinfo.models import ClaimResult, Summary, Transcript, VideoMeta, YaziBolumOzet, YaziMeta, YaziOzet
from videotoinfo.summarize import detayli_metin
from videotoinfo.textutil import dosya_koku, konu_adi, saniye_yaz, sure_dk, wikilink_gecir, yaml_olcek

BAS = "<!-- vti:baslangic -->"
SON = "<!-- vti:bitis -->"
LISTE_BAS = "<!-- vti:liste:baslangic -->"
LISTE_SON = "<!-- vti:liste:bitis -->"
SIRALI = [
    "video_id",
    "yazi_id",
    "baslik",
    "kaynak",
    "platform",
    "kanal",
    "yayin_tarihi",
    "eklenme",
    "sure_dk",
    "dil",
    "yazi_turu",
    "konular",
    "kisiler",
    "tags",
    "durum",
    "dogrulama",
]
HUKUM_YAZI = {
    "dogru": "doğru",
    "yanlis": "yanlış",
    "eksik": "eksik",
    "eskimis": "eskimiş",
    "dogrulanamadi": "doğrulanamadı",
}
DUZELTME_HUKUMLERI = {"yanlis", "eksik", "eskimis"}
YAZI_TURU = {
    "liste": "liste",
    "kavram": "kavram açıklaması",
    "rehber": "rehber",
    "yol_haritasi": "yol haritası",
    "gorus": "görüş",
    "haber": "haber",
    "inceleme": "inceleme",
}
KLASORLER = ["Videolar", "Yazilar", "Transkriptler", "Konular", "Kisiler", "Sesler", "Panolar"]
_BASE_FENCE = re.compile(r"```base\n.*?```", re.DOTALL)
OZELLIK_TIPLERI = {
    "eklenme": "date",
    "yayin_tarihi": "date",
    "sure_dk": "number",
    "tags": "tags",
    "konular": "multitext",
    "kisiler": "multitext",
    "durum": "text",
    "dogrulama": "text",
    "platform": "text",
    "kanal": "text",
    "dil": "text",
    "tur": "text",
    "yazi_turu": "text",
    "video_id": "text",
    "yazi_id": "text",
    "baslik": "text",
    "kaynak": "text",
    "ad": "text",
}


def _filtre_satiri(satir: str) -> str:
    if any(karakter in satir for karakter in "\"'#"):
        return "'" + satir.replace("'", "''") + "'"
    return satir


def _base(filtreler: list[str], *, grup: str | None = None, ad: str) -> str:
    govde = ["filters:", "  and:"]
    govde.extend(f"    - {_filtre_satiri(satir)}" for satir in filtreler)
    govde.extend(
        [
            "properties:",
            "  baslik:",
            "    displayName: Başlık",
            "  kanal:",
            "    displayName: Kanal",
            "  platform:",
            "    displayName: Platform",
            "  yayin_tarihi:",
            "    displayName: Yayın tarihi",
            "  eklenme:",
            "    displayName: Eklenme",
            "  sure_dk:",
            "    displayName: Süre (dk)",
            "  durum:",
            "    displayName: Durum",
            "  dogrulama:",
            "    displayName: Doğrulama",
            "views:",
            "  - type: table",
            f"    name: {ad}",
        ]
    )
    if grup:
        govde.extend(
            [
                "    groupBy:",
                f"      property: {grup}",
                "      direction: ASC",
            ]
        )
    govde.extend(
        [
            "    order:",
            "      - baslik",
            "      - kanal",
            "      - platform",
            "      - yayin_tarihi",
            "      - sure_dk",
            "      - eklenme",
            "      - durum",
            "      - dogrulama",
            "    sort:",
            "      - property: note.eklenme",
            "        direction: DESC",
        ]
    )
    return "\n".join(govde) + "\n"


PANOLAR = {
    "Tum-videolar.base": _base(
        ['file.inFolder("Videolar")', 'file.hasTag("video")'],
        ad="Tüm videolar",
    ),
    "Tum-yazilar.base": _base(
        ['file.inFolder("Yazilar")', 'file.hasTag("yazi")'],
        ad="Tüm yazılar",
    ),
    "Okunacaklar.base": _base(
        ['file.inFolder("Videolar")', 'file.hasTag("video")', 'durum == "okunacak"'],
        ad="Okunacaklar",
    ),
    "Kanallar.base": _base(
        ['file.inFolder("Videolar")', 'file.hasTag("video")'],
        grup="note.kanal",
        ad="Kanala göre",
    ),
    "Konular.base": """filters:
  and:
    - 'file.inFolder("Konular")'
    - 'tur == "konu"'
properties:
  ad:
    displayName: Konu
views:
  - type: table
    name: Konular
    order:
      - ad
      - file.name
    sort:
      - property: note.ad
        direction: ASC
""",
    "Sorunlu-iddialar.base": _base(
        ['dogrulama == "sorunlu"'],
        ad="Sorunlu iddialar",
    ),
}


@dataclass
class VideoKaydi:
    yol: Path
    stem: str
    baslik: str
    kanal: str
    sure_dk: int
    durum: str
    dogrulama: str
    konular: list[str]
    kisiler: list[str]
    video_id: str
    etiketler: list[str]
    klasor: str = "Videolar"


# Ayraç tek başına bir satır olmalı; URL veya başlık içindeki "---" frontmatter'ı bölmez.
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.DOTALL | re.MULTILINE)


def ayir(metin: str) -> tuple[dict, str]:
    eslesme = _FRONTMATTER.match(metin)
    if not eslesme:
        return {}, metin
    try:
        fm = yaml.safe_load(eslesme.group(1)) or {}
    except yaml.YAMLError:
        return {}, metin
    if not isinstance(fm, dict):
        return {}, metin
    return fm, metin[eslesme.end():].lstrip("\n")


def _alan(anahtar: str, deger: object) -> list[str]:
    if isinstance(deger, list):
        if not deger:
            return [f"{anahtar}: []"]
        return [f"{anahtar}:", *[f"  - {yaml_olcek(oge)}" for oge in deger]]
    return [f"{anahtar}: {yaml_olcek(deger)}"]


def frontmatter_yaz(veri: dict) -> str:
    satirlar = ["---"]
    yazilan: set[str] = set()
    for anahtar in SIRALI:
        if anahtar not in veri:
            continue
        satirlar.extend(_alan(anahtar, veri[anahtar]))
        yazilan.add(anahtar)
    for anahtar, deger in veri.items():
        if anahtar in yazilan:
            continue
        blok = yaml.safe_dump({anahtar: deger}, allow_unicode=True, sort_keys=False).strip()
        satirlar.append(blok)
    satirlar.append("---")
    return "\n".join(satirlar)


def govde_birlestir(eski: str, uretilen: str, bas: str = BAS, son: str = SON) -> str:
    blok = f"{bas}\n{uretilen.strip()}\n{son}"
    if bas in eski and son in eski:
        on = eski.split(bas, 1)[0].rstrip()
        geri = eski.split(son, 1)[1].lstrip("\n")
        parcalar = [parca for parca in (on, blok, geri.rstrip()) if parca]
        return "\n\n".join(parcalar).rstrip() + "\n"
    if eski.strip():
        return f"{blok}\n\n{eski.strip()}\n"
    return f"{blok}\n"


def link_ad(link: str) -> str:
    m = re.fullmatch(r"\[\[([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", link.strip())
    ham = m.group(1) if m else link
    return konu_adi(Path(ham).name)


def _liste(deger: object) -> list[str]:
    if deger is None:
        return []
    if isinstance(deger, str):
        return [deger]
    if isinstance(deger, list):
        return [str(oge) for oge in deger]
    return []


def _tarih_metni(deger: object) -> str | None:
    if deger is None:
        return None
    if hasattr(deger, "isoformat"):
        return str(deger.isoformat())[:10]
    metin = str(deger).strip()
    if not metin or metin == "None":
        return None
    return metin[:10]


def birlestir_adlar(eski: object, yeni: list[str]) -> list[str]:
    sonuc: list[str] = []
    for ad in [link_ad(oge) for oge in [*yeni, *_liste(eski)]]:
        if ad and ad not in sonuc:
            sonuc.append(ad)
    return sonuc


def etiket_birlestir(eski: object, yeni: list[str], kok: str = "video") -> list[str]:
    sonuc: list[str] = []
    for etiket in [kok, *yeni, *_liste(eski)]:
        etiket = str(etiket).strip()
        if etiket and etiket not in sonuc:
            sonuc.append(etiket)
    return sonuc


OTO_DOSYA = "not_oto.json"


def oto_yukle(klasor: Path) -> dict | None:
    """Nota en son otomatik yazılan konu, kişi ve etiketler. Kayıt yoksa None."""
    yol = klasor / OTO_DOSYA
    if not yol.exists():
        return None
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return veri if isinstance(veri, dict) else None


def oto_kaydet(klasor: Path, konular: list[str], kisiler: list[str], etiketler: list[str]) -> None:
    klasor.mkdir(parents=True, exist_ok=True)
    (klasor / OTO_DOSYA).write_text(
        json.dumps({"konular": konular, "kisiler": kisiler, "tags": etiketler}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def elle_eklenenler(eski: object, onceki_oto: object, ad=link_ad) -> list[str]:
    """Notta olup önceki otomatik listede olmayanlar kullanıcıya aittir.

    Önceki otomatik liste bilinmiyorsa (eski notlar) eski değerlerin hepsi otomatik sayılır.
    """
    if onceki_oto is None:
        return []
    oto = {ad(oge) for oge in _liste(onceki_oto)}
    return [ad(oge) for oge in _liste(eski) if ad(oge) and ad(oge) not in oto]


def _fm_listeleri(
    eski_fm: dict, onceki: dict | None, konular: list[str], kisiler: list[str], etiketler: list[str], kok: str
) -> dict:
    onceki = onceki or {}
    oto_var = bool(onceki)

    def kullanici(alan: str, ad=link_ad) -> list[str]:
        return elle_eklenenler(eski_fm.get(alan), onceki.get(alan, []) if oto_var else None, ad)

    etiket_ad = lambda e: str(e).strip()  # noqa: E731
    return {
        "konular": [f"[[{ad}]]" for ad in birlestir_adlar(kullanici("konular"), konular)],
        "kisiler": [f"[[{ad}]]" for ad in birlestir_adlar(kullanici("kisiler"), kisiler)],
        "tags": etiket_birlestir(kullanici("tags", etiket_ad), etiketler, kok),
    }


def dogrulama_durumu(iddialar: list[ClaimResult] | None, eski: object) -> str:
    if iddialar is None:
        return str(eski or "yapilmadi")
    if any(iddia.hukum in {"yanlis", "eskimis"} for iddia in iddialar):
        return "sorunlu"
    if any(iddia.hukum in {"dogru", "eksik"} for iddia in iddialar):
        return "dogrulandi"
    return "dogrulanamadi"


def prepare_vault(vault: Path) -> None:
    for ad in KLASORLER:
        (vault / ad).mkdir(parents=True, exist_ok=True)
    obsidian = vault / ".obsidian"
    obsidian.mkdir(exist_ok=True)
    types_yol = obsidian / "types.json"
    types = {"types": {}}
    if types_yol.exists():
        try:
            types = json.loads(types_yol.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            types = {"types": {}}
    types.setdefault("types", {}).update(OZELLIK_TIPLERI)
    types_yol.write_text(json.dumps(types, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    app = obsidian / "app.json"
    if not app.exists():
        app.write_text(
            json.dumps(
                {"attachmentFolderPath": "Sesler", "alwaysUpdateLinks": True, "showUnsupportedFiles": True},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    eklentiler = obsidian / "core-plugins.json"
    if not eklentiler.exists():
        eklentiler.write_text(
            json.dumps(
                [
                    "file-explorer",
                    "global-search",
                    "switcher",
                    "graph",
                    "backlink",
                    "canvas",
                    "outgoing-link",
                    "tag-pane",
                    "properties",
                    "page-preview",
                    "note-composer",
                    "command-palette",
                    "editor-status",
                    "bookmarks",
                    "outline",
                    "word-count",
                    "file-recovery",
                    "bases",
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for ad, icerik in PANOLAR.items():
        (vault / "Panolar" / ad).write_text(icerik, encoding="utf-8")
    baslangic = vault / "Panolar" / "Baslangic.md"
    if not baslangic.exists():
        baslangic.write_text(
            "\n".join(
                [
                    "---",
                    "tags:",
                    "  - pano",
                    "---",
                    "# Video arşivi",
                    "",
                    "Durum alanını notta `okunacak` veya `okundu` yapın. Panolar bu alana ve `dogrulama` alanına göre süzülür.",
                    "",
                    "![[Panolar/Tum-videolar.base]]",
                    "",
                    "## Diğer panolar",
                    "",
                    "- [[Panolar/Okunacaklar.base|Okunacaklar]]",
                    "- [[Panolar/Kanallar.base|Kanala göre]]",
                    "- [[Panolar/Konular.base|Konular]]",
                    "- [[Panolar/Sorunlu-iddialar.base|Sorunlu iddialar]]",
                    "- [[Panolar/Tum-yazilar.base|Tüm yazılar]]",
                    "- [[Gelen kutusu|Gelen kutusu]]",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        metin = baslangic.read_text(encoding="utf-8")
        ek = []
        if "Tum-yazilar.base" not in metin:
            ek.append("- [[Panolar/Tum-yazilar.base|Tüm yazılar]]")
        if "[[Gelen kutusu" not in metin:
            ek.append("- [[Gelen kutusu|Gelen kutusu]]")
        if ek:
            baslangic.write_text(metin.rstrip() + "\n" + "\n".join(ek) + "\n", encoding="utf-8")
    from videotoinfo.inbox import hazirla

    hazirla(vault)


def find_note(vault: Path, klasor_adi: str, kimlik_alani: str, kimlik: str) -> Path | None:
    klasor = vault / klasor_adi
    if not klasor.exists():
        return None
    for yol in sorted(klasor.glob("*.md")):
        fm, _ = ayir(yol.read_text(encoding="utf-8"))
        if fm.get(kimlik_alani) == kimlik:
            return yol
    return None


def not_koku(
    vault: Path,
    klasor_adi: str,
    kimlik_alani: str,
    kimlik: str,
    tarih: str,
    baslik: str,
) -> str:
    mevcut = find_note(vault, klasor_adi, kimlik_alani, kimlik)
    if mevcut is not None:
        return mevcut.stem
    kok = dosya_koku(tarih, baslik)
    yol = vault / klasor_adi / f"{kok}.md"
    if yol.exists():
        fm, _ = ayir(yol.read_text(encoding="utf-8"))
        if fm.get(kimlik_alani) != kimlik:
            kok = dosya_koku(tarih, f"{baslik} {kimlik}")
    return kok


def find_video(vault: Path, video_id: str) -> Path | None:
    return find_note(vault, "Videolar", "video_id", video_id)


def note_stem(vault: Path, meta: VideoMeta) -> str:
    tarih = meta.yayin_tarihi or date.today().isoformat()
    return not_koku(vault, "Videolar", "video_id", meta.video_id, tarih, meta.baslik)


def _gorunen(baslik: str) -> str:
    return baslik.replace("[", "").replace("]", "").replace("|", " ")


def iddia_bolumu(iddialar: list[ClaimResult] | None, komut: str = "vti add <link> --dogrula") -> str:
    if iddialar is None:
        return f"Henüz doğrulanmadı. `{komut}` ile çalıştırın."
    if not iddialar:
        return "Kontrol edilebilir bir iddia bulunamadı."
    parcalar = [
        "> Bu bölüm otomatik bir kontrol notudur, kesin hüküm değildir. Bağlantılar arama sonuçlarından gelir."
    ]
    for sira, iddia in enumerate(iddialar, start=1):
        hukum = HUKUM_YAZI.get(iddia.hukum, iddia.hukum)
        blok = [
            f"### {sira}. {iddia.iddia}",
            f"- **Hüküm:** {hukum}",
            f"- **Gerekçe:** {iddia.gerekce or '—'}",
        ]
        if iddia.duzeltme:
            blok.append(f"- **Doğrusu:** {iddia.duzeltme}")
        if iddia.kaynaklar:
            blok.append("- **Kaynaklar:**")
            for kaynak in iddia.kaynaklar:
                ad = _gorunen(kaynak.baslik) or kaynak.url
                blok.append(f"  - [{ad}]({kaynak.url})")
        else:
            blok.append("- **Kaynaklar:** yok")
        parcalar.append("\n".join(blok))
    return "\n\n".join(parcalar)


def _callout_satirlari(metin: str) -> str:
    satirlar = []
    for satir in metin.strip().splitlines():
        # Obsidian açıklamadaki #etiketleri not etiketi saymasın, [[ ]] bağlantı açmasın.
        satir = re.sub(r"(?<![\w&])#(?=\w)", r"\\#", satir).replace("[[", "\\[\\[")
        satirlar.append(f"> {satir}".rstrip())
    return "\n".join(satirlar)


def video_bilgisi(meta: VideoMeta) -> str:
    satirlar = []
    if meta.url and meta.platform == "youtube":
        satirlar.append(f"![]({meta.url})")
    elif meta.kapak:
        satirlar.append(f"![]({meta.kapak})")
    bilgi = [f"- **Video:** [{_gorunen(meta.baslik)}]({meta.url})" if meta.url else f"- **Video:** {meta.baslik}"]
    if meta.kanal:
        bilgi.append(f"- **Kanal:** {meta.kanal}")
    if meta.platform:
        bilgi.append(f"- **Platform:** {meta.platform}")
    if meta.yayin_tarihi:
        bilgi.append(f"- **Yayın tarihi:** {meta.yayin_tarihi}")
    if meta.sure_saniye:
        bilgi.append(f"- **Süre:** {saniye_yaz(meta.sure_saniye)}")
    satirlar.append("\n".join(bilgi))
    if meta.aciklama:
        satirlar.append("> [!info]- Video açıklaması\n" + _callout_satirlari(meta.aciklama))
    return "\n\n".join(satirlar)


def not_govdesi(
    meta: VideoMeta,
    ozet: Summary,
    iddialar: list[ClaimResult] | None,
    kok: str,
    ses_adi: str | None,
) -> str:
    adlar = [*ozet.konular, *ozet.kisiler]
    kisa = wikilink_gecir(ozet.kisa, adlar)
    detay = wikilink_gecir(detayli_metin(ozet, meta), adlar)
    parcalar = [f"## Video bilgisi\n\n{video_bilgisi(meta)}"]
    parcalar.append(f"## Kısa özet\n\n{kisa}")
    parcalar.append(f"## Detaylı özet\n\n{detay}")
    parcalar.append(f"## İddialar ve kaynaklar\n\n{iddia_bolumu(iddialar)}")
    parcalar.append(f"## Transkript\n\n[[Transkriptler/{kok}|Tam transkript]]")
    if ses_adi:
        parcalar.append(f"## Sesli özet\n\n![[Sesler/{ses_adi}]]")
    return "\n\n".join(parcalar)


def _transkript_satirlari(transcript: Transcript) -> str:
    return "\n".join(f"[{saniye_yaz(seg.start)}] {seg.text}" for seg in transcript.segmentler)


def _yaz(yol: Path, fm: dict, govde: str) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(frontmatter_yaz(fm) + "\n" + govde, encoding="utf-8")


def videolari_oku(vault: Path, klasor_adi: str = "Videolar", kimlik: str = "video_id") -> list[VideoKaydi]:
    klasor = vault / klasor_adi
    if not klasor.exists():
        return []
    kayitlar: list[VideoKaydi] = []
    for yol in sorted(klasor.glob("*.md")):
        fm, _ = ayir(yol.read_text(encoding="utf-8"))
        if not fm.get(kimlik):
            continue
        kayitlar.append(
            VideoKaydi(
                yol=yol,
                stem=yol.stem,
                baslik=str(fm.get("baslik") or yol.stem),
                kanal=str(fm.get("kanal") or ""),
                sure_dk=int(fm.get("sure_dk") or 0),
                durum=str(fm.get("durum") or ""),
                dogrulama=str(fm.get("dogrulama") or ""),
                konular=[ad for ad in (link_ad(oge) for oge in _liste(fm.get("konular"))) if ad],
                kisiler=[ad for ad in (link_ad(oge) for oge in _liste(fm.get("kisiler"))) if ad],
                video_id=str(fm.get(kimlik)),
                etiketler=_liste(fm.get("tags")),
                klasor=klasor_adi,
            )
        )
    return kayitlar


def tum_kayitlar(vault: Path) -> list[VideoKaydi]:
    return videolari_oku(vault) + videolari_oku(vault, "Yazilar", "yazi_id")


def filter_videos(vault: Path, *, durum: str | None = None, dogrulama: str | None = None) -> list[VideoKaydi]:
    cikti = []
    for kayit in videolari_oku(vault):
        if "video" not in kayit.etiketler:
            continue
        if durum is not None and kayit.durum != durum:
            continue
        if dogrulama is not None and kayit.dogrulama != dogrulama:
            continue
        cikti.append(kayit)
    return cikti


def _base_kodu(alan: str) -> str:
    return (
        "```base\n"
        "filters:\n"
        "  and:\n"
        "    - or:\n"
        '      - file.inFolder("Videolar")\n'
        '      - file.inFolder("Yazilar")\n'
        f"    - {alan}.contains(this.file)\n"
        "views:\n"
        "  - type: table\n"
        "    name: Kayıtlar\n"
        "    order:\n"
        "      - baslik\n"
        "      - kanal\n"
        "      - yayin_tarihi\n"
        "      - sure_dk\n"
        "      - durum\n"
        "```"
    )


def _sayfa_sablonu(ad: str, alan: str) -> str:
    return (
        f"# {ad}\n\n"
        "Bu sayfadaki videolar ve yazılar arşive eklendikçe güncellenir.\n\n"
        f"{_base_kodu(alan)}\n"
    )


def _liste_md(kayitlar: list[VideoKaydi]) -> str:
    if not kayitlar:
        return "_Bu başlık altında kayıt yok._"
    satirlar = []
    for kayit in kayitlar:
        bos = "kanal yok" if kayit.klasor == "Videolar" else "yazar yok"
        satirlar.append(
            f"- [[{kayit.klasor}/{kayit.stem}|{_gorunen(kayit.baslik)}]] — {kayit.kanal or bos} — {kayit.sure_dk} dk"
        )
    return "\n".join(satirlar)


def _konu_sayfasi(yol: Path, ad: str, alan: str, tur: str, kayitlar: list[VideoKaydi]) -> None:
    liste = _liste_md(kayitlar)
    if yol.exists():
        fm, govde = ayir(yol.read_text(encoding="utf-8"))
        fm.setdefault("tur", tur)
        fm.setdefault("ad", ad)
        etiketler = etiket_birlestir(fm.get("tags"), [tur])
        etiketler = [etiket for etiket in etiketler if etiket != "video"]
        if tur not in etiketler:
            etiketler.insert(0, tur)
        fm["tags"] = etiketler
        govde = _BASE_FENCE.sub(lambda _: _base_kodu(alan), govde, count=1)
        govde = govde_birlestir(govde, liste, LISTE_BAS, LISTE_SON)
    else:
        fm = {"tur": tur, "ad": ad, "tags": [tur]}
        govde = _sayfa_sablonu(ad, alan) + "\n" + govde_birlestir("", liste, LISTE_BAS, LISTE_SON)
    _yaz(yol, fm, govde)


def listeleri_guncelle(vault: Path) -> None:
    konular: dict[str, list[VideoKaydi]] = {}
    kisiler: dict[str, list[VideoKaydi]] = {}
    for kayit in tum_kayitlar(vault):
        for ad in kayit.konular:
            konular.setdefault(ad, []).append(kayit)
        for ad in kayit.kisiler:
            kisiler.setdefault(ad, []).append(kayit)
    _grup_yaz(vault / "Konular", "konular", "konu", konular)
    _grup_yaz(vault / "Kisiler", "kisiler", "kisi", kisiler)


def _grup_yaz(klasor: Path, alan: str, tur: str, gruplar: dict[str, list[VideoKaydi]]) -> None:
    klasor.mkdir(parents=True, exist_ok=True)
    for ad, liste in gruplar.items():
        _konu_sayfasi(klasor / f"{ad}.md", ad, alan, tur, liste)
    for yol in klasor.glob("*.md"):
        if yol.stem not in gruplar:
            _konu_sayfasi(yol, yol.stem, alan, tur, [])


def missing_links(vault: Path) -> list[str]:
    eksik: list[str] = []
    for kayit in tum_kayitlar(vault):
        if kayit.klasor == "Videolar" and not (vault / "Transkriptler" / f"{kayit.stem}.md").exists():
            eksik.append(f"Transkriptler/{kayit.stem}")
        for ad in kayit.konular:
            if not (vault / "Konular" / f"{ad}.md").exists():
                eksik.append(f"Konular/{ad}")
        for ad in kayit.kisiler:
            if not (vault / "Kisiler" / f"{ad}.md").exists():
                eksik.append(f"Kisiler/{ad}")
        for ad in kayit.konular:
            sayfa = vault / "Konular" / f"{ad}.md"
            if sayfa.exists() and f"[[{kayit.klasor}/{kayit.stem}|" not in sayfa.read_text(encoding="utf-8"):
                eksik.append(f"liste:{ad}:{kayit.stem}")
    return eksik


def write_archive(
    settings: Settings,
    meta: VideoMeta,
    transcript: Transcript,
    ozet: Summary,
    iddialar: list[ClaimResult] | None,
    ses_yolu: Path | None,
) -> Path:
    vault = settings.vault
    prepare_vault(vault)
    kok = note_stem(vault, meta)
    video_yol = vault / "Videolar" / f"{kok}.md"
    eski_fm: dict = {}
    eski_govde = ""
    if video_yol.exists():
        eski_fm, eski_govde = ayir(video_yol.read_text(encoding="utf-8"))
    eklenme = _tarih_metni(eski_fm.get("eklenme")) or date.today().isoformat()
    yayin = meta.yayin_tarihi or _tarih_metni(eski_fm.get("yayin_tarihi"))
    veri_klasoru = settings.data_dir / meta.video_id
    listeler = _fm_listeleri(
        eski_fm, oto_yukle(veri_klasoru), ozet.konular, ozet.kisiler, ozet.etiketler, "video"
    )
    fm = {
        "video_id": meta.video_id,
        "baslik": meta.baslik,
        "kaynak": meta.url,
        "platform": meta.platform,
        "kanal": meta.kanal,
        "yayin_tarihi": yayin,
        "eklenme": eklenme,
        "sure_dk": sure_dk(meta.sure_saniye),
        "dil": transcript.dil,
        **listeler,
        "durum": str(eski_fm.get("durum") or "okunacak"),
        "dogrulama": dogrulama_durumu(iddialar, eski_fm.get("dogrulama")),
    }
    for anahtar, deger in eski_fm.items():
        if anahtar not in fm:
            fm[anahtar] = deger
    ses_adi = ses_yolu.name if ses_yolu is not None else None
    govde = govde_birlestir(eski_govde, not_govdesi(meta, ozet, iddialar, kok, ses_adi))
    _yaz(video_yol, fm, govde)
    oto_kaydet(veri_klasoru, ozet.konular, ozet.kisiler, ozet.etiketler)

    trans_yol = vault / "Transkriptler" / f"{kok}.md"
    trans_fm = {
        "video_id": meta.video_id,
        "tur": "transkript",
        "baslik": meta.baslik,
        "kaynak": meta.url,
        "dil": transcript.dil,
        "tags": ["transkript"],
    }
    giris = f"# Transkript\n\nKaynak video: [[Videolar/{kok}|{_gorunen(meta.baslik)}]]\n\n"
    if trans_yol.exists():
        _, trans_govde = ayir(trans_yol.read_text(encoding="utf-8"))
        trans_govde = govde_birlestir(trans_govde, _transkript_satirlari(transcript))
    else:
        trans_govde = giris + govde_birlestir("", _transkript_satirlari(transcript))
    _yaz(trans_yol, trans_fm, trans_govde)
    listeleri_guncelle(vault)
    return video_yol


def _okuma_dk(kelime: int) -> int:
    if kelime <= 0:
        return 0
    return max(1, int(round(kelime / 200)))


def _duzeltme_kutusu(iddia: ClaimResult) -> str:
    etiket = HUKUM_YAZI.get(iddia.hukum, iddia.hukum)
    satirlar = [
        f"> [!warning] Düzeltme ({etiket})",
        f"> **Yazıdaki iddia:** {iddia.iddia}",
        f"> **Doğrusu:** {iddia.duzeltme or '—'}",
    ]
    if iddia.kaynaklar:
        baglar = []
        for kaynak in iddia.kaynaklar:
            ad = _gorunen(kaynak.baslik) or kaynak.url
            baglar.append(f"[{ad}]({kaynak.url})")
        satirlar.append("> **Kaynak:** " + ", ".join(baglar))
    else:
        satirlar.append("> **Kaynak:** yok")
    return "\n".join(satirlar)


def _detay_ve_duzeltmeler(ozet: YaziOzet, iddialar: list[ClaimResult] | None) -> str:
    harita: dict[int, list[ClaimResult]] = {}
    artan: list[ClaimResult] = []
    for iddia in iddialar or []:
        if iddia.hukum not in DUZELTME_HUKUMLERI:
            continue
        if iddia.bolum is None or not (0 <= iddia.bolum < len(ozet.bolumler)):
            artan.append(iddia)
        else:
            harita.setdefault(iddia.bolum, []).append(iddia)
    parcalar: list[str] = []
    for sira, bolum in enumerate(ozet.bolumler):
        blok = f"### {bolum.baslik}\n\n{bolum.ozet.strip()}"
        kutular = [_duzeltme_kutusu(iddia) for iddia in harita.get(sira, [])]
        if kutular:
            blok += "\n\n" + "\n\n".join(kutular)
        parcalar.append(blok)
    if artan:
        parcalar.append("### Diğer düzeltmeler\n\n" + "\n\n".join(_duzeltme_kutusu(iddia) for iddia in artan))
    return "\n\n".join(parcalar)


def _hucre(metin: str) -> str:
    return " ".join((metin or "").split()).replace("|", "\\|")


def _ogeler_tablosu(ozet: YaziOzet) -> str:
    kavram = ozet.tur == "kavram"
    baglantili = any(oge.url for oge in ozet.ogeler)
    basliklar = ["Terim", "Anlamı"] if kavram else ["Öğe", "Ne işe yarar"]
    if baglantili:
        basliklar.append("Bağlantı")
    satirlar = ["| " + " | ".join(basliklar) + " |", "|" + "---|" * len(basliklar)]
    for oge in ozet.ogeler:
        hucreler = [f"**{_hucre(oge.ad)}**", _hucre(oge.aciklama)]
        if baglantili:
            hucreler.append(f"[aç]({oge.url})" if oge.url else "")
        satirlar.append("| " + " | ".join(hucreler) + " |")
    return "\n".join(satirlar)


def yazi_kaynaklari(ozet: YaziOzet) -> str:
    """Yazarın verdiği bağlantılar, geçtikleri bölüme göre."""
    gorulen: set[str] = set()
    bloklar: list[str] = []
    for bolum in ozet.bolumler:
        satirlar = []
        for bag in bolum.baglantilar:
            if bag.url in gorulen:
                continue
            gorulen.add(bag.url)
            satirlar.append(f"- [{_gorunen(bag.metin) or bag.url}]({bag.url})")
        if satirlar:
            bloklar.append(f"**{_gorunen(bolum.baslik)}**\n" + "\n".join(satirlar))
    return "\n\n".join(bloklar)


def isaretleri_koru(yeni: str, eski: str) -> str:
    """Kullanıcının işaretlediği adımlar not yeniden yazılınca da işaretli kalır."""
    isaretli = {satir[6:].strip() for satir in eski.splitlines() if satir.lower().startswith("- [x] ")}
    if not isaretli:
        return yeni
    return "\n".join(
        f"- [x] {satir[6:]}" if satir.startswith("- [ ] ") and satir[6:].strip() in isaretli else satir
        for satir in yeni.splitlines()
    )


def yazi_govdesi(meta: YaziMeta, ozet: YaziOzet, iddialar: list[ClaimResult] | None) -> str:
    adlar = [*ozet.konular, *ozet.kisiler]
    kisa = wikilink_gecir(ozet.kisa, adlar)
    bagli = YaziOzet(
        kisa=kisa,
        bolumler=[
            YaziBolumOzet(bolum.baslik, wikilink_gecir(bolum.ozet, adlar), bolum.baglantilar)
            for bolum in ozet.bolumler
        ],
        konular=ozet.konular,
        kisiler=ozet.kisiler,
    )
    parcalar = []
    if meta.kismi:
        parcalar.append(
            "> [!note] Bu yazının yalnızca görünen kısmı okunabildi; üyelere özel içerik eksik olabilir."
        )
    elif meta.tam_metin_url:
        parcalar.append(
            f"> [!info] Asıl sayfa üyelere özel; tam metin yazarın ücretsiz kopyasından alındı: "
            f"[{meta.tam_metin_url}]({meta.tam_metin_url})"
        )
    if meta.url:
        parcalar.append(f"[Kaynağı aç]({meta.url})")
    if ozet.tez:
        parcalar.append(f"> [!abstract] Ana tez\n> {wikilink_gecir(ozet.tez, adlar)}")
    parcalar.append(f"## Kısa özet\n\n{kisa}")
    if ozet.cikarimlar:
        parcalar.append("## Çıkarımlar\n\n" + "\n".join(f"- {m}" for m in ozet.cikarimlar))
    if ozet.dayanak:
        parcalar.append("## Dayanak ve eleştiri\n\n" + "\n".join(f"- {m}" for m in ozet.dayanak))
    if ozet.ogeler:
        baslik = "Terimler" if ozet.tur == "kavram" else "Öğeler"
        parcalar.append(f"## {baslik}\n\n{_ogeler_tablosu(ozet)}")
    if ozet.adimlar:
        parcalar.append("## Adımlar\n\n" + "\n".join(f"- [ ] {m}" for m in ozet.adimlar))
    parcalar.append(f"## Detaylı özet\n\n{_detay_ve_duzeltmeler(bagli, iddialar)}")
    kaynaklar = yazi_kaynaklari(ozet)
    if kaynaklar:
        parcalar.append(f"## Yazıdaki bağlantılar\n\n{kaynaklar}")
    parcalar.append(f"## İddialar ve kaynaklar\n\n{iddia_bolumu(iddialar, 'vti oku <link>')}")
    return "\n\n".join(parcalar)


def write_article(
    settings: Settings,
    meta: YaziMeta,
    ozet: YaziOzet,
    iddialar: list[ClaimResult] | None,
) -> Path:
    vault = settings.vault
    prepare_vault(vault)
    tarih = meta.yayin_tarihi or date.today().isoformat()
    kok = not_koku(vault, "Yazilar", "yazi_id", meta.yazi_id, tarih, meta.baslik)
    yol = vault / "Yazilar" / f"{kok}.md"
    eski_fm: dict = {}
    eski_govde = ""
    if yol.exists():
        eski_fm, eski_govde = ayir(yol.read_text(encoding="utf-8"))
    eklenme = _tarih_metni(eski_fm.get("eklenme")) or date.today().isoformat()
    yayin = meta.yayin_tarihi or _tarih_metni(eski_fm.get("yayin_tarihi"))
    veri_klasoru = settings.data_dir / meta.yazi_id
    listeler = _fm_listeleri(
        eski_fm, oto_yukle(veri_klasoru), ozet.konular, ozet.kisiler, ozet.etiketler, "yazi"
    )
    fm = {
        "yazi_id": meta.yazi_id,
        "baslik": meta.baslik,
        "kaynak": meta.url,
        "platform": meta.site,
        "kanal": meta.yazar or meta.site,
        "yayin_tarihi": yayin,
        "eklenme": eklenme,
        "sure_dk": _okuma_dk(meta.kelime),
        **({"yazi_turu": YAZI_TURU[ozet.tur]} if ozet.tur in YAZI_TURU else {}),
        **listeler,
        "durum": str(eski_fm.get("durum") or "okunacak"),
        "dogrulama": dogrulama_durumu(iddialar, eski_fm.get("dogrulama")),
    }
    for anahtar, deger in eski_fm.items():
        if anahtar not in fm:
            fm[anahtar] = deger
    govde = govde_birlestir(eski_govde, isaretleri_koru(yazi_govdesi(meta, ozet, iddialar), eski_govde))
    _yaz(yol, fm, govde)
    oto_kaydet(veri_klasoru, ozet.konular, ozet.kisiler, ozet.etiketler)
    listeleri_guncelle(vault)
    return yol
