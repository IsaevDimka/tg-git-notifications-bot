# CLAUDE.md — tg-git-notifications-bot

Self-hosted multi-user Telegram bot: GitLab/GitHub code-review notifications ("whose move is it").
Spec: `docs/specs/2026-09-24-design.md`. Plan: `docs/plans/2026-09-24-v0.1-mvp.md`.

## Rules
- One container + SQLite, polling only. Only `TELEGRAM_TOKEN` is required.
- No code file over 1000 lines (aim < 400).
- All user-facing text via `app.i18n.t()`; keep `ru.yml`/`en.yml` keys identical.
- Conventional Commits in English. Branch + PR, never push without the owner's go-ahead.
- `workflow.md` and `.env` hold real secrets — never commit or print them.

## Commands
- `uv sync` · `uv run pytest -q` · `uv run ruff check .`
- Local run: `set -a; source .env; set +a; uv run python -m app`

## Deploy
- Image: `ghcr.io/isaevdimka/tg-git-notifications-bot` (built by `.github/workflows/release.yml` on `v*` tags).
- Target server (TODO D1): `do-ams3-claude-01` / 188.166.91.129, next to tg-claude-bot.
- Backlog: `TODO.md`.
