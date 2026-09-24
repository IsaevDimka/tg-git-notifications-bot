"""Tiny YAML-backed i18n. Missing key → English → the key itself."""

from functools import cache
from pathlib import Path

import yaml

LANGS = ("ru", "en")
DEFAULT_LANG = "en"
_DIR = Path(__file__).parent


@cache
def _catalog(lang: str) -> dict[str, str]:
    with open(_DIR / f"{lang}.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pick_lang(language_code: str | None) -> str:
    return "ru" if (language_code or "").lower().startswith("ru") else "en"


def t(lang: str, key: str, **kw) -> str:
    text = _catalog(lang if lang in LANGS else DEFAULT_LANG).get(key) or _catalog(DEFAULT_LANG).get(key) or key
    return text.format(**kw) if kw else text
