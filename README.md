# VideoToInfo

Turn a video or article link into a summarized, fact-checked Obsidian note. Transcription, summarization, and fact-checking run on local models.

The command-line interface, prompts, and code identifiers are in Turkish.

## What it does

- Downloads a video with yt-dlp and transcribes it with mlx-whisper, or extracts an article with trafilatura.
- Summarizes with Ollama in the source language for English, and in Turkish otherwise. `ozet_dili` can pin this to `tr` or `en`.
- Extracts claims and checks them against web search (ddgs by default, Brave optional).
- Writes an Obsidian note, and can read a waiting list from the vault's "Gelen kutusu" note.
- Speaks the short summary with the macOS `say` command, and serves a local web UI with chat.

## Requirements

- macOS on Apple Silicon
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) with the models named in `config.toml` (by default `qwen3.8:27b-mlx` for summaries and `gemma4:26b-mlx` for claims, verdicts, and chat)
- ffmpeg, if yt-dlp needs it to extract audio

## Install

```sh
uv sync --no-editable --group dev
./vti init
```

`./vti init` creates the vault folders and dashboards. The default vault is `~/Documents/VideoToInfo-Vault`.

## Usage

```sh
./vti add "https://www.youtube.com/watch?v=..." --dogrula --ses
./vti oku "https://example.com/article"
./vti kuyruk
./vti web
scripts/web-servis.sh kur
```

- `add` downloads a video. `--dogrula` fact-checks the claims. `--ses` speaks the short summary.
- `oku` summarizes an article. Fact-checking is on unless you pass `--dogrulamasiz`.
- `kuyruk` processes links pasted into the vault's "Gelen kutusu" note.
- `web` serves the UI at `http://127.0.0.1:8765`.
- `scripts/web-servis.sh kur` registers a LaunchAgent that starts the UI at login. `kaldir`, `yenile`, and `durum` stop, restart, and inspect it.

## Configuration

`config.toml` holds the defaults. To keep your own copy (and stop the web UI from editing the file in the repo), point `VIDEOTOINFO_CONFIG` at it. Every command also accepts `--config`.

| Variable | Effect |
|---|---|
| `VIDEOTOINFO_CONFIG` | Path to the config file to read and, from the web UI, write |
| `BRAVE_API_KEY` | Required only when `arama.saglayici` is `brave` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` | Optional tracing. Ignored when the keys are unset |

`yazi.cerez_tarayici` is empty by default, so a fresh clone never reads browser cookies. Set it to `chrome`, `safari`, `firefox`, `brave`, or `edge` only if you want a logged-in copy of a members-only article. Cookies are read for that article's domain. The article finder does not use paywall mirrors.

## Model comparison

On 2026-11-12, `scripts/model_eval.py` compared the two local models on the same articles, one video, and the same claims. Gemma generates about five times faster and leaves more claims as unverified. Qwen writes more detailed Turkish summaries, so summaries stay on Qwen and claims, verdicts, and chat stay on Gemma. The two models agreed on 9 of 14 verdicts.

| | qwen3.8:27b-mlx | gemma4:26b-mlx |
|---|---:|---:|
| Generation (tokens/s) | 15.8 | 86.1 |
| Prompt processing (tokens/s) | 105.1 | 1332.1 |

Rerun it with the models you have installed:

```sh
PYTHONPATH=src uv run --no-sync python scripts/model_eval.py qwen3.8:27b-mlx gemma4:26b-mlx
```

The script does not write to the vault. It needs the local models, network access for one Turkish article, and the cached sources under `data/` from a previous run.

## Tests

```sh
uv run pytest
```

Integration tests that need a local model, the network, or audio are skipped by default.
