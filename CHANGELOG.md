# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: SemVer.

## [Unreleased]

### Added
- `/start` onboarding: GitLab.com, self-hosted GitLab or GitHub; token message deleted at once, token stored
  encrypted; time zone picker.
- Access control: first user is admin, others need approval; `ALLOWED_USERS` bypass.
- Notifications: review requested, new comment, reply to your comment, your thread resolved/reopened, mention,
  approval, changes requested, merged, closed, merge conflict, your MR waiting 24 h+ for reviewers.
- Buttons: reply, resolve, approve (with confirmation), ping reviewers (with confirmation), snooze, mark read.
- `/inbox` (your move only), `/mr` (to review / mine), `/accounts`, `/settings`, `/help`.
- Quiet hours and weekends with a morning digest; per-type mutes; per-user check interval.
- Docker image (amd64/arm64), docker compose, Fly/Render/Railway templates, CI.
