"""The bench's own model client: one chat completion over urllib, no `llm` subprocess.

    python metrics/client.py <alias> "prompt"     # one call; prints what ask_once would return

RELEASE-PLAN.md Phases 2a and 2b. A drop-in for `llm -m <alias>`, and the
default since v0.3 (`llm` stays a choice on Settings or BENCH_CLIENT=llm; see
chosen()), used by suite/run.py's
ask_once, and so the perf probe, the suite, retries and auto-retire's
confirmation call, and by the gateway's seats. It reads the same
registration (the yaml entry's model name, base, key name and headers), the
same keys.json and the same per-model options as `llm`, and it ends the
"database is locked" crash that parallel seats hit in llm's log.

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
  which accepts one-word replies. A call the bench declines to send says so
  in the same form: pacing's `Error: client failed: rate limit (not sent;
  ...)` is a capacity failure (metrics/pacing.py), the free-tier guard's
  `... payment required ...` an entitlement one.
- no answer before the deadline is `<<TIMED OUT after Ns>>`;
- an empty answer is the empty string (a reasoning model out of budget), but
  a 200 with no choice at all is `client failed`: a broken endpoint, which
  "empty" would have kept out of auto-retirement. So is a stream that breaks
  off before it says it is finished: a cut answer graded as the model's work
  is the confusion this repo exists to prevent.

Redirects are not followed (see _NoRedirect): urllib would hand the key to
whatever host a redirect names.

STREAMED BY DEFAULT (2b)
------------------------
Chat completions are asked for as a stream (`stream: true`, with
`stream_options.include_usage` for the token counts), as llm asks: a 5-7
minute reasoning answer survives a proxy's idle timeout only if bytes keep
coming. An upstream that answers with plain JSON anyway is read as before,
and one that refuses the stream options (a 400 or 422 naming "stream") is
asked once more without them. Reasoning text (`reasoning_content`, or
`reasoning`) is kept apart from the answer. `on_delta(kind, text)`, when
given, hears each piece as it arrives (kind "text" or "reasoning") so a
caller can relay it; a callback that raises is dropped, never the call. The
deadline is checked between chunks, and so is STOP, which a server shutting
down sets.

WHAT EVERY CALL LEAVES BEHIND (2b)
----------------------------------
- a ledger line (metrics/ledger.py): tokens in and out, seconds, status,
  attempts, caller;
- quota records from the reply's rate-limit headers and any 429 body
  (metrics/quota.py);
- a silent-swap note when the reply names another model than the one
  registered (learn.note_served_as, and `swap` in the ledger line); the call
  still succeeds.

BEFORE A CALL IS SENT (2b)
--------------------------
- the free-tier guard: a model on an active "went paid" alert (catalog.py
  saw a price) is not called, so a free model turned paid never bills by
  surprise. Allowed per model by config/connections.json `allow_paid`
  (aliases), or for one process by BENCH_ALLOW_PAID=1. A connection paid by
  subscription never raises that alert in the first place.
- pacing (metrics/pacing.py): stored and learned limits, the monthly cap and
  quota records, counted from the ledger with calls in flight.

AN EMPTY ANSWER CUT OFF BY ITS BUDGET IS TRIED ONCE MORE: when the reply is
empty and finished for "length", the same call goes again with twice the
output budget (capped at the model's known max output), and the result says
so (`budget_retry`).

ENDPOINTS CONNECTED BY SIGN-IN (metrics/connect.py) come through here even
when BENCH_CLIENT is `llm` (see backend()): a token that renews is renewed
just before the call, and ChatGPT's plan is reached through the Responses API
(streamed, unstored) rather than chat completions. `llm -m` cannot do either,
so for those models use `python metrics/client.py <alias> "prompt"`.

NATIVE ANTHROPIC AND GEMINI: an endpoint whose `api` is `anthropic` or
`gemini` (the Add endpoint form) is called in that API, through here
whatever BENCH_CLIENT says: metrics/formats.py builds the request (system
text apart, the model's options it knows, images as the API's own parts),
the key goes as x-api-key or x-goog-api-key, and the stream is read into
the same result, usage and finish included. Anthropic's error events read
as the status that carries them (overloaded_error is 529), so the text
contract holds. Tool calls are not sent to them (route mode proxies chat
completions only).

FOR A PROXY (the gateway's route mode): `body`, `on_chunk`, `first_token`
and `retries` (see chat()) pass a request's own fields, relay each chunk
exactly, give up early on a member that sends nothing, and leave failover
to the caller.

RETRIES MATCH THE SDK UNDER llm: two more tries on a connection error, 408,
409, 429 or 5xx, waiting the Retry-After when it is 60 s or less, else about
0.5 then 1 s. Without them this client would surface 429s llm absorbed, and
the comparison would blame the client. A wait that would pass the deadline is
not taken: the error is returned, which says more than a timeout.

    python scripts/test_client.py    # both backends on a fake server, same results
    python scripts/test_quota.py     # the ledger, quota records and pacing, on a fake server
"""

from __future__ import annotations

import datetime as dt
import email.utils
import http.client
import json
import os
import pathlib
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
sys.path.insert(0, str(ROOT / "metrics"))
import connect  # noqa: E402
import endpoints  # noqa: E402
import formats  # noqa: E402

OPTIONS = DATA / "config" / "model-options.json"
MAX_RETRIES = 2
RETRY_STATUS = {408, 409, 429}
MAX_REPLY = 32_000_000                             # bytes
STOP = threading.Event()                           # set by a server shutting down


CLIENTS = ("llm", "builtin")
CHOICE = DATA / "config" / "client.json"           # the Settings page's choice


def chosen() -> tuple[str, str]:
    """The configured client and what set it: BENCH_CLIENT ("environment"),
    else the Settings page's choice ("settings"), else ("default") the
    built-in client, installed `llm` or not: since v0.3 the `llm` CLI is an
    alternative chosen on Settings, no longer the default (its trial week:
    RELEASE-PLAN Phase 2a). Read on every call, so a change applies to the
    next one."""
    env = (os.environ.get("BENCH_CLIENT") or "").strip().lower()
    if env:
        return env, "environment"
    try:
        saved = json.loads(CHOICE.read_text(encoding="utf-8")).get("client")
    except (OSError, ValueError, AttributeError):
        saved = None
    return (saved, "settings") if saved in CLIENTS else ("builtin", "default")


def backend(alias: str = "") -> str:
    """`llm` or `builtin`, as chosen(). A model on an endpoint connected by
    sign-in whose token renews, or that speaks another API than chat
    completions (ChatGPT's plan, native Anthropic or Gemini), is always
    `builtin`: llm can do neither."""
    if alias:
        entry = endpoints.entry_of(alias)
        if (entry.get("api") or "chat") != "chat" or (entry.get("connect") or {}).get("refreshes"):
            return "builtin"
    return chosen()[0]


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


class _Late(Exception):
    """The deadline passed while the reply was arriving."""


class _Broken(Exception):
    """The reply broke off part way (the connection dropped mid-stream)."""


def _wait_at_most(response, deadline: float) -> None:
    """The socket's timeout is per wait, so it is cut to what is left of the
    deadline before each read: a server trickling bytes cannot hold a seat
    past it. Best effort: the socket sits three wrappers down."""
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.settimeout(max(deadline - time.monotonic(), 0.01))
        except OSError:
            pass


def _chunks(response, deadline: float, relay: _Relay | None = None):
    """The body in pieces as they arrive. Raises _Late past the deadline (or
    the relay's first-piece cutoff, or when STOP is set), ValueError past
    MAX_REPLY, since the gateway is a long-lived process, and _Broken if the
    connection drops part way."""
    size = 0
    while True:
        limit = relay.cutoff(deadline) if relay else deadline
        if STOP.is_set() or time.monotonic() > limit:
            raise _Late
        _wait_at_most(response, limit)
        try:
            chunk = response.read1(65536)
        except TimeoutError:
            raise _Late from None
        except (OSError, http.client.HTTPException) as e:
            raise _Broken(str(e) or type(e).__name__) from None
        if not chunk:
            return
        size += len(chunk)
        if size > MAX_REPLY:
            raise ValueError(f"the reply is over {MAX_REPLY // 1_000_000} MB")
        yield chunk


def _lines(chunks):
    """Decoded lines from byte chunks, split as they arrive."""
    buf = b""
    for chunk in chunks:
        buf += chunk
        *done, buf = buf.split(b"\n")
        for line in done:
            yield line.rstrip(b"\r").decode("utf-8", "replace")
    if buf:
        yield buf.rstrip(b"\r").decode("utf-8", "replace")


def _events(lines):
    """Server-sent events' data, parsed: a dict per event, the string
    "[DONE]" for the end marker. Comments (`: OPENROUTER PROCESSING`) and
    event names are skipped; data lines of one event are joined, or, when
    they do not parse joined, read one by one: some servers leave out the
    blank line between events."""
    data: list[str] = []

    def flush() -> list:
        lines, payload = list(data), "\n".join(data).strip()
        data.clear()
        if not payload:
            return []
        try:
            return ["[DONE]" if payload == "[DONE]" else json.loads(payload)]
        except ValueError:
            pass
        out = []
        for one in lines:
            try:
                out.append("[DONE]" if one.strip() == "[DONE]" else json.loads(one))
            except ValueError:
                continue
        return out
    for line in lines:
        if not line:
            yield from flush()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    yield from flush()


class _Relay:
    """on_delta and on_chunk, kept from breaking the call: one that raises is
    dropped. `first_by`: when the first piece must have come by (monotonic),
    for a caller that would rather try another model than wait (route mode)."""

    def __init__(self, on_delta, started: float, on_chunk=None, first_by: float | None = None):
        self.fn, self.started, self.first = on_delta, started, None
        self.chunk_fn, self.first_by = on_chunk, first_by

    def mark(self) -> None:
        """The first piece (text, reasoning or a tool call) has arrived."""
        if self.first is None:
            self.first = round(time.monotonic() - self.started, 2)

    def cutoff(self, deadline: float) -> float:
        """What a wait may last until: the deadline, or sooner while the first piece is still awaited."""
        return min(deadline, self.first_by) if self.first is None and self.first_by is not None else deadline

    def __call__(self, kind: str, piece: str) -> None:
        if not piece:
            return
        self.mark()
        if self.fn is not None:
            try:
                self.fn(kind, piece)
            except Exception:                          # noqa: BLE001
                self.fn = None

    def chunk(self, chunk: dict) -> None:
        if self.chunk_fn is not None:
            try:
                self.chunk_fn(chunk)
            except Exception:                          # noqa: BLE001
                self.chunk_fn = None


def _chat_stream(events, relay: _Relay) -> dict:
    """A streamed chat completion as chat()'s fields. Each chunk that is not
    an error goes to relay.chunk as it came, for a proxy (route mode)."""
    text, reasoning, model, usage, finish, seen, done = [], [], "", {}, "", False, False
    calls: set = set()
    for ev in events:
        if ev == "[DONE]":
            done = True
            break
        if not isinstance(ev, dict):
            continue
        # An error sent mid-stream, as some relays do, or as OpenRouter does:
        # with a choice whose finish_reason is "error", after part of an answer.
        choices = ev.get("choices")
        if ev.get("error") or (isinstance(choices, list) and choices and isinstance(choices[0], dict)
                               and choices[0].get("finish_reason") == "error"):
            err = ev.get("error") or {}
            code = (err.get("code") if isinstance(err, dict) else None) or "stream"
            return {"error": f"Error: Error code: {code} - {ev}"}
        model = ev.get("model") or model
        if isinstance(ev.get("usage"), dict) and ev["usage"]:
            usage = ev["usage"]
        if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
            relay.chunk(ev)
            continue
        seen = True
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        if isinstance(delta, dict):
            if piece := _text(delta.get("reasoning_content") or delta.get("reasoning")):
                reasoning.append(piece)
                relay("reasoning", piece)
            if piece := _text(delta.get("content")):
                text.append(piece)
                relay("text", piece)
            for tc in delta.get("tool_calls") or ():
                if isinstance(tc, dict):
                    calls.add(tc["index"] if tc.get("index") is not None else tc.get("id"))
                    relay.mark()
        relay.chunk(ev)
        finish = choices[0].get("finish_reason") or finish
    if not seen:
        return {"error": "Error: client failed: the reply had no choices: the stream sent none"}
    if not (done or finish):
        return {"error": f"Error: client failed: the stream broke off before it finished"
                         f" ({len(''.join(text))} characters in)"}
    return {"text": "".join(text), "reasoning": "".join(reasoning), "model": model, "usage": usage,
            "finish": finish, "tool_calls": len(calls)}


def _chat_json(raw: bytes, relay: _Relay | None = None) -> dict:
    """A plain JSON chat completion (an upstream that does not stream) as
    chat()'s fields; to relay.chunk as the one chunk it would have streamed."""
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {"error": "Error: client failed: the reply was not JSON: " + raw[:1000].decode("utf-8", "replace")}
    if not isinstance(data, dict):
        return {"error": f"Error: client failed: unexpected reply: {str(data)[:1000]}"}
    # Some relays answer 200 with an error object instead of a status.
    if data.get("error") and not data.get("choices"):
        code = (data["error"].get("code") if isinstance(data["error"], dict) else None) or 200
        return {"error": f"Error: Error code: {code} - {data}"}
    choices = data.get("choices")
    # A 200 with no choice is a broken endpoint, not an empty answer: "empty"
    # is kept out of auto-retirement as a budget problem.
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        return {"error": f"Error: client failed: the reply had no choices: {str(data)[:1000]}"}
    message = choices[0].get("message") or {}
    calls = [tc for tc in message.get("tool_calls") or () if isinstance(tc, dict)]
    out = {"text": _text(message.get("content")),
           "reasoning": _text(message.get("reasoning_content") or message.get("reasoning")),
           "model": data.get("model") or "", "usage": data.get("usage") or {},
           "finish": choices[0].get("finish_reason") or "", "tool_calls": len(calls)}
    if relay is not None:
        if out["text"] or out["reasoning"] or calls:
            relay.mark()
        delta = {"role": "assistant", **{k: message[k] for k in ("content", "reasoning_content", "reasoning")
                                         if message.get(k)}}
        if calls:
            delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(calls)]
        relay.chunk({"id": data.get("id") or "", "object": "chat.completion.chunk", "created": data.get("created"),
                     "model": out["model"], "usage": out["usage"] or None,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": out["finish"] or "stop"}]})
    return out


def _reply(response, deadline: float, api: str, relay: _Relay) -> dict:
    """The reply's fields, read as it arrives, in the endpoint's API (`api`:
    chat, responses, anthropic or gemini). Raises _Late past the deadline."""
    ctype = (response.headers.get("content-type") or "").lower()
    stream = {"responses": _responses_events, "anthropic": _anthropic_events, "gemini": _gemini_events}
    try:
        if api == "responses" or (api in stream and "event-stream" in ctype):
            return stream[api](_events(_lines(_chunks(response, deadline, relay))), relay)
        if "event-stream" in ctype:
            return _chat_stream(_events(_lines(_chunks(response, deadline, relay))), relay)
        raw = b"".join(_chunks(response, deadline, relay))
    except _Broken as e:
        # Not retried: what arrived may already have been relayed.
        return {"error": f"Error: client failed: the reply broke off part way ({e})"}
    if api in stream:                                  # a native API answering with one JSON body
        try:
            return stream[api]([json.loads(raw or b"{}")], relay)
        except ValueError:
            return {"error": "Error: client failed: the reply was not JSON: " + raw[:1000].decode("utf-8", "replace")}
    if raw.lstrip().startswith(b"data:"):              # a stream sent without its content type
        return _chat_stream(_events(_lines([raw])), relay)
    return _chat_json(raw, relay)


# Anthropic's error types as the HTTP statuses that carry them, so an error
# sent mid-stream reads with the same markers (429, 529) as one sent as a status.
ANTHROPIC_STATUS = {"invalid_request_error": 400, "authentication_error": 401, "permission_error": 403,
                    "not_found_error": 404, "request_too_large": 413, "rate_limit_error": 429,
                    "api_error": 500, "overloaded_error": 529}
ANTHROPIC_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}


def _anthropic_events(events, relay: _Relay) -> dict:
    """A native Anthropic Messages stream (or one whole reply) as chat()'s
    fields. Finished only once message_stop (or a whole message) arrives."""
    text, reasoning, model, usage, finish, done = [], [], "", {}, "", False
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "error":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            return {"error": f"Error: Error code: {ANTHROPIC_STATUS.get(err.get('type'), err.get('type') or 'stream')} - {ev}"}
        if kind == "message":                          # a whole reply, not a stream
            text.append(formats.anthropic_text(ev))
            relay("text", text[-1])
            model, usage, finish, done = ev.get("model") or "", ev.get("usage") or {}, ev.get("stop_reason") or "", True
        elif kind == "message_start":
            message = ev.get("message") or {}
            model, usage = message.get("model") or "", dict(message.get("usage") or {})
        elif kind == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta":
                text.append(delta.get("text") or "")
                relay("text", delta.get("text") or "")
            elif delta.get("type") == "thinking_delta":
                reasoning.append(delta.get("thinking") or "")
                relay("reasoning", delta.get("thinking") or "")
        elif kind == "message_delta":
            finish = (ev.get("delta") or {}).get("stop_reason") or finish
            usage.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
        elif kind == "message_stop":
            done = True
    if not done:
        return {"error": f"Error: client failed: the stream broke off before it finished ({len(''.join(text))} characters in)"}
    return {"text": "".join(text), "reasoning": "".join(reasoning), "model": model, "usage": usage,
            "finish": ANTHROPIC_FINISH.get(finish, finish)}


def _gemini_events(events, relay: _Relay) -> dict:
    """A native Gemini generateContent stream (or one whole reply) as chat()'s
    fields. A stream has no end marker: it is finished once a candidate says
    why it stopped."""
    text, reasoning, model, usage, finish = [], [], "", {}, ""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("error"):
            err = ev["error"] if isinstance(ev["error"], dict) else {}
            return {"error": f"Error: Error code: {err.get('code') or 'stream'} - {ev}"}
        if (blocked := (ev.get("promptFeedback") or {}).get("blockReason")):
            return {"error": f"Error: client failed: the prompt was blocked ({blocked}): {ev}"}
        model = ev.get("modelVersion") or model
        if (meta := ev.get("usageMetadata")):
            usage = {"prompt_tokens": meta.get("promptTokenCount"), "completion_tokens": meta.get("candidatesTokenCount"),
                     **({"completion_tokens_details": {"reasoning_tokens": meta["thoughtsTokenCount"]}}
                        if meta.get("thoughtsTokenCount") else {})}
        candidate = (ev.get("candidates") or [{}])[0]
        for part in (candidate.get("content") or {}).get("parts") or ():
            if part.get("text"):
                (reasoning if part.get("thought") else text).append(part["text"])
                relay("reasoning" if part.get("thought") else "text", part["text"])
        finish = candidate.get("finishReason") or finish
    if not finish:
        return {"error": f"Error: client failed: the stream broke off before it finished ({len(''.join(text))} characters in)"}
    return {"text": "".join(text), "reasoning": "".join(reasoning), "model": model,
            "usage": {k: v for k, v in usage.items() if v is not None},
            "finish": {"STOP": "stop", "MAX_TOKENS": "length"}.get(finish, finish.lower())}


def _exchange(url: str, payload: dict, headers: dict, deadline: float, api: str,
              relay: _Relay, observe, retries: int = MAX_RETRIES) -> dict:
    """Send the call with the SDK's retries (`retries` more tries); chat()'s
    fields plus `attempts`. `observe(status, headers, body)` sees every
    response, for quota records."""
    attempts, attempt, fell_back = 0, 0, False
    while True:
        left = relay.cutoff(deadline) - time.monotonic()
        if left <= 0:
            return {"timed_out": True, "attempts": attempts}
        request = urllib.request.Request(url, json.dumps(payload).encode(), headers)
        status = 0
        attempts += 1
        try:
            with _OPENER.open(request, timeout=left) as r:
                status = r.status
                observe(status, r.headers, None)
                got = _reply(r, deadline, api, relay)
            return {"status": status, "attempts": attempts, **got}
        except _Late:
            return {"status": status, "timed_out": True, "attempts": attempts}
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace").strip()
            except (OSError, http.client.HTTPException):   # the body broke off
                text = ""
            try:
                shown = json.loads(text)
            except ValueError:
                shown = text
            observe(e.code, e.headers, shown)
            # An upstream that does not know stream_options, or will not stream:
            # the same call once more without them, not counted as a retry.
            if (api == "chat" and not fell_back and e.code in (400, 422) and payload.get("stream")
                    and "stream" in text.lower()):
                drop = ("stream_options",) if "stream_options" in text.lower() else ("stream", "stream_options")
                payload = {k: v for k, v in payload.items() if k not in drop}
                fell_back = True
                continue
            wait = _retry_after(e.headers)
            err = f"Error: Error code: {e.code}" + (f" - {shown}" if text else "") \
                + (f" Retry-After: {e.headers.get('retry-after')}" if e.headers.get("retry-after") else "") \
                + (f" (redirect to {e.headers.get('location')} not followed)" if 300 <= e.code < 400 else "")
            if not (e.code in RETRY_STATUS or e.code >= 500) or attempt >= retries:
                return {"status": e.code, "error": err, "attempts": attempts}
            wait = wait or _backoff(attempt)
            if time.monotonic() + wait >= relay.cutoff(deadline):
                return {"status": e.code, "error": err, "attempts": attempts}
            time.sleep(wait)
            attempt += 1
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            reason = getattr(e, "reason", e)
            # The deadline itself, or the OS giving up on a connection early
            # (a dropped SYN), which is a connection error and is retried.
            if isinstance(reason, TimeoutError) and time.monotonic() >= relay.cutoff(deadline) - 0.5:
                return {"timed_out": True, "attempts": attempts}
            err = f"Error: client failed: Connection error. ({reason})"
            wait = _backoff(attempt)
            if attempt >= retries or time.monotonic() + wait >= relay.cutoff(deadline):
                return {"error": err, "attempts": attempts}
            time.sleep(wait)
            attempt += 1
        except ValueError as e:                        # over MAX_REPLY, or a malformed URL
            return {"status": status, "error": f"Error: client failed: {e}", "attempts": attempts}


def _estimate(prompt: str, system: str, messages: list[dict] | None) -> int:
    """Tokens a prompt will take, roughly (four characters a token), for
    pacing a tokens-per-minute limit before the provider has counted."""
    if messages is None:
        return (len(prompt) + len(system)) // 4 + 1
    return sum(len(m.get("content") if isinstance(m.get("content"), str) else _text(m.get("content")))
               for m in messages if isinstance(m, dict)) // 4 + 1


def _paid_hold(alias: str, served: str) -> str:
    """The free-tier guard's refusal, or "" when the model may be called."""
    if os.environ.get("BENCH_ALLOW_PAID") == "1" or alias in (endpoints.connections().get("allow_paid") or ()):
        return ""
    try:
        import alerts
        alert = alerts.active().get(f"model:paid:{served}") or {}
    except Exception:                                  # noqa: BLE001
        return ""
    if alert.get("kind") != "went paid":
        return ""
    return (f"Error: client failed: not sent: the free-tier guard holds {alias}, which is listed with a"
            f" price now ({alert.get('message', '')[:160]}), so a call may be billed: payment required"
            f" is assumed. Allow it by adding {alias!r} to allow_paid in config/connections.json,"
            f" or for one run with BENCH_ALLOW_PAID=1.")


def _budget(payload: dict, usage: dict, alias: str) -> tuple[str, int, int] | None:
    """(option name, budget used, the larger budget) for retrying an empty
    answer that ran out of output budget, or None when it cannot grow."""
    key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
    try:
        current = int(float(payload.get(key) or usage.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        return None
    if current <= 0:
        return None
    bigger = current * 2
    try:
        import catalog
        ceiling = catalog.info(alias).get("max_output")
    except Exception:                                  # noqa: BLE001
        ceiling = None
    if isinstance(ceiling, int) and ceiling > 0:
        bigger = min(bigger, ceiling)
    return (key, current, bigger) if bigger > current else None


def _swapped(requested: str, served: str) -> bool:
    """The reply names another model, beyond spelling: a vendor prefix, `:free`
    and a date or version tail are the same model (sources.norm_model)."""
    if not served or not requested or served == requested:
        return False
    import sources
    return sources.norm_model(served) != sources.norm_model(requested)


def _caller() -> str:
    """The script making the call: gateway, perf/run, suite/run, serve, ..."""
    p = pathlib.Path(sys.argv[0] if sys.argv and sys.argv[0] else "python")
    return f"{p.parent.name}/{p.stem}" if p.parent.name in ("perf", "suite", "scripts", "metrics") else p.stem


def chat(alias: str, prompt: str, system: str = "", timeout: float = 420,
         messages: list[dict] | None = None, on_delta=None, purpose: str = "",
         body: dict | None = None, on_chunk=None, first_token: float | None = None,
         retries: int | None = None) -> dict:
    """One chat completion. Returns {text, reasoning, error, timed_out, status,
    model (as served), usage, finish, tool_calls (how many), first_token_s,
    attempts, seconds}, and `budget_retry` when an empty answer was tried
    again; `error` is "" on success, else the contract text. `messages`, when
    given, are sent as they are instead of system + prompt: the gateway's
    requests that carry images, which `llm -m` cannot send. `on_delta(kind,
    text)` hears the answer ("text") and the reasoning ("reasoning") as they
    arrive. `purpose` goes into the ledger line beside the calling script's name.

    For a proxy (the gateway's route mode), chat completions only: `body`,
    the request's other fields (tools, tool_choice, response_format,
    temperature, ...), which win over the model's own options; `on_chunk`,
    which hears each stream chunk exactly as it came (or the one a whole
    reply would have been); `first_token`, seconds to wait for the first
    piece (text, reasoning or a tool call) before giving up as timed out;
    `retries`, how many more tries a 429, 5xx or connection error gets (the
    SDK's two by default). An empty answer is not tried again: it is relayed."""
    started, t0 = time.monotonic(), time.time()
    deadline = started + timeout
    res = {"text": "", "reasoning": "", "error": "", "timed_out": False, "status": 0,
           "model": "", "usage": {}, "finish": "", "tool_calls": 0, "first_token_s": None, "attempts": 0}
    ctx = {"connection": "", "endpoint": "", "ticket": None, "requested": "", "refused": False}
    relay = _Relay(on_delta, started, on_chunk, started + first_token if first_token else None)
    try:
        _call(alias, prompt, system, messages, deadline, relay, res, ctx, body,
              MAX_RETRIES if retries is None else retries)
    finally:
        res["seconds"] = round(time.monotonic() - started, 1)
        _record(alias, res, ctx, t0, purpose)
    return res


# Anthropic's API version header: required on every call, and the one its
# Messages API has been served under since it launched.
ANTHROPIC_VERSION = "2023-06-01"


def _request(api: str, base: str, model: str, alias: str, prompt: str, system: str,
             messages: list[dict] | None, body: dict | None) -> tuple[str, dict]:
    """(url, payload) for one call in the endpoint's API. Raises ValueError
    for what that API cannot be sent from here."""
    if body is not None and api != "chat":
        raise ValueError(f"a request's own fields (tools and the rest) go to chat completions only, not the {api} API")
    if api == "responses":
        # ChatGPT's plan (metrics/connect.py): streamed, unstored, no system
        # message items, no sampling or length options.
        if messages is not None:
            # ponytail: chat parts are not Responses input items; converting
            # them (input_text, input_image) waits for a caller that needs it.
            raise ValueError("given messages are sent to chat completions only, not the Responses API")
        return base + "/responses", {"model": model, "input": [{"role": "user", "content": prompt}],
                                     "stream": True, "store": False, **({"instructions": system} if system else {})}
    if messages is None:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    opts = _options(alias)
    if api == "anthropic":
        # formats.py builds the native request; the model's options that it
        # knows (max_tokens, temperature, top_p, stop) go with it.
        return base + "/messages", formats.anthropic_request(messages, model, opts, stream=True)
    if api == "gemini":
        # The base is the API's version root (.../v1beta), as its model list is under it.
        return (f"{base}/models/{urllib.parse.quote(model.removeprefix('models/'), safe='-._~')}"
                ":streamGenerateContent?alt=sse", formats.gemini_request(messages, opts))
    payload = {"model": model, "messages": messages, **opts}
    if body:
        if "max_completion_tokens" in body:
            payload.pop("max_tokens", None)        # both at once are refused by some APIs
        payload.update({k: v for k, v in body.items() if k not in ("model", "messages", "stream", "stream_options")})
    return base + "/chat/completions", {**payload, "stream": True, "stream_options": {"include_usage": True}}


def _call(alias, prompt, system, messages, deadline, relay, res, ctx, body, retries) -> None:
    reg = endpoints.registrations().get(alias)
    if reg is None:
        res["error"] = f"Error: client failed: no model is registered as {alias!r}"
        return
    base = (reg.get("api_base") or "").rstrip("/")
    # urllib would also open file:// and ftp:// URLs given as a base.
    if not base.startswith(("http://", "https://")):
        res["error"] = f"Error: client failed: endpoint base must be http(s), not {base!r}"
        return
    model = ctx["requested"] = reg.get("model_name") or alias
    api = endpoints.entry_of(alias).get("api") or "chat"
    try:
        url, payload = _request(api, base, model, alias, prompt, system, messages, body)
    except ValueError as e:                            # formats.Unsupported is one too
        res["error"] = f"Error: client failed: {e}"
        return
    headers = {"Content-Type": "application/json", "User-Agent": "benchwarmers",
               **reg.get("headers", {})}
    if api == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
    if name := reg.get("api_key_name", ""):
        if why := connect.fresh(name):               # a signed-in token past renewal
            res["error"] = f"Error: client failed: {why}"
            return
        # As llm does: a named key that is not set stops here, rather than
        # sending an unauthenticated call whose 401 would read as entitlement.
        if not (key := endpoints.keys().get(name, "")):
            res["error"] = f"Error: client failed: no key named {name!r} is set (llm keys set {name})"
            return
        headers.update(endpoints.auth_headers(api, key))

    try:
        if str(ROOT / "suite") not in sys.path:      # route reads the registry through suite/run.py
            sys.path.insert(0, str(ROOT / "suite"))
        import route
        ctx["connection"] = route.connection_of(alias)
        ctx["endpoint"] = endpoints.endpoint_name(reg) or ""
    except Exception:                                  # noqa: BLE001  -- bookkeeping never stops a call
        ctx["connection"] = ctx["connection"] or "?"
    if why := _paid_hold(alias, model):
        res["error"], ctx["refused"] = why, True
        return
    try:
        import pacing
        # A proxy that would rather try another model waits for room no
        # longer than for a first piece. ponytail: the in-flight slot then
        # expires that much sooner too; per-call expiry if it ever matters.
        ctx["ticket"], why = pacing.admit(alias, ctx["connection"],
                                          _estimate(prompt, system, messages) + len(json.dumps(body or "")) // 4,
                                          relay.cutoff(deadline) - time.monotonic())
    except Exception:                                  # noqa: BLE001  -- a broken limit file is not a refusal
        why = ""
    if why:
        res["error"], ctx["refused"] = why, True
        return
    # The ledger's `t`: when the call went out, after any wait for room, since
    # that is the moment a provider's window (and pacing's) counts it from.
    ctx["sent"] = time.time()

    def observe(status, hdrs, body) -> None:
        try:
            import quota
            quota.observe(alias, ctx["connection"], base, status, hdrs, body)
        except Exception:                              # noqa: BLE001
            pass

    got = _exchange(url, payload, headers, deadline, api, relay, observe, retries)
    # Not for a proxy: what it relayed already went to its caller.
    if (api in ("chat", "anthropic") and relay.chunk_fn is None and not got.get("error")
            and not got.get("timed_out") and got.get("text") == "" and not got.get("tool_calls")
            and got.get("finish") == "length" and (grow := _budget(payload, got.get("usage") or {}, alias))):
        key, used, bigger = grow
        again = _exchange(url, {**payload, key: bigger}, headers, deadline, api, relay, observe, retries)
        attempts = got.get("attempts", 0) + again.get("attempts", 0)
        if again.get("error") or again.get("timed_out"):
            # The empty answer stands: the retry's failure (a 400 for a budget
            # over the model's cap, a 429) is not the model's, and an error
            # here would count toward auto-retirement where "empty" does not.
            got["budget_retry"] = {key: [used, bigger], "answered": False,
                                   "failed": (again.get("error") or "timed out")[:200]}
        else:
            got = again
            got["budget_retry"] = {key: [used, bigger], "answered": bool(again.get("text"))}
        got["attempts"] = attempts
    res.update(got)
    res["first_token_s"] = relay.first


def _record(alias: str, res: dict, ctx: dict, t0: float, purpose: str) -> None:
    """The ledger line, the silent-swap note, and the in-flight slot freed.
    Never raises: the answer matters more than its bookkeeping."""
    try:
        swap = not res["error"] and _swapped(ctx["requested"], res["model"])
        if swap:
            import learn
            # Noted and logged when it is new; every line in the ledger says so.
            if (learn.learned().get(alias, {}).get("served_as") or {}).get("served") != res["model"]:
                learn.note_served_as(alias, ctx["requested"], res["model"])
                print(f"note: {alias} asked for {ctx['requested']} and was served {res['model']}"
                      " (a silent swap?)", file=sys.stderr)
        status = ("refused" if ctx["refused"] else "timeout" if res["timed_out"] else
                  "error" if res["error"] else "ok" if res["text"] or res.get("tool_calls") else "empty")
        import ledger
        t = ctx.get("sent") or t0
        row = {"t": round(t, 3), "at": dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds"),
               "alias": alias, "connection": ctx["connection"], "endpoint": ctx["endpoint"],
               "served": res["model"], "status": status, "attempts": res.get("attempts", 0),
               **ledger.tokens(res["usage"]), "seconds": res["seconds"],
               "caller": _caller(), "purpose": purpose or os.environ.get("BENCH_PURPOSE", "")}
        if swap:
            row["swap"] = True
        if res.get("budget_retry"):
            row["budget_retry"] = res["budget_retry"]
        ledger.append(row)
    except Exception as e:                             # noqa: BLE001
        # The slot stays taken until it expires (the call's deadline and a
        # minute): released now, this call would count nowhere at all.
        print(f"note: the usage ledger was not written: {e}", file=sys.stderr)
        return
    try:
        import pacing
        pacing.release(ctx["ticket"])
    except Exception:                                  # noqa: BLE001  -- the lock timed out: it expires instead
        pass


def _responses_events(events, relay: _Relay) -> dict:
    """A streamed Responses API reply as chat()'s fields. It counts as an answer
    only once `response.completed` arrives (OpenAI's rule for the plan flow); a
    usage limit can arrive mid-stream as `response.failed`."""
    deltas, final = [], None
    for event in events:
        kind = event.get("type", "") if isinstance(event, dict) else ""
        if kind == "response.output_text.delta":
            deltas.append(event.get("delta") or "")
            relay("text", event.get("delta") or "")
        elif kind == "response.reasoning_summary_text.delta":
            relay("reasoning", event.get("delta") or "")
        elif kind in ("response.completed", "response.failed", "response.incomplete", "error"):
            final = event
    if final is None:
        return {"error": "Error: client failed: the stream ended before response.completed"}
    resp = final.get("response") or {}
    if final["type"] != "response.completed":
        err = resp.get("error") or resp.get("incomplete_details") or final
        code = err.get("code") if isinstance(err, dict) else ""
        return {"error": f"Error: Error code: {code or final['type']} - {err}"}
    text = "".join(part.get("text") or "" for item in resp.get("output") or []
                   if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
    return {"text": text or "".join(deltas), "model": resp.get("model") or "",
            "usage": resp.get("usage") or {}}


def _responses(raw: bytes) -> dict:
    """The same, from a whole body (the self-check's canned streams)."""
    return _responses_events(_events(_lines([raw])), _Relay(None, time.monotonic()))


def output(res: dict, timeout: float) -> str:
    """The result as ask_once's text: the answer as llm prints it (with its
    closing newline), the error, or the timeout sentinel."""
    if res["timed_out"]:
        return f"<<TIMED OUT after {timeout:g}s>>"
    return res["error"] or (res["text"] + "\n" if res["text"] else "")


LOG_LOCKED = "sqlite3.OperationalError: database is locked"


def llm_log_failed_only(stdout: str, stderr: str) -> bool:
    """True when a non-zero `llm -m` exit is only llm failing to write its own
    log after printing the answer. Seats called at once each open llm's
    logs.db, and the loser's crash (2026-10-22, inkling-small-free answered
    "pong") turned an answer into an error. llm writes the log only once the
    response is complete, so stdout is the whole answer; the same lock in the
    migration llm runs before the call leaves stdout empty and stays an error.
    Not `llm -n`: metrics/cost.py reads that log for the llm backend's tokens."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return bool((stdout or "").strip()) and bool(lines) and lines[-1] == LOG_LOCKED


def _selftest() -> None:
    """The stream parsers (chat completions and Responses): an answer with its
    reasoning and usage, a mid-stream error, a cut-off stream; and which `llm`
    failures are only its log."""
    tb = "Traceback (most recent call last):\n  File \"llm/cli.py\", in prompt\n" + LOG_LOCKED + "\n"
    assert llm_log_failed_only("pong\n", tb)
    assert not llm_log_failed_only("", tb)                                  # locked before the call
    assert not llm_log_failed_only("half an ans", "openai.APIError: stream cut\n")
    assert not llm_log_failed_only("pong", tb + "Error: something after\n")
    ok = (b'event: x\ndata: {"type":"response.output_text.delta","delta":"re"}\n\n'
          b'data: {"type":"response.completed","response":{"model":"m","usage":{"total_tokens":3},'
          b'"output":[{"type":"reasoning"},{"type":"message","content":[{"type":"output_text","text":"ready"}]}]}}\n')
    assert _responses(ok) == {"text": "ready", "model": "m", "usage": {"total_tokens": 3}}, _responses(ok)
    limit = (b'data: {"type":"response.output_text.delta","delta":"half"}\n'
             b'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n')
    assert _responses(limit)["error"].startswith("Error: Error code: subscription_sharing_usage_limit_exceeded")
    assert "before response.completed" in _responses(b'data: {"type":"response.output_text.delta","delta":"x"}\n')["error"]

    def chunks(*evs):                                  # each event split across two network reads
        body = "".join((e if e.startswith(":") else f"data: {e}") + "\n\n" if isinstance(e, str)
                       else f"data: {json.dumps(e)}\n\n" for e in evs).encode()
        return [body[:len(body) // 2], body[len(body) // 2:]]
    heard = []
    relay = _Relay(lambda k, t: heard.append((k, t)), time.monotonic())
    got = _chat_stream(_events(_lines(chunks(
        ": OPENROUTER PROCESSING",
        {"model": "m-2", "choices": [{"delta": {"reasoning_content": "hmm "}}]},
        {"choices": [{"delta": {"reasoning": "so"}}]},
        {"choices": [{"delta": {"content": "rea"}}]},
        {"choices": [{"delta": {"content": [{"type": "text", "text": "dy"}]}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}, "[DONE]"))), relay)
    assert got == {"text": "ready", "reasoning": "hmm so", "model": "m-2", "finish": "stop", "tool_calls": 0,
                   "usage": {"prompt_tokens": 3, "completion_tokens": 5}}, got
    assert heard == [("reasoning", "hmm "), ("reasoning", "so"), ("text", "rea"), ("text", "dy")], heard
    assert relay.first is not None
    cut = _chat_stream(_events(_lines(chunks({"choices": [{"delta": {"content": "half an"}}]}))), _Relay(None, 0))
    assert "broke off before it finished (7 characters in)" in cut["error"], cut
    err = _chat_stream(_events(_lines(chunks({"error": {"code": 429, "message": "Rate limit exceeded"}}))),
                       _Relay(None, 0))
    assert err["error"].startswith("Error: Error code: 429 - "), err
    # OpenRouter's mid-stream error: part of an answer, then a choice finishing "error".
    err = _chat_stream(_events(_lines(chunks(
        {"choices": [{"delta": {"content": "half"}}]},
        {"error": {"code": "server_error", "message": "upstream died"},
         "choices": [{"delta": {"content": ""}, "finish_reason": "error"}]}, "[DONE]"))), _Relay(None, 0))
    assert err["error"].startswith("Error: Error code: server_error - "), err
    assert "no choices" in _chat_stream(_events(_lines(chunks("[DONE]"))), _Relay(None, 0))["error"]
    bad = _Relay(lambda k, t: 1 / 0, 0)                 # a callback that raises is dropped, the call goes on
    bad("text", "x")
    assert bad.fn is None
    assert _chat_json(b'{"choices":[{"message":{"content":"","reasoning":"r"},"finish_reason":"length"}]}') \
        == {"text": "", "reasoning": "r", "model": "", "usage": {}, "finish": "length", "tool_calls": 0}
    # A proxy hears every chunk as it came, and a whole reply as the one chunk it would have streamed.
    seen: list = []
    relay = _Relay(None, 0, seen.append, first_by=0)
    assert relay.cutoff(10) == 0 and _Relay(None, 0).cutoff(10) == 10      # awaiting the first piece
    call = {"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{}"}}
    got = _chat_stream(_events(_lines(chunks({"choices": [{"delta": {"tool_calls": [call]}}]},
                                             {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, "[DONE]"))), relay)
    assert got["tool_calls"] == 1 and len(seen) == 2 and seen[0]["choices"][0]["delta"]["tool_calls"] == [call]
    assert relay.cutoff(10) == 10                                           # a tool call is a first piece
    seen.clear()
    _chat_json(b'{"id":"x","choices":[{"message":{"content":null,"tool_calls":[{"id":"c1","function":{}}]},'
               b'"finish_reason":"tool_calls"}]}', _Relay(None, 0, seen.append))
    assert seen[0]["choices"][0]["delta"]["tool_calls"] == [{"index": 0, "id": "c1", "function": {}}], seen
    # Native streams: Anthropic Messages and Gemini, read into the same fields.
    got = _anthropic_events(_events(_lines(chunks(
        {"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 9}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hm"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "ready"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        {"type": "message_stop"}))), _Relay(None, 0))
    assert got == {"text": "ready", "reasoning": "hm", "model": "claude-x", "finish": "stop",
                   "usage": {"input_tokens": 9, "output_tokens": 4}}, got
    err = _anthropic_events([{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}], _Relay(None, 0))
    assert err["error"].startswith("Error: Error code: 529 - "), err
    assert "broke off" in _anthropic_events([{"type": "message_start", "message": {}}], _Relay(None, 0))["error"]
    got = _gemini_events([{"candidates": [{"content": {"parts": [{"text": "hm", "thought": True}, {"text": "rea"}]}}]},
                          {"candidates": [{"content": {"parts": [{"text": "dy"}]}, "finishReason": "STOP"}],
                           "modelVersion": "gemini-x", "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}],
                         _Relay(None, 0))
    assert got == {"text": "ready", "reasoning": "hm", "model": "gemini-x", "finish": "stop",
                   "usage": {"prompt_tokens": 3, "completion_tokens": 2}}, got
    assert _gemini_events([{"error": {"code": 429, "message": "quota"}}], _Relay(None, 0))["error"].startswith(
        "Error: Error code: 429 - ")
    assert "broke off" in _gemini_events([{"candidates": [{"content": {"parts": [{"text": "x"}]}}]}], _Relay(None, 0))["error"]
    assert not _swapped("openai/gpt-oss-120b:free", "gpt-oss-120b") and _swapped("llama-3.3-70b", "llama-3.1-8b")
    print("ok")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        return 0
    if len(sys.argv) != 3:
        print(__doc__.split("\n\n")[0])
        return 2
    res = chat(sys.argv[1], sys.argv[2], timeout=120)
    print(output(res, 120))
    return 1 if res["error"] or res["timed_out"] else 0


if __name__ == "__main__":
    sys.exit(main())
