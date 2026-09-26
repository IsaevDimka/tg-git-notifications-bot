# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: SemVer.

## [Unreleased]

## [0.3.0] - 2026-09-26

### Added
- 🧵 Count of your threads the other side answered (waiting for your resolve) in `/mr`, `/inbox` and summaries.
- `/load` — your MRs waiting on each reviewer, and how many reviews each colleague in the bot is holding (numbers only; hide yourself in `/settings`).
- `/stats [days]` — review requests, re-reviews, replies, mentions, your merges/approvals, and what's waiting now.
- Opt-in Friday report (17:00 local) with the week's stats.
- `make deploy` / `deploy-env` / `deploy-status` / `deploy-logs` / `deploy-restart`: deploy over SSH with `deploy/docker-compose.prod.yml` (own compose project, 256 MB limit, rotated logs).
- `make install` (first run: asks for the bot token, writes `.env` 0600, pulls and starts) and `make status`.
- Requirements and everyday `make` commands in both READMEs and on the website.

### Fixed
- Schema migrations run in a transaction: a crash mid-way no longer breaks every restart with "duplicate column".
- "Token stopped working" is no longer counted as an unread mention, and tokens that died before this notice existed are reported too.
- Editing a sent command (e.g. `/mute`, `/invite`) no longer runs it again.
- `/mute` and `/watch` buttons use a stable key, so an older message can't toggle the wrong row.
- Morning/evening summaries go out at the chosen time even inside quiet hours (quiet weekends still apply).
- Approving an MR you already approved on GitLab no longer says the token stopped working.
- Network errors while polling are logged as one line; a rejected Telegram token exits with a clear message without printing it.
- GitHub: search is paginated (newest first, up to 300) and the newest review threads are fetched on very busy PRs.
## [0.2.0] - 2026-09-24

### Added
- "Ping" reaches colleagues who use this bot as a Telegram notice (respecting their quiet hours, once a day); others still get an MR comment. `/invite` makes a one-time 7-day link that skips admin approval.
- Opt-in evening summary (17–20, default 18:00): merge requests where it's still your move.
- `/status` — per account: last successful check, last error, counts, API requests left; queue size and quiet-hours state. `/test` — samples of every notification type.
- `/watch <MR link>` — follow someone else's merge request (approvals, merged, closed; no comment noise); `/watch` lists and unfollows.
- MRs labelled `blocker` / `hotfix` (configurable: `URGENT_LABELS`) notify even during quiet hours.
- Noise filters: bot accounts (renovate, dependabot, `*[bot]`, GitLab project/group bots) and drafts you review are quiet by default (toggles in `/settings`); `/mute` silences whole projects in notifications, lists and the daily summary.
- 3+ comments from one person on one MR in one go (e.g. a submitted review) arrive as one message with the first quotes and a single "✓ Read".
- "⏰ Waiting for your review for N": a daily reminder once an MR has been your move for 2+ days — only when the morning summary is off (the summary already lists them); max 5 per check, stops after 30 days (the same 30-day stop now applies to "waiting for reviewers").
- Review requests on draft MRs are announced when the draft becomes ready.
- "🔁 New commits after your review": when the author pushes after you requested changes or commented, it's your move again — no re-request click needed. (A rebase triggers it too.)
- One-time "token stopped working" notice when an account's token is rejected.

### Fixed
- The poll loop no longer dies if the heartbeat file can't be written.
- `/mr`, `/inbox`, `/my`, `/settings`, `/accounts`, `/lang`, `/help` answer only in private chats (they show private MR titles).
- Editing a sent message no longer crashes the text handler or eats a pending prompt.
## [0.1.0] - 2026-09-24

### Added
- `/start` onboarding: GitLab.com, self-hosted GitLab or GitHub; token message deleted at once, token stored
  encrypted; time zone picker.
- Access control: first user is admin, others need approval; `ALLOWED_USERS` bypass.
- Notifications: review requested, new comment, reply to your comment, your thread resolved/reopened, mention,
  approval, changes requested, merged, closed, merge conflict, your MR waiting 24 h+ for reviewers.
- Buttons: reply, resolve, approve (with confirmation), ping reviewers (with confirmation), snooze, mark read.
- `/inbox` (your move only), `/mr` (to review / mine), `/accounts`, `/settings`, `/help`.
- Quiet hours and weekends with a morning digest; per-type mutes; per-user check interval.
- `/my` — only your own merge requests; `/lang` — switch RU/EN (also switches the chat's command menu).
- Daily summary at a chosen local time (09–12, default 10:00): what waits for your review, your MRs where it's your move, your MRs waiting for reviewers; can be turned off in `/settings`.
- `Makefile`: `up`, `down`, `logs`, `update`, `backup` (consistent SQLite snapshot + key), `check`, `run`.
- Docker image (amd64/arm64), docker compose, Fly/Render/Railway templates, CI.
