# gaia

[![gaia-agent.com](https://img.shields.io/badge/gaia--agent.com-0F172A?style=flat&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAAABGdBTUEAALGPC/xhBQAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoABQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAEgAAAABAAAASAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAADqADAAQAAAABAAAADgAAAACOfw3NAAAACXBIWXMAAAsTAAALEwEAmpwYAAACmmlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPHg6eG1wbWV0YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNi4wLjAiPgogICA8cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRheC1ucyMiPgogICAgICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgICAgICAgICB4bWxuczp0aWZmPSJodHRwOi8vbnMuYWRvYmUuY29tL3RpZmYvMS4wLyIKICAgICAgICAgICAgeG1sbnM6ZXhpZj0iaHR0cDovL25zLmFkb2JlLmNvbS9leGlmLzEuMC8iPgogICAgICAgICA8dGlmZjpYUmVzb2x1dGlvbj43MjwvdGlmZjpYUmVzb2x1dGlvbj4KICAgICAgICAgPHRpZmY6WVJlc29sdXRpb24+NzI8L3RpZmY6WVJlc29sdXRpb24+CiAgICAgICAgIDx0aWZmOlJlc29sdXRpb25Vbml0PjI8L3RpZmY6UmVzb2x1dGlvblVuaXQ+CiAgICAgICAgIDxleGlmOlBpeGVsWURpbWVuc2lvbj4xMjQ8L2V4aWY6UGl4ZWxZRGltZW5zaW9uPgogICAgICAgICA8ZXhpZjpQaXhlbFhEaW1lbnNpb24+MTI0PC9leGlmOlBpeGVsWERpbWVuc2lvbj4KICAgICAgICAgPGV4aWY6Q29sb3JTcGFjZT4xPC9leGlmOkNvbG9yU3BhY2U+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICA8L3JkZjpSREY+CjwveDp4bXBtZXRhPgqvVIrFAAAB/0lEQVQoFaWSu2tUQRTGvzMz9+7eXbK7bNZkTZQQEBRiaTob0cLGRgVRsEwrdiIETKFNYkjjvxBLESwsLQSxsJDVYAoFCxsNkrD37n3N4zh3zUMsdYbDnGG+3znfDAP846C/ufaTNxebfdzsd/PTJya0m2+JwUyoNu/NX3j7p/YIXHmlWmiuUc/diWat6HczzLcNTrUlZiWXXUWrt49fekBEriogDqp0f7SWhQvv2jIXeRpznKW8myX8M9lzQ52ErsbLmzsv7x/oxx1nrgzO2LZ+V3azZjFdME9naEwWmOoUmGtZnmsQTjYC6tVUKhUtLvWuflJVBZmV1ylQTR04h8iSbTiM6gY7oQErDePdacqZ681mVLprHnk4BoOUFoy0EIGDSBgcOxSRhQ01rNTkJxfEcDWLNrmFqtkYVIklCAEZ+EtHzDQEOGLSoUMsPSxKcsKy8C4mha24fTClAaS8IUNve+ThOvsCgKs7mMAi9XaFj5o28Ma3KnD8qtLQMxR6JDMhREaQKYFSf5pVYWFyg7Qsxe4wzr8Xw+eH4Iev57adM6vC1UAFhA9AM2C8LWMYWpPxFuMsW/+y+PjjIVgln7e/PdIcr1li44QSLODbOoLyGUqDOF5P1fuVSluNo5/ze4+Jyy/O553RLTMVn+Vje0BnuIV68hRLG6/3Jf+3/AKwAunY2GH4ZwAAAABJRU5ErkJggg==&logoColor=white)](https://gaia-agent.com)
[![Docs](https://img.shields.io/badge/docs-0F172A?style=flat&logo=astro&logoColor=white)](https://docs.gaia-agent.com)
[![CI](https://img.shields.io/github/actions/workflow/status/Sho0pi/gaia/ci.yml?branch=master&style=flat&logo=githubactions&logoColor=white&label=CI&labelColor=0F172A)](https://github.com/Sho0pi/gaia/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Sho0pi/gaia?style=flat&logo=opensourceinitiative&logoColor=white&label=license&labelColor=0F172A&color=0F172A)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-0F172A?style=flat&logo=python&logoColor=white)](https://www.python.org/)

> I hate python — here is Gaia, the best agent you will ever use.

**Gaia** is an AI agent that forges task-specific subagents (**souls**) on demand, **stores them
for reuse** (never recreating), and grows to you over time. Inspired by openclaw, hermes-agent,
and picoclaw.

## ⚠️ Disclaimer

Gaia is autonomous software that runs **LLM-driven actions on your machine** — it can execute shell
commands, control a browser, read and write files, and send messages on your behalf. A model can be
wrong or be manipulated, so Gaia may take **unintended or destructive actions**. **Use it entirely at
your own risk.**

Gaia is provided **"AS IS", without warranty of any kind**. To the maximum extent permitted by law,
the authors and contributors are **not liable** for any damage, data loss, financial cost, or other
harm arising from its use — this is the legally binding [MIT License](LICENSE). **You alone are
responsible** for where you run it, what credentials and access you give it, and everything it does.
Read the [security model](SECURITY.md) and the permission/sandbox docs, and keep it on least
privilege, before pointing it at anything you care about.

## 📚 Documentation

Full docs — install, concepts, guides, and the CLI/config reference — live at
**[docs.gaia-agent.com](https://docs.gaia-agent.com)**.

## Quickstart

```bash
git clone https://github.com/Sho0pi/gaia.git
cd gaia
uv sync --all-groups
echo "GEMINI_API_KEY=your-key" >> ~/.gaia/.env
uv run gaia                     # inline terminal chat
```

See [Getting started](https://docs.gaia-agent.com/getting-started/) for connecting Telegram /
WhatsApp.

## Built on

Gaia stands on these open-source projects — each remains under **its own license**, held by its
respective authors:

- [google-adk](https://github.com/google/adk-python) — agent runtime + LLM skeleton
- [a2a-sdk](https://a2a-protocol.org) — agent-to-agent protocol
- [mem0ai](https://github.com/mem0ai/mem0) — long-term memory
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) ·
  [pywa](https://github.com/david-lev/pywa) · [neonize](https://github.com/krypton-byte/neonize) —
  connectors
- [Typer](https://typer.tiangolo.com) · [Textual](https://textual.textualize.io) · the docs site on
  [Astro Starlight](https://starlight.astro.build)
- Managed with [uv](https://docs.astral.sh/uv/).

Gaia's own code is [MIT-licensed](LICENSE); the dependencies above keep their original licenses.

## Develop

```bash
uv sync --all-groups            # install
uv run ruff check --fix .       # lint
uv run mypy src                 # types
uv run pytest                   # tests
```

The docs site lives in [`docs/`](docs/) (Astro Starlight); its Reference pages are generated by
[`scripts/gen_reference.py`](scripts/gen_reference.py). See [CLAUDE.md](CLAUDE.md) for the full
development workflow.
