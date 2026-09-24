# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: SemVer.

## [Unreleased]

### Added (v0.2 quick wins)
- One-time "token stopped working" notice when an account's token is rejected.

### Fixed (v0.2 quick wins)
- The poll loop no longer dies if the heartbeat file can't be written.

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
