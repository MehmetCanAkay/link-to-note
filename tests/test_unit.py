from pathlib import Path

from videotoinfo.config import default_settings, load_settings
from videotoinfo.factcheck import duzeltme_temizle, hukmu_uygula
from videotoinfo.inbox import bekleyen_urller, hata_yaz, tamamla, tur_belirle
from videotoinfo.models import (
    ClaimResult,
    SectionSummary,
    Segment,
    Source,
    Summary,
    Transcript,
    VideoMeta,
    YaziBolum,
    YaziBolumOzet,
    YaziMeta,
    YaziOzet,
)
from videotoinfo.yazi import getir, kismi_mi, metin_yeterli, metni_bol, yazi_id_uret, yazi_kaydet, yazi_klasoru
from videotoinfo.search import SearchResult
from videotoinfo.summarize import bolumlere_ayir, detayli_metin
from videotoinfo.textutil import (
    dosya_koku,
    doldur,
    json_cikar,
    konusma_metni,
    norm_url,
    wikilink_gecir,
    zaman_linki,
)
from videotoinfo.vault import (
    ayir,
    dogrulama_durumu,
    filter_videos,
    missing_links,
    prepare_vault,
    write_archive,
    write_article,
    yazi_govdesi,
)


def _ayar(tmp_path: Path):
    ayar = default_settings()
    ayar.vault = tmp_path / "vault"
    ayar.data_dir = tmp_path / "data"
    return ayar


def _meta(video_id: str, baslik: str, kanal: str = "Kanal", sure: int = 3600) -> VideoMeta:
    return VideoMeta(
        video_id=video_id,
        kaynak_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        baslik=baslik,
        kanal=kanal,
        platform="youtube",
        yayin_tarihi="2026-11-01",
        sure_saniye=sure,
        ses_dosyasi="ses.m4a",
    )


def _transkript() -> Transcript:
    return Transcript("tr", [Segment(0, 5, "Merhaba"), Segment(5, 10, "Devam")])


def _ozet(*konular: str, kisiler: list[str] | None = None) -> Summary:
    return Summary(
        kisa="- Birinci nokta\n- İkinci nokta",
        bolumler=[SectionSummary(0, 600, "Bölüm özeti.")],
        konular=list(konular),
        kisiler=kisiler or [],
        etiketler=["teknoloji"],
    )


def test_bir_saatlik_bolum_sonu_kacar():
    segmentler = [Segment(i, i + 30, f"cümle {i}") for i in range(0, 3600, 30)]
    bolumler = bolumlere_ayir(segmentler, 600)
    assert len(bolumler) == 6
    assert bolumler[-1][0].start == 3000
    assert bolumler[-1][-1].start == 3570
    ozet = Summary(
        kisa="- kısa",
        bolumler=[
            SectionSummary(grup[0].start, grup[-1].end, f"özet {grup[0].start}")
            for grup in bolumler
        ],
    )
    detay = detayli_metin(
        ozet,
        _meta("abc", "Saatlik"),
    )
    assert "00:50:00" in detay
    assert "t=3000s" in detay
    assert "00:00:00" in detay


def test_json_ve_link_araclari():
    veri = json_cikar('açıklama\n```json\n{"iddialar": ["bir"]}\n```')
    assert veri["iddialar"] == ["bir"]
    assert zaman_linki("https://youtu.be/abc", 90, "youtube") == "https://youtu.be/abc?t=90s"
    assert zaman_linki("https://www.youtube.com/watch?v=abc", 15, "youtube").endswith("&t=15s")
    assert zaman_linki("https://ornek.com/v", 15, "instagram") == "https://ornek.com/v"
    assert norm_url("https://www.Example.com/a/") == norm_url("https://example.com/a")
    assert "[[Yapay Zeka]]" in wikilink_gecir("Yapay Zeka ve Yapay Zeka", ["Yapay Zeka"])
    assert wikilink_gecir("[[Yapay Zeka]]", ["Yapay Zeka"]).count("[[") == 1
    assert dosya_koku("2026-11-01", "a/b:c") == "2026-11-01 a b c"
    assert "{metin}" not in doldur("Başlık {baslik}\n{metin}", baslik="A", metin="olduğu gibi {baslik}")
    assert "madde" in konusma_metni("- madde\n## Başlık")


def test_uydurma_kaynak_hukumu_dusurur():
    bulunan = [SearchResult("Ansiklopedi", "https://www.example.com/paris", "Paris")]
    hukum, kaynaklar = hukmu_uygula(
        "yanlis",
        "değil",
        ["https://uydurma.example/yok"],
        bulunan,
    )
    assert hukum == "dogrulanamadi"
    assert kaynaklar == []
    hukum, kaynaklar = hukmu_uygula(
        "yanlis",
        "değil",
        ["https://example.com/paris"],
        bulunan,
    )
    assert hukum == "yanlis"
    assert kaynaklar[0].url == "https://www.example.com/paris"


def test_uc_video_panolari_ve_konu_baglantilari(tmp_path: Path):
    ayar = _ayar(tmp_path)
    bir = write_archive(
        ayar,
        _meta("bir", "Birinci video", "Kanal A", 300),
        _transkript(),
        _ozet("Yapay Zeka", kisiler=["Ada Lovelace"]),
        None,
        None,
    )
    write_archive(
        ayar,
        _meta("iki", "İkinci video", "Kanal B", 600),
        _transkript(),
        _ozet("Yapay Zeka", "Ekonomi"),
        [ClaimResult("Eyfel Kulesi Berlin'dedir.", "yanlis", "Paris'tedir.", [Source("https://example.com", "Kaynak")])],
        None,
    )
    write_archive(
        ayar,
        _meta("uc", "Üçüncü video", "Kanal A", 120),
        _transkript(),
        _ozet("Ekonomi"),
        [ClaimResult("Su 100 derecede kaynar.", "dogru", "Kaynak destekliyor.", [Source("https://example.com/su", "Su")])],
        None,
    )

    metin = bir.read_text(encoding="utf-8")
    fm, govde = ayir(metin)
    assert fm["durum"] == "okunacak"
    assert fm["dogrulama"] == "yapilmadi"
    assert "[[Yapay Zeka]]" in metin
    assert "## Kısa özet" in govde
    govde_sonlu = govde.rstrip() + "\n\nKullanıcı notu duruyor.\n"
    bir.write_text(frontmatter_durum(metin, "okundu") + "\n" + govde_sonlu, encoding="utf-8")

    write_archive(
        ayar,
        _meta("bir", "Birinci video", "Kanal A", 300),
        _transkript(),
        _ozet("Yapay Zeka", kisiler=["Ada Lovelace"]),
        None,
        None,
    )
    fm, govde = ayir(bir.read_text(encoding="utf-8"))
    assert fm["durum"] == "okundu"
    assert "Kullanıcı notu duruyor." in govde

    assert len(filter_videos(ayar.vault)) == 3
    assert len(filter_videos(ayar.vault, durum="okunacak")) == 2
    assert len(filter_videos(ayar.vault, durum="okundu")) == 1
    assert len(filter_videos(ayar.vault, dogrulama="sorunlu")) == 1
    assert {k.kanal for k in filter_videos(ayar.vault)} == {"Kanal A", "Kanal B"}

    assert missing_links(ayar.vault) == []
    konu = (ayar.vault / "Konular" / "Yapay Zeka.md").read_text(encoding="utf-8")
    assert "[[Videolar/" in konu
    assert "konular.contains(this.file)" in konu
    assert (ayar.vault / "Kisiler" / "Ada Lovelace.md").exists()

    panolar = ayar.vault / "Panolar"
    tum = (panolar / "Tum-videolar.base").read_text(encoding="utf-8")
    assert 'file.inFolder("Videolar")' in tum
    assert 'file.hasTag("video")' in tum
    assert 'durum == "okunacak"' in (panolar / "Okunacaklar.base").read_text(encoding="utf-8")
    assert "note.kanal" in (panolar / "Kanallar.base").read_text(encoding="utf-8")
    assert 'tur == "konu"' in (panolar / "Konular.base").read_text(encoding="utf-8")
    assert 'dogrulama == "sorunlu"' in (panolar / "Sorunlu-iddialar.base").read_text(encoding="utf-8")
    assert (panolar / "Baslangic.md").exists()


def frontmatter_durum(metin: str, durum: str) -> str:
    fm, _ = ayir(metin)
    fm["durum"] = durum
    from videotoinfo.vault import frontmatter_yaz

    return frontmatter_yaz(fm)


def test_config_yolu_ortam_degiskenini_secer(monkeypatch, tmp_path: Path):
    from videotoinfo.config import config_path

    hedef = tmp_path / "benim.toml"
    monkeypatch.setenv("VIDEOTOINFO_CONFIG", str(hedef))
    assert config_path() == hedef
    assert config_path(tmp_path / "acik.toml") == tmp_path / "acik.toml"
    monkeypatch.delenv("VIDEOTOINFO_CONFIG")
    assert config_path() == Path("config.toml")


def test_ayar_dosyasi_okunur(tmp_path: Path):
    yol = tmp_path / "config.toml"
    yol.write_text(
        """
[genel]
veri_klasoru = "%s"
vault = "%s"

[arama]
saglayici = "brave"
max_iddia = 4
""".strip()
        % (tmp_path / "veri", tmp_path / "kasa"),
        encoding="utf-8",
    )
    ayar = load_settings(yol)
    assert ayar.vault == tmp_path / "kasa"
    assert ayar.data_dir == tmp_path / "veri"
    assert ayar.search_provider == "brave"
    assert ayar.max_claims == 4
    assert ayar.model == "qwen3.8:27b-mlx"


def test_bos_arama_dogrulanmis_sayilmaz():
    assert dogrulama_durumu([ClaimResult("x", "dogrulanamadi", "", [])], None) == "dogrulanamadi"
    assert dogrulama_durumu([ClaimResult("x", "yanlis", "", [])], None) == "sorunlu"
    assert dogrulama_durumu(None, "okunacak") == "okunacak"


def test_vault_hazir_bosken(tmp_path: Path):
    vault = tmp_path / "vault"
    prepare_vault(vault)
    assert (vault / ".obsidian" / "types.json").exists()
    assert (vault / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8").find("bases") > 0
    assert (vault / "Gelen kutusu.md").exists()
    assert (vault / "Yazilar").is_dir()
    assert 'file.inFolder("Yazilar")' in (vault / "Panolar" / "Tum-yazilar.base").read_text(encoding="utf-8")
    baslangic = vault / "Panolar" / "Baslangic.md"
    baslangic.write_text("# Eski pano\n", encoding="utf-8")
    prepare_vault(vault)
    guncel = baslangic.read_text(encoding="utf-8")
    assert guncel.startswith("# Eski pano")
    assert "[[Gelen kutusu|Gelen kutusu]]" in guncel
    assert "Tum-yazilar.base" in guncel


def _yazi(yazi_id: str = "abc123def456", baslik: str = "Deneme yazısı") -> YaziMeta:
    return YaziMeta(
        yazi_id=yazi_id,
        url="https://example.com/yazi",
        baslik=baslik,
        yazar="Yazar",
        site="Example",
        yayin_tarihi="2026-11-02",
        kelime=800,
        kismi=False,
    )


def _yazi_ozet() -> YaziOzet:
    return YaziOzet(
        kisa="- Birinci nokta\n- İkinci nokta",
        bolumler=[
            YaziBolumOzet("Giriş", "Giriş özeti."),
            YaziBolumOzet("Asıl konu", "Konu özeti burada."),
        ],
        konular=["Ekonomi"],
        kisiler=["Ada Lovelace"],
        etiketler=["bilim"],
    )


def test_yazi_bolumleme_ve_kimlik():
    uzun = "Bu cümle bölümün yeterince uzun olması için tekrar ediyor. " * 8
    baslikli = metni_bol(
        f"# Başlık\n\nGiriş paragrafı. {uzun}\n\n## Birinci\n\nBirinci metin. {uzun}\n\n## İkinci\n\nİkinci metin. {uzun}\n",
        6000,
        "Yazı",
    )
    assert [bolum.baslik for bolum in baslikli] == ["Başlık", "Birinci", "İkinci"]
    assert "Giriş paragrafı." in baslikli[0].metin

    duz = "Birinci paragraf burada biter.\n\nIkinci paragraf da burada biter.\n\nUcuncu paragraf son olur."
    parcalar = metni_bol(duz, 40, "Yazı")
    assert len(parcalar) >= 2
    assert all(bolum.baslik.startswith("Yazı") for bolum in parcalar)
    assert len(metni_bol("Kısa bir yazı.", 6000, "Başlık")) == 1
    assert not metin_yeterli([YaziBolum("A", "kısa")])
    assert metin_yeterli([YaziBolum("A", "x" * 600)])
    assert yazi_id_uret("https://www.Example.com/a/?utm=1") == yazi_id_uret("https://example.com/a")
    assert kismi_mi("<html>Member-only story</html>")
    assert not kismi_mi("<html>açık yazı</html>")


def test_yazi_onbellekten_okunur(tmp_path: Path):
    ayar = _ayar(tmp_path)
    url = "https://example.com/onbellek"
    kimlik = yazi_id_uret(url)
    meta = YaziMeta(kimlik, url, "Önbellek", "Yazar", "Example", "2026-01-01", 700, False)
    yazi_kaydet(yazi_klasoru(ayar, kimlik), meta, [YaziBolum("Giriş", "x" * 700)])
    yuklenen, bolumler = getir(url, ayar)
    assert yuklenen.baslik == "Önbellek"
    assert bolumler[0].baslik == "Giriş"


def test_kaynaksiz_duzeltme_silinir_ve_eskimis_sorunlu():
    bulunan = [SearchResult("Ansiklopedi", "https://www.example.com/paris", "Paris")]
    hukum, _kaynaklar = hukmu_uygula("yanlis", "değil", ["https://uydurma.example/yok"], bulunan)
    assert hukum == "dogrulanamadi"
    assert duzeltme_temizle(hukum, "Paris'tedir.") == ""
    hukum, kaynaklar = hukmu_uygula("eskimis", "eski", ["https://example.com/paris"], bulunan)
    assert hukum == "eskimis"
    assert kaynaklar[0].url == "https://www.example.com/paris"
    assert duzeltme_temizle(hukum, "Güncel değer 2.") == "Güncel değer 2."
    assert duzeltme_temizle("dogru", "eklenmemeli") == ""
    iddia = ClaimResult("x", "eskimis", "artık değil", [Source("https://example.com", "Kaynak")])
    assert dogrulama_durumu([iddia], None) == "sorunlu"


def test_link_turu():
    assert tur_belirle("https://www.youtube.com/watch?v=abc") == "video"
    assert tur_belirle("https://youtu.be/abc") == "video"
    assert tur_belirle("https://medium.com/@a/yazi") == "yazi"
    assert tur_belirle("https://x.com/a/status/1") == "desteklenmiyor"
    assert tur_belirle("https://twitter.com/a/status/1") == "desteklenmiyor"
    assert tur_belirle("https://example.com/dosya.PDF?dl=1") == "desteklenmiyor"


def test_gelen_kutusu_satirlarini_korur(tmp_path: Path):
    vault = tmp_path / "vault"
    prepare_vault(vault)
    yol = vault / "Gelen kutusu.md"
    yol.write_text(
        "# Gelen kutusu\n\nGiriş notu duruyor.\n\n"
        "## Bekleyenler\n\n"
        "- [ ] https://example.com/bir\n"
        "- https://example.com/iki\n\n"
        "## İşlenenler\n",
        encoding="utf-8",
    )
    assert bekleyen_urller(yol) == ["https://example.com/bir", "https://example.com/iki"]
    hata_yaz(yol, "https://example.com/bir", "sayfa açılmadı")
    metin = yol.read_text(encoding="utf-8")
    metin = metin.replace("## İşlenenler", "- [ ] https://example.com/uc\n\n## İşlenenler")
    yol.write_text(metin, encoding="utf-8")
    tamamla(
        yol,
        "https://example.com/iki",
        "- [x] [[Yazilar/x|İki]] · dogrulandi · [kaynak](https://example.com/iki)",
    )
    bekleyen = bekleyen_urller(yol)
    assert "https://example.com/iki" not in bekleyen
    assert bekleyen == ["https://example.com/bir", "https://example.com/uc"]
    metin = yol.read_text(encoding="utf-8")
    assert "Giriş notu duruyor." in metin
    assert "hata: sayfa açılmadı" in metin
    assert "[[Yazilar/x|İki]]" in metin
    hata_yaz(yol, "https://example.com/bir", "yine olmadı")
    metin = yol.read_text(encoding="utf-8")
    assert metin.count("hata:") == 1
    assert "yine olmadı" in metin
    assert "https://example.com/uc" in bekleyen_urller(yol)


def test_yazi_notu_duzeltmeyi_bolum_altina_yazar(tmp_path: Path):
    ayar = _ayar(tmp_path)
    write_archive(
        ayar,
        _meta("video1", "Ekonomi videosu", "Kanal A", 300),
        _transkript(),
        _ozet("Ekonomi"),
        None,
        None,
    )
    iddialar = [
        ClaimResult(
            "İddia A doğrudur.",
            "dogru",
            "kaynak destekliyor",
            [Source("https://example.com/a", "A")],
            bolum=0,
        ),
        ClaimResult(
            "İddia B yanlıştır.",
            "yanlis",
            "kaynak çürütüyor",
            [Source("https://example.com/b", "B")],
            duzeltme="Doğrusu budur.",
            bolum=1,
        ),
        ClaimResult(
            "Eski nüfus sayısı.",
            "eskimis",
            "artık değil",
            [Source("https://example.com/c", "C")],
            duzeltme="Güncel sayı 10.",
            bolum=None,
        ),
    ]
    yol = write_article(ayar, _yazi(), _yazi_ozet(), iddialar)
    fm, govde = ayir(yol.read_text(encoding="utf-8"))
    assert fm["durum"] == "okunacak"
    assert fm["dogrulama"] == "sorunlu"
    assert fm["sure_dk"] == 4
    assert "yazi" in fm["tags"]
    assert govde.index("Giriş özeti.") < govde.index("Konu özeti burada.")
    assert govde.index("Konu özeti burada.") < govde.index("Düzeltme (yanlış)")
    assert govde.index("Düzeltme (yanlış)") < govde.index("Diğer düzeltmeler")
    assert "Düzeltme (eskimiş)" in govde
    assert govde.index("## İddialar") > govde.index("Düzeltme (eskimiş)")
    assert govde.split("## İddialar")[0].count("İddia A doğrudur.") == 0
    assert "- **Doğrusu:** Doğrusu budur." in govde
    assert "[B](https://example.com/b)" in govde

    govde_sonlu = govde.rstrip() + "\n\nKullanıcı notu duruyor.\n"
    yol.write_text(frontmatter_durum(yol.read_text(encoding="utf-8"), "okundu") + "\n" + govde_sonlu, encoding="utf-8")
    write_article(ayar, _yazi(), _yazi_ozet(), iddialar)
    fm, govde = ayir(yol.read_text(encoding="utf-8"))
    assert fm["durum"] == "okundu"
    assert "Kullanıcı notu duruyor." in govde
    assert "Düzeltme (yanlış)" in govde

    assert len(filter_videos(ayar.vault)) == 1
    assert missing_links(ayar.vault) == []
    konu = (ayar.vault / "Konular" / "Ekonomi.md").read_text(encoding="utf-8")
    assert "[[Videolar/" in konu
    assert "[[Yazilar/" in konu
    assert 'file.inFolder("Yazilar")' in konu
    kisi = ayar.vault / "Kisiler" / "Ada Lovelace.md"
    assert kisi.exists()
    assert "[[Yazilar/" in kisi.read_text(encoding="utf-8")

    kismi = _yazi()
    kismi.kismi = True
    assert "üyelere özel" in yazi_govdesi(kismi, _yazi_ozet(), None)


def test_yazi_ayar_bolum_karakter(tmp_path: Path):
    yol = tmp_path / "config.toml"
    yol.write_text("[yazi]\nbolum_karakter = 4000\n", encoding="utf-8")
    ayar = load_settings(yol)
    assert ayar.section_chars == 4000


# --- dil, erişim, paketleme ve doğrulama yardımcıları ---

from videotoinfo.factcheck import Haric, _bolum_bul, _json_iddialar, _paketler, ilgili_kisim  # noqa: E402
from videotoinfo.summarize import _no_ozetleri, paketle  # noqa: E402
from videotoinfo.textutil import cikti_dili, dil_tespit, dilde_gorunuyor  # noqa: E402
from videotoinfo.yazi import _Sayfa, akis_birlestir, gurultu_temizle, kopya_uygun  # noqa: E402


def test_dil_tespit_ve_cikti_dili():
    assert dil_tespit("I recently read this piece and it was fun to read, so I wrote this for you.") == "en"
    assert dil_tespit("Bu yazıda yapay zeka ile ilgili bir çok terim var ve açıklanıyor.") == "tr"
    assert cikti_dili("kaynak", "en") == "en"
    assert cikti_dili("kaynak", "diger") == "tr"
    assert cikti_dili("tr", "en") == "tr"
    assert dilde_gorunuyor("- The article explains what a harness is and why it matters.", "en")


def test_gorev_modeli(tmp_path: Path):
    yol = tmp_path / "config.toml"
    yol.write_text('[ollama]\nmodel = "ana"\nmodel_hukum = "buyuk"\nthink_hukum = "low"\n', encoding="utf-8")
    ayar = load_settings(yol)
    assert ayar.gorev("ozet").model == "ana"
    assert ayar.gorev("hukum").model == "buyuk"
    assert ayar.gorev("hukum").think == "low"
    assert ayar.gorev("iddia").think == ayar.think


def test_gurultu_temizle():
    metin = "Member-only story\n\nListen\n\nShare\n\nGerçek paragraf burada.\n\n12\n"
    assert gurultu_temizle(metin) == "Gerçek paragraf burada."


def test_akis_birlestir_gizli_icerigi_tasir():
    html = (
        "<html><body><article><p>Bir</p><template id=\"P:1\"></template></article>"
        "<div hidden id=\"S:1\"><h3>7. Computer Use</h3><p>İki</p></div></body></html>"
    )
    sonuc = akis_birlestir(html)
    assert sonuc.index("Bir") < sonuc.index("7. Computer Use") < sonuc.index("İki")
    assert "template" not in sonuc


def _sayfa(govde: str, yazar: str = "Jim Clyde Monge", baslik: str = "8 New AI Terms") -> _Sayfa:
    return _Sayfa(url="https://x", baslik=baslik, yazar=yazar, site="", tarih=None, govde=govde, kismi=False)


def test_kopya_uygun_yazar_ve_metin_ister():
    gorunen = "I recently read this piece from Shreyas Naphad and had fun reading it, so here we go."
    asil = _sayfa(gorunen)
    tam = gorunen + "\n\n" + "More content. " * 200
    assert kopya_uygun(asil, _sayfa(tam))
    assert not kopya_uygun(asil, _sayfa(tam, yazar="Başka Biri"))
    assert not kopya_uygun(asil, _sayfa(tam, yazar=""))
    assert not kopya_uygun(asil, _sayfa("Different start. " * 200))
    assert not kopya_uygun(asil, _sayfa(tam, baslik="Something else"))


def test_yazi_paketleme_ve_no_ozetleri():
    bolumler = [YaziBolum(f"B{i}", "x" * 3000) for i in range(5)]
    paketler = paketle(bolumler, 7000)
    assert [len(p) for p in paketler] == [2, 2, 1]
    assert paketler[1][0][0] == 2
    assert _no_ozetleri({"bolumler": [{"no": 1, "ozet": "a"}, {"no": "B3", "ozet": "c"}, {"no": "?", "ozet": "d"}]}) == {0: "a", 2: "c"}


def test_iddia_paketleri_ve_bolum_eslesmesi():
    parcalar = ["Harness gives the model tools.", "Skills live in SKILL.md files.", "z" * 50]
    paketler = _paketler(parcalar, 6000)
    assert len(paketler) == 1
    assert _bolum_bul("Agent Skills store instructions in SKILL.md files", paketler[0]) == 1
    assert _json_iddialar('{"iddialar": [{"iddia": "A B", "sorgu": "a b"}, "C"]}') == [("A B", "a b"), ("C", "C")]


def test_haric_ve_ilgili_kisim():
    haric = Haric(urller=("https://medium.com/x/yazi",), baslik="8 new ai terms you should know")
    assert haric.dislar(SearchResult("Herhangi", "https://freedium.net/yazi", ""))
    assert haric.dislar(SearchResult("Herhangi", "https://medium.com/x/yazi?source=a", ""))
    assert haric.dislar(SearchResult("8 New AI Terms You Should Know | LinkedIn", "https://linkedin.com/p", ""))
    assert not haric.dislar(SearchResult("OpenAI harness engineering", "https://openai.com/h", ""))
    metin = "\n".join(["Alakasız uzun bir paragraf burada duruyor ve hiçbir şey söylemiyor."] * 30 + ["SKILL.md files store skill instructions for agents."])
    secilen = ilgili_kisim(metin, "Skills are stored in SKILL.md files", 200)
    assert "SKILL.md" in secilen and len(secilen) <= 200


def test_kismi_mi_medium_kilit_bilgisi():
    assert kismi_mi('<span>Member-only story</span>"isLockedPreviewOnly":true')
    assert not kismi_mi('<span>Member-only story</span>"isLockedPreviewOnly":false')
    assert kismi_mi("<span>Member-only story</span>")
    assert not kismi_mi("<p>serbest yazı</p>")


# --- bağlantılar, bölümleme, tür bazlı not ve kaynak seçimi ---

from videotoinfo.factcheck import kaynak_puani, sirala, yazi_baglantilari  # noqa: E402
from videotoinfo.models import Baglanti, YaziOge  # noqa: E402
from videotoinfo.search import baslik_temizle  # noqa: E402
from videotoinfo.summarize import oge_baglantisi  # noqa: E402
from videotoinfo.textutil import temiz_url  # noqa: E402
from videotoinfo.vault import isaretleri_koru  # noqa: E402
from videotoinfo.yazi import baglanti_ayikla, html_hazirla  # noqa: E402

_UZUN = "Bu cümle bölümün yeterince uzun olması için tekrar ediyor. " * 8


def test_frontmatter_url_icindeki_tireler_bolmez(tmp_path: Path):
    metin = '---\nyazi_id: "abc"\nkaynak: "https://x.io/a?source=home---------2-98-----"\n---\nGövde\n---\nSon'
    fm, govde = ayir(metin)
    assert fm["yazi_id"] == "abc"
    assert govde == "Gövde\n---\nSon"
    assert ayir("---\n---\nboş") == ({}, "boş")
    assert ayir("başlıksız") == ({}, "başlıksız")


def test_temiz_url_takip_parametrelerini_atar():
    assert temiz_url("https://a.io/y?gi=1&source=home---2&utm_medium=x&id=5") == "https://a.io/y?id=5"
    assert temiz_url("https://a.io/y") == "https://a.io/y"


def test_bolumleme_ana_baslik_kisa_bolum_ve_tanitim():
    md = (
        "# Yazının Adı\n\n"
        "## Bu yazı, bir alanı sıfırdan öğrenmek isteyenler için hazırlanmış bir yol haritasıdır.\n\n"
        f"Giriş. {_UZUN}\n\n"
        "## Phase 1: Temeller\n\n"
        f"Temel metin. {_UZUN}\n\n"
        "### Resources 💻\n\n"
        "- [**CS50 Python**](https://cs50.harvard.edu/python/?source=post_page---x) (sertifika)\n\n"
        "**4. Optimization**\n\n"
        f"Optimizasyon anlatımı. {_UZUN}\n\n"
        "![görsel](https://img.example/a.png)\n\n"
        "## Kısa\n\nÇok kısa bölüm.\n\n"
        "## Phase 2: Matematik\n\n"
        f"Matematik metni. {_UZUN}\n\n"
        "## Ready to go beyond reading? Here’s how I can help\n\n"
        "[Bundle](https://x.gumroad.com/l/a?utm_source=medium_post_bottom_cta) satın alın.\n\n"
        "## 2. Working on Something Bigger?\n\nBenimle çalışın.\n"
    )
    bolumler = metni_bol(md, 6000, "Yazının Adı", "https://blog.example/yazi")
    assert [b.baslik for b in bolumler] == ["Giriş", "Phase 1: Temeller", "Phase 2: Matematik"]
    assert "yol haritasıdır" in bolumler[0].metin
    faz1 = bolumler[1]
    assert "### 4. Optimization" in faz1.metin
    assert "Çok kısa bölüm." in faz1.metin
    assert "CS50 Python" in faz1.metin and "](" not in faz1.metin and "img.example" not in faz1.metin
    assert faz1.baglantilar == [Baglanti("CS50 Python", "https://cs50.harvard.edu/python/")]
    assert all("Bundle" not in b.metin and "Benimle" not in b.metin for b in bolumler)


def test_tek_ust_baslik_ana_duzey_sayilmaz():
    md = (
        "# Adı\n\n## Bu yazıda bilmeniz gereken yeni terimleri tek tek açıklıyorum.\n\n"
        + "".join(f"### {i}. Terim\n\n{_UZUN}\n\n" for i in range(1, 4))
    )
    assert [b.baslik for b in metni_bol(md, 6000, "Adı")] == ["1. Terim", "2. Terim", "3. Terim"]


def test_uzun_grup_alt_basliktan_bolunur():
    md = "## Ana\n\n" + "".join(f"### Alt {i}\n\n{_UZUN}\n\n" for i in range(6)) + "## Son\n\n" + _UZUN
    bolumler = metni_bol(md, 1200, "Yazı")
    assert bolumler[0].baslik == "Ana"
    assert bolumler[1].baslik.startswith("Ana — Alt ")
    assert bolumler[-1].baslik == "Son"


def test_baglanti_ayikla_ic_baglantilari_atar():
    duz, baglar = baglanti_ayikla(
        "[Yazar](https://medium.com/@yazar) okudu [P](https://x.io/p_(a)) ve [kendisi](https://b.io/yazi).",
        "https://b.io/yazi",
    )
    assert duz == "Yazar okudu P ve kendisi."
    assert baglar == [Baglanti("P", "https://x.io/p_(a)")]


def test_sondaki_yayin_altbilgisi_atilir():
    metin = (
        "Connect with us on LinkedIn if this helps, says the intro.\n\n"
        "Asıl içerik burada.\n\n"
        "This story is published under the [Generative AI](https://generativeai.pub/) publication. "
        "Connect with us on [LinkedIn](https://www.linkedin.com/company/x)."
    )
    temiz = gurultu_temizle(metin)
    assert temiz.startswith("Connect with us")
    assert temiz.endswith("Asıl içerik burada.")


def test_tanitim_paragrafi_ve_bolunmus_baglanti():
    metin = (
        "I help developers through the [**Bundle**](https://gum.co/u/abc) and work with "
        "[teams](https://example.com/services).\n\nAsıl içerik burada."
    )
    assert gurultu_temizle(metin) == "Asıl içerik burada."
    _, baglar = baglanti_ayikla("### P[ortfolio Project #1](https://example.com/p) ve [x](https://example.com/x)")
    assert [b.metin for b in baglar] == ["Portfolio Project #1", "x"]


def test_html_hazirla_kart_ve_altyazi():
    html = (
        "<html><body><article><p>Paragraf bir uzunca.</p>"
        '<div><a href="/kurs?source=post_page---x"><div><h2>3Blue1Brown</h2><h3>Görsel matematik</h3>'
        "<p>3blue1brown.com</p></div></a></div>"
        '<p><img src="a.png" alt="Harness"></p><p>Harness</p>'
        "<figure><img src='b.png'><figcaption>Şekil 1</figcaption></figure>"
        "<p>Son cümle.</p></article></body></html>"
    )
    sonuc = html_hazirla(html, "https://site.io/yazi")
    assert '<a href="https://site.io/kurs?source=post_page---x">3Blue1Brown</a>: Görsel matematik' in sonuc
    assert "<p>Harness</p>" not in sonuc
    assert "Şekil 1" not in sonuc
    assert "Son cümle." in sonuc


def test_oto_konu_ve_etiketler_birikmez(tmp_path: Path):
    ayar = _ayar(tmp_path)
    ozet = _yazi_ozet()
    yol = write_article(ayar, _yazi(), ozet, None)
    metin = yol.read_text(encoding="utf-8").replace('  - "bilim"', '  - "bilim"\n  - "benim"')
    yol.write_text(metin, encoding="utf-8")
    ozet.konular, ozet.etiketler = ["Tarih"], ["arsiv"]
    write_article(ayar, _yazi(), ozet, None)
    fm, _ = ayir(yol.read_text(encoding="utf-8"))
    assert fm["konular"] == ["[[Tarih]]"]
    assert fm["tags"] == ["yazi", "arsiv", "benim"]
    assert len(list((ayar.vault / "Yazilar").glob("*.md"))) == 1


def test_tur_bazli_yazi_notu(tmp_path: Path):
    ozet = _yazi_ozet()
    ozet.tur = "liste"
    ozet.cikarimlar = ["Önce yerel aracı dene."]
    ozet.ogeler = [YaziOge("Semble", "Anlama göre kod arar | yerel", "https://github.com/x/semble"), YaziOge("Serena", "LSP")]
    ozet.bolumler[1].baglantilar = [Baglanti("Semble deposu", "https://github.com/x/semble")]
    govde = yazi_govdesi(_yazi(), ozet, None)
    assert "## Çıkarımlar\n\n- Önce yerel aracı dene." in govde
    assert "| **Semble** | Anlama göre kod arar \\| yerel | [aç](https://github.com/x/semble) |" in govde
    assert "| **Serena** | LSP |  |" in govde
    assert "## Yazıdaki bağlantılar\n\n**Asıl konu**\n- [Semble deposu](https://github.com/x/semble)" in govde
    assert govde.index("## Öğeler") < govde.index("## Detaylı özet") < govde.index("## Yazıdaki bağlantılar")

    ozet.tur, ozet.ogeler, ozet.adimlar = "yol_haritasi", [], ["Python öğren", "Matematik çalış"]
    govde = yazi_govdesi(_yazi(), ozet, None)
    assert "## Adımlar\n\n- [ ] Python öğren\n- [ ] Matematik çalış" in govde
    korunan = isaretleri_koru(govde, "eski\n- [x] Python öğren\n")
    assert "- [x] Python öğren" in korunan and "- [ ] Matematik çalış" in korunan


def test_anlatici_kalibi_temizlenir():
    from videotoinfo.textutil import anlatici_temizle

    metin = "Kod kolay. The section highlights that delivery is hard.\n- The article notes that apps grew.\nThe author argues that AI fails."
    assert anlatici_temizle(metin) == "Kod kolay. Delivery is hard.\n- Apps grew.\nThe author argues that AI fails."
    assert anlatici_temizle("- The article claims that Lovable is doomed.") == "- The author claims that Lovable is doomed."


def test_gorus_yazisi_tez_ve_dayanak():
    ozet = _yazi_ozet()
    ozet.tur, ozet.tez = "gorus", "Kuralları şartnameye yaz, kodu bağımsız doğrula."
    ozet.dayanak = ["Başlıktaki %1 iddiası için veri yok."]
    ozet.cikarimlar = ["Kuralları YAML ile yaz."]
    govde = yazi_govdesi(_yazi(), ozet, None)
    assert "> [!abstract] Ana tez\n> Kuralları şartnameye yaz, kodu bağımsız doğrula." in govde
    assert "## Dayanak ve eleştiri\n\n- Başlıktaki %1 iddiası için veri yok." in govde
    assert govde.index("Ana tez") < govde.index("## Kısa özet") < govde.index("## Çıkarımlar") < govde.index("## Dayanak")
    assert YaziOzet.from_dict(ozet.as_dict()).dayanak == ozet.dayanak


def test_oge_baglantisi_bolumden_bulunur():
    bolumler = [
        YaziBolum("1. Semble", "metin", [Baglanti("repo", "https://github.com/minishlab/semble")]),
        YaziBolum("2. Diğer", "metin", [Baglanti("Serena MCP", "https://github.com/oraios/serena")]),
    ]
    assert oge_baglantisi("Semble", bolumler) == "https://github.com/minishlab/semble"
    assert oge_baglantisi("Serena", bolumler) == "https://github.com/oraios/serena"
    assert oge_baglantisi("Yok", bolumler) == ""


def test_chat_anahtarsiz(monkeypatch):
    import sys
    from types import SimpleNamespace

    import ollama

    from videotoinfo.llm import _langfuse, chat

    for ad in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST", "LANGFUSE_TRACING_ENABLED"):
        monkeypatch.delenv(ad, raising=False)
    sys.modules.pop("langfuse", None)
    assert _langfuse() is None
    assert "langfuse" not in sys.modules
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    assert _langfuse() is None
    assert "langfuse" not in sys.modules

    cagrilar = []

    def sahte(**kwargs):
        cagrilar.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content="tamam", thinking=None), prompt_eval_count=4, eval_count=1)

    monkeypatch.setattr(ollama, "chat", sahte)
    metin, token = chat(default_settings(), "merhaba")
    assert metin == "tamam" and token == 4 and len(cagrilar) == 1


def test_chat_langfuse_hatasi_modeli_bir_kez_calistirir(monkeypatch):
    from types import SimpleNamespace

    import ollama

    from videotoinfo.llm import chat

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    cagrilar = []

    def sahte(**kwargs):
        cagrilar.append(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(content="tamam", thinking=None),
            prompt_eval_count=4,
            eval_count=1,
            load_duration=1_000_000,
            prompt_eval_duration=2_000_000,
            eval_duration=2_000_000_000,
        )

    class _Gen:
        def update(self, **kwargs):
            raise RuntimeError("langfuse update")

        def end(self, **kwargs):
            raise RuntimeError("langfuse end")

    class _Istemci:
        def start_observation(self, **kwargs):
            return _Gen()

    monkeypatch.setattr("videotoinfo.llm._langfuse", lambda: _Istemci())
    monkeypatch.setattr(ollama, "chat", sahte)
    metin, token = chat(default_settings(), "merhaba", iz="deneme")
    assert metin == "tamam" and token == 4 and len(cagrilar) == 1


def test_chat_bos_yanitta_kaydi_hata_ile_kapatir(monkeypatch):
    from types import SimpleNamespace

    import ollama
    import pytest

    from videotoinfo.llm import chat

    olaylar = []

    class _Gen:
        def update(self, **kwargs):
            olaylar.append(kwargs)

        def end(self, **kwargs):
            olaylar.append({"end": True})

    class _Istemci:
        def start_observation(self, **kwargs):
            return _Gen()

    monkeypatch.setattr("videotoinfo.llm._langfuse", lambda: _Istemci())
    monkeypatch.setattr(ollama, "chat", lambda **k: SimpleNamespace(message=None))
    with pytest.raises(RuntimeError, match="Model yanıtı boş"):
        chat(default_settings(), "merhaba")
    assert olaylar[0]["level"] == "ERROR" and olaylar[-1] == {"end": True}


def test_is_izi_kayit_acilamazsa_govde_bir_kez_calisir(monkeypatch):
    import pytest

    from videotoinfo import llm

    class _Istemci:
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError("langfuse yok")

        def flush(self):
            pass

    monkeypatch.setattr(llm, "_langfuse", lambda: _Istemci())
    calisti = []
    with llm.is_izi("video", "k1", {}):
        calisti.append(1)
    assert calisti == [1]
    with pytest.raises(ValueError) as bilgi:
        with llm.is_izi("video", "k1", {}):
            raise ValueError("iş hatası")
    assert bilgi.value.__context__ is None


def test_sohbet_akisi_iptal_kaydi_kapatir(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import ollama

    from videotoinfo.sohbet import sor

    for ad in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        monkeypatch.delenv(ad, raising=False)

    class _Parca:
        def __init__(self, content, done=False):
            self.message = SimpleNamespace(content=content, thinking=None)
            self.done = done
            self.prompt_eval_count = 2 if done else None
            self.eval_count = 1 if done else None

    def sahte(**kwargs):
        yield _Parca("merhaba ")
        yield _Parca("dünya", done=True)

    guncellemeler = []

    class _Gen:
        def update(self, **kwargs):
            guncellemeler.append(kwargs)

        def end(self):
            guncellemeler.append({"end": True})

    class _Istemci:
        def start_observation(self, **kwargs):
            guncellemeler.append(kwargs)
            return _Gen()

    monkeypatch.setattr("videotoinfo.sohbet._langfuse", lambda: _Istemci())
    monkeypatch.setattr(ollama, "chat", sahte)
    akis = sor(default_settings(), tmp_path, "ne dedi?")
    assert next(akis) == "merhaba "
    akis.close()
    assert any(g.get("status_message") == "iptal" and g.get("level") == "WARNING" for g in guncellemeler)
    assert guncellemeler[-1] == {"end": True}
    assert guncellemeler[0]["name"] == "sohbet"


def test_kaynak_secimi_ve_baslik_temizligi():
    iddia = "OpenAI published an article on harness engineering for Codex."
    baglar = [Baglanti("harness engineering", "https://openai.com/index/harness-engineering/"), Baglanti("Slack", "https://slack.com")]
    yazidan = yazi_baglantilari(iddia, baglar)
    assert [s.url for s in yazidan] == ["https://openai.com/index/harness-engineering/"] and yazidan[0].yazidan
    sonuclar = [
        SearchResult("Blog", "https://medium.com/@a/harness", ""),
        SearchResult("Rastgele", "https://unlimitedai.org/harness", ""),
        SearchResult("Resmi", "https://openai.com/index/harness-engineering/", ""),
    ]
    assert [s.baslik for s in sirala(sonuclar, iddia)] == ["Resmi", "Rastgele", "Blog"]
    assert kaynak_puani(SearchResult("CS50", "https://cs50.harvard.edu/python/", ""), "Harvard offers CS50") >= 5
    yapisik = "[2311.02462] Levels of AGI for Operationalizing Progress on ...Levels of AGI for Operationalizing Progress on the Path to AGI"
    assert baslik_temizle(yapisik) == "[2311.02462] Levels of AGI for Operationalizing Progress on…"
    assert baslik_temizle("Wait…PyTorch Or TensorFlow?") == "Wait…PyTorch Or TensorFlow?"
