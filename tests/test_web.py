import tomllib

from videotoinfo.web.server import adim_bul, toml_guncelle

ORNEK = """# yorum
[genel]
vault = "~/a"   

[ollama]
model = "x"
num_ctx = 16384
"""


def test_toml_guncelle_yorumu_korur_ve_gecerli_kalir():
    cikti = toml_guncelle(ORNEK.splitlines(), {("ollama", "num_ctx"): 8192, ("genel", "vault"): '~/"b"'})
    veri = tomllib.loads(cikti)
    assert veri["ollama"]["num_ctx"] == 8192
    assert veri["genel"]["vault"] == '~/"b"'
    assert veri["ollama"]["model"] == "x"
    assert cikti.startswith("# yorum")


def test_toml_guncelle_eksik_anahtar_ve_bolum_ekler():
    cikti = toml_guncelle(ORNEK.splitlines(), {("ollama", "think"): "low", ("ses", "ses"): "Yelda"})
    veri = tomllib.loads(cikti)
    assert veri["ollama"]["think"] == "low"
    assert veri["ollama"]["num_ctx"] == 16384
    assert veri["ses"]["ses"] == "Yelda"


def test_adim_bul_ilerler_ve_hatada_sabit_kalir():
    assert adim_bul("Video bilgisi alınıyor", 0) == 0
    assert adim_bul("Yazıya dökülüyor: x", 0) == 1
    assert adim_bul("Özet hazır. Bağlam: 1/2 token", 1) == 2
    assert adim_bul("Doğrulama bitti (4 iddia)", 2) == 3
    assert adim_bul("Hata: bir şey", 3) == 3
    assert adim_bul("Not yazıldı: x", 3) == 5


def _kayit_kur(tmp_path):
    import json

    from videotoinfo.config import Settings
    from videotoinfo.vault import frontmatter_yaz

    ayar = Settings(data_dir=tmp_path / "data", vault=tmp_path / "vault")
    klasor = ayar.data_dir / "youtube_abc"
    klasor.mkdir(parents=True)
    (klasor / "meta.json").write_text(json.dumps({"video_id": "youtube_abc", "baslik": "Deneme", "url": "https://youtu.be/abc"}), encoding="utf-8")
    (klasor / "transcript.json").write_text(json.dumps({"dil": "tr", "segmentler": [{"start": 0, "text": "elma armut"}, {"start": 65, "text": "kuvvet ivme"}]}), encoding="utf-8")
    (klasor / "ses.json").write_text(json.dumps({"dosya": "deneme.m4a"}), encoding="utf-8")
    for ad in ("Videolar", "Transkriptler", "Sesler"):
        (ayar.vault / ad).mkdir(parents=True)
    (ayar.vault / "Videolar" / "Deneme.md").write_text(frontmatter_yaz({"video_id": "youtube_abc", "tags": ["video"]}) + "\nx\n", encoding="utf-8")
    (ayar.vault / "Transkriptler" / "Deneme.md").write_text("t", encoding="utf-8")
    (ayar.vault / "Sesler" / "deneme.m4a").write_bytes(b"0")
    (ayar.vault / "Videolar" / "Baska.md").write_text(frontmatter_yaz({"video_id": "youtube_xyz"}) + "\ny\n", encoding="utf-8")
    return ayar, klasor


def test_kayit_sil_tum_izleri_kaldirir(tmp_path):
    from videotoinfo.web.server import kayit_sil

    ayar, klasor = _kayit_kur(tmp_path)
    silinen = kayit_sil(ayar, "youtube_abc")
    assert not klasor.exists()
    assert not (ayar.vault / "Videolar" / "Deneme.md").exists()
    assert not (ayar.vault / "Transkriptler" / "Deneme.md").exists()
    assert not (ayar.vault / "Sesler" / "deneme.m4a").exists()
    assert (ayar.vault / "Videolar" / "Baska.md").exists()
    assert len(silinen) == 4


def test_parca_sec_sigmayinca_ilgili_parcalari_sirali_secer():
    from videotoinfo.sohbet import parca_sec

    parcalar = ["elma armut " * 10, "kuvvet ivme " * 10, "elma kuvvet " * 10]
    assert parca_sec(parcalar, "x", 10_000) == parcalar
    secilen = parca_sec(parcalar, "kuvvet nedir", 250)
    assert secilen == [parcalar[1], parcalar[2]]


def test_baglam_zaman_damgali_transkript_icerir(tmp_path):
    from videotoinfo.sohbet import baglam_metni

    _, klasor = _kayit_kur(tmp_path)
    metin = baglam_metni(klasor, "ivme", 10_000)
    assert "Başlık: Deneme" in metin
    assert "[0:00] elma armut kuvvet ivme" in metin


def test_sohbet_nota_yansir_ve_temizlenince_kalkar(tmp_path):
    import json

    from videotoinfo.sohbet import NOT_BAS, gecmis_sil, gecmis_yaz, nota_yaz

    ayar, klasor = _kayit_kur(tmp_path)
    yol = ayar.vault / "Videolar" / "Deneme.md"
    gecmis_yaz(klasor, [{"rol": "user", "icerik": "Ne anlatıyor?", "zaman": 0}, {"rol": "assistant", "icerik": "Kuvveti [1:05] anlatıyor."}])
    json.loads((klasor / "meta.json").read_text())
    meta = json.loads((klasor / "meta.json").read_text()) | {"platform": "youtube"}
    (klasor / "meta.json").write_text(json.dumps(meta))
    nota_yaz(ayar, klasor)
    metin = yol.read_text(encoding="utf-8")
    assert "> [!question] Soru" in metin and "> Ne anlatıyor?" in metin
    assert "[1:05](https://youtu.be/abc?t=65s)" in metin
    nota_yaz(ayar, klasor)
    assert yol.read_text(encoding="utf-8").count(NOT_BAS) == 1
    gecmis_sil(klasor)
    nota_yaz(ayar, klasor)
    assert NOT_BAS not in yol.read_text(encoding="utf-8")
    assert "x" in yol.read_text(encoding="utf-8")


def test_video_bilgisi_aciklamayi_etiketsiz_yazar():
    from videotoinfo.models import VideoMeta
    from videotoinfo.vault import video_bilgisi

    meta = VideoMeta("youtube_a", "a", "https://www.youtube.com/watch?v=a", "Başlık", "Kanal", "youtube", "2025-01-01", 125, "ses.webm", aciklama="Merhaba #fizik\nhttps://x.com/a#b")
    md = video_bilgisi(meta)
    assert "![](https://www.youtube.com/watch?v=a)" in md
    assert "**Kanal:** Kanal" in md and "00:02:05" in md
    assert "> [!info]- Video açıklaması" in md
    assert "> Merhaba \\#fizik" in md and "x.com/a#b" in md
