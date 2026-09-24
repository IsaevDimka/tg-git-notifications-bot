# tg-git-notifications-bot

A self-hosted Telegram bot that makes sure you never miss a code review on **GitLab** or **GitHub**.
It messages you when it's *your move* — so nobody has to ping you by hand.

- 🔔 You were asked to review a merge request
- 🔁 The author pushed new commits after your review · ↩️ someone replied to your comment · ✔️ your thread was resolved or reopened
- 💬 New comments on merge requests you review or own · 📣 @mentions
- ✅ Approvals, 🔄 change requests, ⚠️ conflicts, 🎉 merged / closed on your own merge requests
- ⏳ Your merge request has waited 24 h+ for reviewers → one tap posts a friendly reminder
- ⏰ An MR has waited for *your* review 2+ days → a daily nudge (stops after 30 days)
- 🔑 Your token stopped working → you're told once, instead of the bot going silent
- Reply, resolve, approve and snooze **right from Telegram**
- `/inbox` shows only the merge requests where it's your move; `/mr` shows everything
- Quiet hours and weekends: events wait and arrive as one morning digest
- Daily summary at your chosen time (can be turned off)
- Quiet by default for bots (renovate, dependabot…) and drafts you review; `/mute` a whole project
- Multi-user: one deployment serves your whole team; everyone connects their own token
- One container, SQLite, no public URL needed (long polling). Russian and English UI.

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Run it anywhere Docker runs:

   ```bash
   docker run -d --name git-notify --restart unless-stopped \
     -e TELEGRAM_TOKEN=123456:ABC... \
     -v git-notify-data:/data \
     ghcr.io/isaevdimka/tg-git-notifications-bot:latest
   ```

   or `cp .env.example .env`, fill in `TELEGRAM_TOKEN`, `docker compose up -d`.
3. Message your bot `/start`. **The first person to do so becomes the admin**; others need the admin's
   approval (or list their IDs in `ALLOWED_USERS`).
4. Tap GitLab.com / Self-hosted GitLab / GitHub and send a token:
   - GitLab: personal access token with scope **`api`** (`read_api` works for notifications only)
   - GitHub: **classic** token with scope **`repo`** (mentions need the notifications API, which
     fine-grained tokens can't use)

   The bot deletes your message with the token immediately and stores it encrypted.

> Keep `/data` on a volume. At start the container hands `/data` to its unprivileged user (uid 10001) and then
> drops root, so root-owned volumes on Fly, Railway or Render and plain bind mounts work as is.

### With the Makefile (from a clone of this repo)

```bash
git clone https://github.com/IsaevDimka/tg-git-notifications-bot && cd tg-git-notifications-bot
cp .env.example .env      # set TELEGRAM_TOKEN
make up                   # start (make logs · make down · make restart)
make update               # pull the newest image and restart
make backup               # bot.db + secret.key → ./backups (safe while running)
```

`make help` lists everything, including `make test` / `make run` for development.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_TOKEN` | — (required) | Your bot's token from @BotFather |
| `DATA_DIR` | `/data` | Where `bot.db` and `secret.key` live |
| `ALLOWED_USERS` | empty | Comma-separated Telegram user IDs let in without approval |
| `POLL_INTERVAL` | `180` | Default seconds between checks, per user |
| `MIN_POLL_INTERVAL` | `60` | Lower bound users can choose in `/settings` |
| `URGENT_LABELS` | `blocker,hotfix` | MRs with these labels notify even during quiet hours |
| `LOG_LEVEL` | `INFO` | Python log level |

Back up `DATA_DIR` as a whole (`make backup` does it): without `secret.key` the stored tokens can't be decrypted.

## Commands

| Command | What it does |
|---|---|
| `/start` | Connect a GitLab or GitHub account |
| `/inbox` | Merge requests where it's your move, with unread counts |
| `/mr` | Everything you review and everything you authored |
| `/my` | Only your own merge requests |
| `/watch <link>` | Follow someone else's MR: approvals, merged, closed |
| `/mute` | Mute a noisy project (list with buttons, or `/mute group/project`) |
| `/lang` | Switch language (RU / EN) |
| `/accounts` | Connected accounts, their status; disconnect |
| `/settings` | Notification types, daily summary on/off and time, check interval, quiet hours, language, time zone |
| `/help` | Command list |

## How “your move” is decided

- **You review:** your move until you comment or approve; after you comment it's the author's; when the author
  answers in your thread it's yours again; drafts are nobody's.
- **You authored:** your move on a conflict, on an unresolved thread where a reviewer spoke last, and when all
  required approvals are in (time to merge); otherwise it's the reviewers'.

## Deploy templates

`fly.toml`, `render.yaml` and `railway.json` are included. Each needs `TELEGRAM_TOKEN` and a persistent volume
mounted at `/data`.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
cp .env.example .env   # set TELEGRAM_TOKEN and DATA_DIR=./data
set -a; source .env; set +a; uv run python -m app
```

Design: `docs/specs/2026-09-24-design.md`. License: MIT.
