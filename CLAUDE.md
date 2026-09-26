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
- `make help` — all targets. Dev: `make check` (ruff + pytest), `make run` (local, ./data).
- Server: `make install` (first run, asks for the token) / `status` / `logs` / `restart` / `update` / `backup` / `down` (docker compose; backup via `python -m app.backup`).

## Deploy
- Image: `ghcr.io/isaevdimka/tg-git-notifications-bot` (built by `.github/workflows/release.yml` on `v*` tags).
- Prod: `do-ams3-claude-01` / 188.166.91.129 (SSH `devops@tg-bot`), `/opt/tg-git-notifications-bot`, compose project `tg-git-notifications-bot`, next to tg-claude-bot (don't touch it).
- `make deploy [IMAGE_TAG=x.y.z]` / `deploy-env` / `deploy-status` / `deploy-logs` / `deploy-restart`; compose source: `deploy/docker-compose.prod.yml`.
- GitLab mirror: `git@gitlab.isaevdimka.com:iac/tg-git-notifications-bot.git` (remote `gitlab`, project ID 57). `.gitlab-ci.yml`: lint + test on push/MR; manual `deploy` on `v*` tags (shell runner `deploy` on the server, waits for the ghcr image). After a tag: push it to both remotes, then play the deploy job via GitLab API.
- Backlog: `TODO.md`.
