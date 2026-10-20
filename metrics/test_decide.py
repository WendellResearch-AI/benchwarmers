"""python metrics/test_decide.py — error parsing and the question-type guard, no network."""
import io
import sys
import urllib.error

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import decide  # noqa: E402


def http_error(body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", 400, "bad", {}, io.BytesIO(body))


# Every shape providers send must come back as text, never raise.
assert "decisions model" in decide.error_text(http_error(b'{"error": {"message": "is a decisions model"}}'))
assert "decisions model" in decide.error_text(http_error(b'{"error": "is a decisions model"}'))
assert "passthrough" in decide.error_text(http_error(b'{"detail": "Direct API passthrough is disabled"}'))
assert decide.error_text(http_error(b'{"error": null, "message": "down"}')).startswith("400")
assert decide.error_text(http_error(b"not json")) == "400"

# ask() refuses a type the entry does not list, before any call is made.
decide.load = lambda: {"respan": {"served": "r", "via": "nowhere", "question_types": ["noul"]}}
try:
    decide.ask("respan", "s", {"q": {"type": "choice", "instructions": "?", "criteria": {"a": None}}})
except ValueError as exc:
    assert "does not take choice" in str(exc)
else:
    raise AssertionError("choice should have been refused")
print("ok")
