"""The bench's own model client: one chat completion over urllib, no `llm` subprocess.

    python metrics/client.py <alias> "prompt"     # one call; prints what ask_once would return

RELEASE-PLAN.md Phase 2a. A drop-in for `llm -m <alias>`, used when
BENCH_CLIENT=builtin (the default is still `llm`): suite/run.py's ask_once,
and so the perf probe, the suite, retries and auto-retire's confirmation call,
and the gateway's seats. It reads the same registration (the yaml entry's
model name, base, key name and headers), the same keys.json and the same
per-model options as `llm`, and it ends the "database is locked" crash that
parallel seats hit in llm's log.

THE ERROR TEXT IS A CONTRACT
----------------------------
Failures are classified by reading their text (suite/run.py `outcome` and
`failure_class`, metrics/learn.py, provider notices), so the text keeps the
markers llm's produced:

- an HTTP error reads as the openai SDK's, which llm prints:
  `Error: Error code: <status> - <body>`, the body parsed from JSON and shown
  as Python shows a dict. For a body that is not JSON (an HTML 502 page) the
  SDK prints only the text; the status is kept here as well, since 502 and
  503 are markers. A Retry-After header is appended for learn.py.
- a failure on this side (connection refused, unknown alias, bad base URL)
  reads `Error: client failed: ...`, the counterpart of ask_once's
  `Error: llm exited N: ...`; both are in suite/run.py ERRORS. A bare
  "Error: Connection error." would have scored as an answer in the perf tier,
  which accepts one-word replies.
- no answer before the deadline is `<<TIMED OUT after Ns>>`;
- an empty answer is the empty string (a reasoning model out of budget), but
  a 200 with no choice at all is `client failed`: a broken endpoint, which
  "empty" would have kept out of auto-retirement.

Redirects are not followed (see _NoRedirect): urllib would hand the key to
whatever host a redirect names.

RETRIES MATCH THE SDK UNDER llm: two more tries on a connection error, 408,
409, 429 or 5xx, waiting the Retry-After when it is 60 s or less, else about
0.5 then 1 s. Without them this client would surface 429s llm absorbed, and
the comparison would blame the client. A wait that would pass the deadline is
not taken: the error is returned, which says more than a timeout.

Not yet (Phase 2b): streaming, usage ledger, quota records, enforcing learned
limits. Non-streaming means a long reasoning answer behind a proxy with a
short idle timeout can fail here where llm's stream survived; the perf tier's
one-word prompts are where this is tried first.

    python scripts/test_client.py    # both backends on a fake server, same results
"""

from __future__ import annotations

import email.utils
import http.client
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
sys.path.insert(0, str(ROOT / "metrics"))
import endpoints  # noqa: E402

OPTIONS = DATA / "config" / "model-options.json"
MAX_RETRIES = 2
RETRY_STATUS = {408, 409, 429}
MAX_REPLY = 32_000_000                             # bytes


def backend() -> str:
    """`llm` (default) or `builtin`, from BENCH_CLIENT."""
    return (os.environ.get("BENCH_CLIENT") or "llm").strip().lower()


def _options(alias: str) -> dict:
    """Per-model request defaults (the inkling pair's max_tokens), as llm applies them."""
    try:
        data = json.loads(OPTIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    opts = data.get(alias)
    return dict(opts) if isinstance(opts, dict) else {}


def _retry_after(headers) -> float | None:
    """Seconds the server asked us to wait, when it is a usable 0-60 s (the SDK's rule)."""
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name) if headers else None
        if not raw:
            continue
        try:
            wait = float(raw) * scale
        except ValueError:
            try:                                       # an HTTP date
                wait = email.utils.parsedate_to_datetime(raw).timestamp() - time.time()
            except (TypeError, ValueError):
                wait = -1
        if 0 < wait <= 60:
            return wait
    return None


def _backoff(attempt: int) -> float:
    return min(0.5 * 2 ** attempt, 8.0) * (1 - 0.25 * random.random())


def _text(content) -> str:
    """Message content as text: a string, or a list of parts some providers send."""
    if isinstance(content, list):
        return "".join(p.get("text") or "" for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are refused, so the 3xx surfaces as an HTTPError. urllib would
    resend the Authorization header to whatever host a redirect names (httpx,
    under llm, strips it), and turns the POST into a body-less GET anyway."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _read(response, deadline: float) -> bytes | None:
    """The whole body, or None if the deadline passes while it arrives. Read in
    pieces because the socket timeout is per wait: a server trickling bytes
    would otherwise hold a seat past its deadline. Raises ValueError past
    MAX_REPLY, since the gateway is a long-lived process."""
    chunks, size = [], 0
    while chunk := response.read1(65536):
        size += len(chunk)
        if size > MAX_REPLY:
            raise ValueError(f"the reply is over {MAX_REPLY // 1_000_000} MB")
        chunks.append(chunk)
        if time.monotonic() > deadline:
            return None
    return b"".join(chunks)


def chat(alias: str, prompt: str, system: str = "", timeout: float = 420) -> dict:
    """One chat completion. Returns {text, reasoning, error, timed_out, status,
    model, usage, seconds}; `error` is "" on success, else the contract text."""
    started = time.monotonic()
    deadline = started + timeout
    res = {"text": "", "reasoning": "", "error": "", "timed_out": False, "status": 0,
           "model": "", "usage": {}}

    def done(**kw) -> dict:
        res.update(kw, seconds=round(time.monotonic() - started, 1))
        return res

    reg = endpoints.registrations().get(alias)
    if reg is None:
        return done(error=f"Error: client failed: no model is registered as {alias!r}")
    base = (reg.get("api_base") or "").rstrip("/")
    # urllib would also open file:// and ftp:// URLs given as a base.
    if not base.startswith(("http://", "https://")):
        return done(error=f"Error: client failed: endpoint base must be http(s), not {base!r}")
    messages = ([{"role": "system", "content": system}] if system else []) \
        + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": reg.get("model_name") or alias, "messages": messages,
                       **_options(alias)}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "model-bench",
               **reg.get("headers", {})}
    if name := reg.get("api_key_name", ""):
        # As llm does: a named key that is not set stops here, rather than
        # sending an unauthenticated call whose 401 would read as entitlement.
        if not (key := endpoints.keys().get(name, "")):
            return done(error=f"Error: client failed: no key named {name!r} is set"
                              f" (llm keys set {name})")
        headers["Authorization"] = f"Bearer {key}"

    for attempt in range(MAX_RETRIES + 1):
        left = deadline - time.monotonic()
        if left <= 0:
            return done(timed_out=True)
        request = urllib.request.Request(base + "/chat/completions", body, headers)
        status = 0
        try:
            with _OPENER.open(request, timeout=left) as r:
                status = r.status
                raw = _read(r, deadline)
            if raw is None:
                return done(timed_out=True)
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace").strip()
            except (OSError, http.client.HTTPException):   # the body broke off
                text = ""
            try:
                shown = json.loads(text)
            except ValueError:
                shown = text
            wait = _retry_after(e.headers)
            err = f"Error: Error code: {e.code}" + (f" - {shown}" if text else "") \
                + (f" Retry-After: {e.headers.get('retry-after')}" if e.headers.get("retry-after") else "") \
                + (f" (redirect to {e.headers.get('location')} not followed)" if 300 <= e.code < 400 else "")
            res["status"] = e.code
            if not (e.code in RETRY_STATUS or e.code >= 500) or attempt == MAX_RETRIES:
                return done(error=err)
            wait = wait or _backoff(attempt)
            if time.monotonic() + wait >= deadline:
                return done(error=err)
            time.sleep(wait)
            continue
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", e)
            # The deadline itself, or the OS giving up on a connection early
            # (a dropped SYN), which is a connection error and is retried.
            if isinstance(reason, TimeoutError) and time.monotonic() >= deadline - 0.5:
                return done(timed_out=True)
            err = f"Error: client failed: Connection error. ({reason})"
            wait = _backoff(attempt)
            if attempt == MAX_RETRIES or time.monotonic() + wait >= deadline:
                return done(error=err)
            time.sleep(wait)
            continue
        except ValueError as e:                        # over MAX_REPLY, or a malformed URL
            return done(status=status, error=f"Error: client failed: {e}")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return done(status=status, error="Error: client failed: the reply was not JSON: "
                                             + raw[:1000].decode("utf-8", "replace"))
        if not isinstance(data, dict):
            return done(status=status, error=f"Error: client failed: unexpected reply: {str(data)[:1000]}")
        # Some relays answer 200 with an error object instead of a status.
        if data.get("error") and not data.get("choices"):
            code = (data["error"].get("code") if isinstance(data["error"], dict) else None) or status
            return done(status=status, error=f"Error: Error code: {code} - {data}")
        choices = data.get("choices")
        # A 200 with no choice is a broken endpoint, not an empty answer: "empty"
        # is kept out of auto-retirement as a budget problem.
        if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
            return done(status=status, error=f"Error: client failed: the reply had no choices: {str(data)[:1000]}")
        message = choices[0].get("message") or {}
        return done(status=status, text=_text(message.get("content")),
                    reasoning=_text(message.get("reasoning_content") or message.get("reasoning")),
                    model=data.get("model") or "", usage=data.get("usage") or {})
    return done(timed_out=True)                        # unreachable: every path above returns


def output(res: dict, timeout: float) -> str:
    """The result as ask_once's text: the answer as llm prints it (with its
    closing newline), the error, or the timeout sentinel."""
    if res["timed_out"]:
        return f"<<TIMED OUT after {timeout:g}s>>"
    return res["error"] or (res["text"] + "\n" if res["text"] else "")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        print(__doc__.split("\n\n")[0])
        return 2
    res = chat(sys.argv[1], sys.argv[2], timeout=120)
    print(output(res, 120))
    return 1 if res["error"] or res["timed_out"] else 0


if __name__ == "__main__":
    sys.exit(main())
