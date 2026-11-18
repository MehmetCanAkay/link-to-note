"""Komut satırı: vti add, vti oku, vti kuyruk."""

from __future__ import annotations

from pathlib import Path

import typer

from videotoinfo.config import load_settings
from videotoinfo.download import IndirmeHatasi
from videotoinfo.inbox import kuyrugu_isle, tur_belirle
from videotoinfo.llm import measure_context
from videotoinfo.pipeline import isle, yazi_isle
from videotoinfo.vault import prepare_vault
from videotoinfo.yazi import YaziHatasi

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _ayar(config: Path | None):
    return load_settings(config)


@app.command("add")
def add(
    link: str = typer.Argument(..., help="Video bağlantısı"),
    dogrula: bool = typer.Option(False, "--dogrula", help="İddiaları web kaynaklarıyla kontrol et"),
    ses: bool = typer.Option(False, "--ses", help="Kısa özeti Türkçe seslendir"),
    bastan: bool = typer.Option(False, "--bastan", help="Sesi tutup türetilmiş çıktıları yeniden üret"),
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """Videoyu indir, Türkçe özetle ve Obsidian notu yaz."""
    try:
        isle(
            link,
            _ayar(config),
            dogrula_iste=dogrula,
            ses=ses,
            bastan=bastan,
            log=lambda mesaj: typer.echo(mesaj),
        )
    except (IndirmeHatasi, RuntimeError) as hata:
        typer.echo(f"Hata: {hata}", err=True)
        raise typer.Exit(1) from hata


@app.command("oku")
def oku_cmd(
    link: str = typer.Argument(..., help="Yazı bağlantısı"),
    dogrulamasiz: bool = typer.Option(False, "--dogrulamasiz", help="İddiaları web'de kontrol etme"),
    bastan: bool = typer.Option(False, "--bastan", help="Önbelleği yok sayıp yeniden üret"),
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """Yazıyı özetle, iddialarını kontrol et ve Obsidian notu yaz."""
    tur = tur_belirle(link)
    if tur == "video":
        typer.echo("Bu bir video bağlantısı. `vti add` kullanın.", err=True)
        raise typer.Exit(1)
    if tur == "desteklenmiyor":
        typer.echo("Twitter/X ve PDF henüz desteklenmiyor.", err=True)
        raise typer.Exit(1)
    try:
        yazi_isle(
            link,
            _ayar(config),
            dogrula_iste=not dogrulamasiz,
            bastan=bastan,
            log=lambda mesaj: typer.echo(mesaj),
        )
    except (YaziHatasi, RuntimeError) as hata:
        typer.echo(f"Hata: {hata}", err=True)
        raise typer.Exit(1) from hata


@app.command("kuyruk")
def kuyruk_cmd(
    dogrulamasiz: bool = typer.Option(False, "--dogrulamasiz", help="İddiaları web'de kontrol etme"),
    bastan: bool = typer.Option(False, "--bastan", help="Önbelleği yok sayıp yeniden üret"),
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """Gelen kutusu notundaki bekleyen bağlantıları işle."""
    try:
        hatali = kuyrugu_isle(
            _ayar(config),
            dogrula_iste=not dogrulamasiz,
            bastan=bastan,
            log=lambda mesaj: typer.echo(mesaj),
        )
    except RuntimeError as hata:
        typer.echo(f"Hata: {hata}", err=True)
        raise typer.Exit(1) from hata
    if hatali:
        raise typer.Exit(1)


@app.command("init")
def init_cmd(
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """Vault klasörlerini ve panoları oluştur."""
    ayar = _ayar(config)
    prepare_vault(ayar.vault)
    typer.echo(f"Vault hazır: {ayar.vault}")


@app.command("baglam")
def baglam_cmd(
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """num_ctx ayarının modele uygulandığını ölç."""
    ayar = _ayar(config)
    try:
        token = measure_context(ayar)
    except RuntimeError as hata:
        typer.echo(f"Hata: {hata}", err=True)
        raise typer.Exit(1) from hata
    typer.echo(f"prompt_eval_count={token} num_ctx={ayar.num_ctx}")
    if token <= 4096:
        typer.echo("Uyarı: bağlam varsayılan 4096 sınırında kalmış görünüyor.", err=True)
        raise typer.Exit(1)


@app.command("web")
def web_cmd(
    port: int = typer.Option(8765, "--port", help="Dinlenecek port"),
    ac: bool = typer.Option(True, "--ac/--acma", help="Tarayıcıyı aç"),
    config: Path | None = typer.Option(None, "--config", help="config.toml yolu"),
) -> None:
    """Yerel web arayüzünü başlat."""
    import threading
    import webbrowser

    import uvicorn

    from videotoinfo.web.server import uygulama

    adres = f"http://127.0.0.1:{port}"
    typer.echo(f"Arayüz: {adres}")
    if ac:
        threading.Timer(1.0, lambda: webbrowser.open(adres)).start()
    uvicorn.run(uygulama(config), host="127.0.0.1", port=port, log_level="warning")
