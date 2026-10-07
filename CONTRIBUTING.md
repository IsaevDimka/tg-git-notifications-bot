# Contributing

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Quick start

```bash
make deps      # uv sync
make check     # ruff + pytest — must be green before a PR
make run       # local bot with data in ./data (needs TELEGRAM_TOKEN in .env)
```

Python 3.12+, [uv](https://docs.astral.sh/uv/). Tests never touch the network: providers are replaced by `tests/fakes.py`.

## Ground rules

- **Small and boring.** One container, SQLite, polling only. Only `TELEGRAM_TOKEN` is required — new features must work without extra config.
- **No code file over 1000 lines** (aim for < 400). Split by feature instead.
- **All user-facing text goes through `app.i18n.t()`.** Add every key to both `app/i18n/en.yml` and `app/i18n/ru.yml` (a test checks they match).
- **Schema changes** are new entries appended to `MIGRATIONS` in `app/storage/schema.py` — never edit an existing one.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) in English (`feat:`, `fix:`, `docs:`…). One topic per PR; add a line to `CHANGELOG.md` under `[Unreleased]`.
- **Privacy:** never log or print tokens, MR titles of other people in shared views, or message contents.

## Layout

| Path | What lives there |
|---|---|
| `app/providers/` | Git hosting clients (GitLab, GitHub) behind one `Provider` protocol |
| `app/core/` | Poller, diffing, "whose move is it", rendering, quiet hours — no Telegram, no HTTP |
| `app/bot/` | Telegram commands, buttons, settings, daily summaries |
| `app/storage/` | SQLite store and migrations |
| `app/i18n/` | `en.yml` / `ru.yml` |

## Adding a git host (Gitea, Forgejo, Bitbucket…)

The core never imports a concrete provider, so a new host is mostly one file:

1. **`app/providers/<name>.py`** — a class implementing the `Provider` protocol from `app/providers/base.py`: `whoami`, `list_items`, `details`, `fetch_item`, `get_by_ref`, `mentions`, and the write actions `reply`, `resolve`, `approve`, `comment`. Raise `AuthError` on HTTP 401 and `ProviderError` on anything else. Use `app/providers/gitlab.py` as the reference.
2. **`app/providers/__init__.py`** — add a branch to `make_provider()`.
3. **`app/providers/base.py`** — the token scope needed for write actions in `WRITE_SCOPES`.
4. **`app/bot/onboarding.py`** — the "create a token" link in `token_url()` and the picker callback; the button itself is in `app/bot/keyboards.py` (`connect_keyboard`); add the button texts to both i18n files.
5. **`tests/test_<name>.py`** — mock the HTTP API with `respx` like `tests/test_gitlab.py`, covering at least listing, details (threads, approvals) and a 401.

Open an issue first if the host lacks something the core relies on (threads, approvals, review requests) — we'll agree on how to degrade.

## Reporting bugs

Use the issue templates. Include the bot version (image tag or `git describe`), the host (GitLab self-managed / gitlab.com / GitHub) and logs **with tokens removed**. Security issues — please don't open a public issue; write to the maintainer on Telegram instead.
