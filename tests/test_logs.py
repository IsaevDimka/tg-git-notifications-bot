import logging
import sys

from app.logs import RedactingFormatter, redact

FAKE_BOT = "bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def test_redacts_known_token_shapes():
    text = (
        f"GET https://api.telegram.org/{FAKE_BOT}/getMe "
        "glpat-abcdefghijklmnop ghp_abcdefghijklmnopqrstuvwxyz0123 github_pat_abcdefghijklmnopqrstuvwxyz"
    )
    out = redact(text)
    assert "ABCDEFGH" not in out
    assert "glpat-abc" not in out
    assert "ghp_abc" not in out
    assert "github_pat_abc" not in out
    assert out.count("[REDACTED]") == 4


def test_plain_text_untouched():
    assert redact("MR !42 approved by alice") == "MR !42 approved by alice"


def test_formatter_redacts_exception_text():
    fmt = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"bad url https://api.telegram.org/{FAKE_BOT}/x")
    except RuntimeError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    assert "ABCDEFGH" not in fmt.format(record)
