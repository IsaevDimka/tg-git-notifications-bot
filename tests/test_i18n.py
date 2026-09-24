import string

from app.i18n import _catalog, pick_lang, t


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_catalogs_have_same_keys():
    assert set(_catalog("ru")) == set(_catalog("en"))


def test_placeholders_match_between_languages():
    ru, en = _catalog("ru"), _catalog("en")
    for key in en:
        assert _fields(ru[key]) == _fields(en[key]), key


def test_pick_lang():
    assert pick_lang("ru-RU") == "ru"
    assert pick_lang("ru") == "ru"
    assert pick_lang("en") == "en"
    assert pick_lang("de") == "en"
    assert pick_lang(None) == "en"


def test_format_and_fallbacks():
    assert "5" in t("ru", "mr.more", count=5)
    assert t("xx", "btn.open") == t("en", "btn.open")
    assert t("en", "no.such.key") == "no.such.key"
