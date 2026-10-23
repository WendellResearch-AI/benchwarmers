"""Other APIs' shapes, translated to and from the OpenAI chat shape the bench speaks.

    python scripts/test_formats.py      # the round trips, and every route against fake seats

The gateway answers clients that speak OpenAI chat completions, OpenAI's
legacy completions, OpenAI Responses, Anthropic Messages, Ollama and Gemini.
Each of those is translated here into OpenAI-style messages (images as
`image_url` parts with data: or http(s) URLs, so the gateway's vision
filtering sees them), and the panel's answer back into the format's own
response and streaming events. Pure functions, stdlib only, no I/O: the
gateway does the HTTP.

The same shapes run the other way for the built-in client, which will call
the native Anthropic, Gemini, Ollama and Responses APIs with them (not wired
yet; metrics/client.py speaks chat completions and Responses itself today):

    inbound, the gateway answering a client          outbound, the client calling an API
    <fmt>_ask(body)      native request -> Ask       <fmt>_request(messages, ...)  OpenAI messages -> native request
    <fmt>_reply(text...) an answer -> native reply   <fmt>_text(reply)             native reply -> its text
    <Fmt>Stream          an answer as it comes ->    <fmt>_delta(event)            one native stream event -> its text
                         native stream bytes
    <fmt>_error(status, message) -> native error body

An Ask is a dict: `model` (as the client named it), `messages` (OpenAI
style), `stream`, `options` (max_tokens, temperature, top_p, stop, when
given) and the bench's own `with` and `reply`, read from any format's body
(an SDK's extra_body). The gateway reads `options` but does not pass them
on: each seat uses its own call options (config/model-options.json).

WHAT IS REFUSED, NOT DROPPED
----------------------------
A request carrying tools, or a conversation holding tool calls or results,
is refused (Unsupported, a 400) rather than answered as if the tools were not
there: a panel's reply is several models' text, not a tool call, and an agent
that waits for one would hang or loop. A route mode that proxies one model
with tools is planned. So is anything else that would be lost on the way: a
PDF or audio part, an uploaded file id, a stored previous response. A
model's own reasoning from an earlier turn (Anthropic thinking blocks,
Responses reasoning items, Gemini thought parts) is dropped: each seat
reasons for itself. Structured-output settings (Ollama `format`, Gemini's
responseSchema, Responses `text.format`) are ignored: a panel answers in
markdown.

Token counts in replies are estimates (characters / 4 plus 10%), as the
gateway's size check makes; durations are real.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import mimetypes
import secrets
import time
import urllib.parse
from typing import Callable, NamedTuple

TOOLS = ("this request carries tools or tool calls, and the bench gateway answers with a panel of models,"
         " not a tool call, so tool use is not supported yet (a route mode that proxies one model, tools"
         " and all, is coming). Send it without tools.")
REPLIES = ("all", "merged")       # bench_collections.REPLIES; not imported, so this file stands alone
LEAD = ("system", "developer")    # newer OpenAI SDKs send "developer" where older ones send "system"
# The Ollama API version whose shapes these follow. Clients check it to
# decide which fields to send; a lower one would have them hold back.
OLLAMA_VERSION = "0.12.0"
# Anthropic requires max_tokens; this is what the outbound request asks for
# when the caller set none (the seat's own option, in the built-in client).
ANTHROPIC_MAX_TOKENS = 8192
ANTHROPIC_ERRORS = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
                    404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error",
                    503: "overloaded_error"}
GEMINI_STATUS = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
                 413: "INVALID_ARGUMENT", 415: "INVALID_ARGUMENT", 429: "RESOURCE_EXHAUSTED",
                 503: "UNAVAILABLE"}


class Unsupported(ValueError):
    """A request that cannot be put to a panel without losing part of it:
    answered 400 with the reason, never silently trimmed."""


class UpstreamError(RuntimeError):
    """An error a native API sent in place of an answer (outbound)."""


def estimate(text: str) -> int:
    """Tokens in `text`: characters / 4 plus 10%, which errs high
    (gateway.estimate_tokens says why). At least 1 for any text: a count
    of 0 for "hi" reads as nothing sent."""
    return max(1, int(len(text) / 4 * 1.1)) if text else 0


# ---- the parts every format shares ---------------------------------------

def image(url: str) -> dict:
    """An OpenAI image part."""
    return {"type": "image_url", "image_url": {"url": url}}


def text_part(text) -> dict:
    return {"type": "text", "text": str(text or "")}


def data_url(mime: str, data: str) -> str:
    return f"data:{mime};base64,{data}"


def split_data_url(url: str) -> tuple[str, str] | None:
    """("image/png", "<base64>") for a base64 data: URL, else None."""
    if url[:5].lower() != "data:":
        return None
    head, sep, data = url[5:].partition(",")
    mime, _, enc = head.partition(";")
    return (mime or "application/octet-stream", data) if sep and enc.lower() == "base64" else None


def sniff(b64: str) -> str:
    """An image's type from its first bytes, for Ollama, which sends bare
    base64 with no type. PNG when it cannot tell: every model reads one."""
    try:
        head = base64.b64decode(b64[:24] + "=" * (-len(b64[:24]) % 4), validate=False)
    except (binascii.Error, ValueError):
        return "image/png"
    for magic, mime in ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
                        (b"RIFF", "image/webp"), (b"BM", "image/bmp")):
        if head.startswith(magic):
            return mime
    return "image/png"


def _image_mime(mime: str, where: str) -> str:
    if not str(mime or "").lower().startswith("image/"):
        raise Unsupported(f"{where} is {mime or 'of no type'}; only images can be passed to a panel")
    return mime


def _content(parts: list[dict]) -> str | list[dict]:
    """Text alone as one string, as most clients send it; with an image, the parts."""
    if all(p["type"] == "text" for p in parts):
        return "\n\n".join(p["text"] for p in parts)
    return parts


def parts_of(content) -> list[dict]:
    """OpenAI message content as a list of text and image_url parts."""
    if content is None:
        return []
    if isinstance(content, str):
        return [text_part(content)] if content else []
    out = []
    for p in content if isinstance(content, list) else ():
        if not isinstance(p, dict):
            continue
        if p.get("type") == "image_url":
            u = p.get("image_url")
            out.append(image(u.get("url", "") if isinstance(u, dict) else str(u or "")))
        elif p.get("type") in ("text", "input_text", "output_text"):
            out.append(text_part(p.get("text")))
    return out


def text_of(content) -> str:
    return "\n\n".join(p["text"] for p in parts_of(content) if p["type"] == "text")


def _image_url(part: dict) -> str:
    return part["image_url"]["url"]


def _no_tools(tools) -> None:
    if tools:
        raise Unsupported(TOOLS)


def _ask(body: dict, model, messages: list[dict], stream, options: dict) -> dict:
    """The Ask every format's request becomes, with the bench's own fields
    checked as POST /v1/chat/completions always checked them."""
    if not isinstance(model, str):
        raise Unsupported('"model" must be a collection name such as bench/core')
    nominated, opts = body.get("with") or [], body.get("bench") or {}
    if not (isinstance(nominated, list) and all(isinstance(a, str) for a in nominated)):
        raise Unsupported('"with" must be a list of model aliases')
    if not isinstance(opts, dict) or opts.get("reply", "all") not in REPLIES:
        raise Unsupported('"bench" must be an object whose "reply" is "all" or "merged"')
    stop = options.get("stop")
    if isinstance(stop, str):
        options = {**options, "stop": [stop]}
    return {"model": model, "messages": messages, "stream": bool(stream),
            "options": {k: v for k, v in options.items() if v is not None},
            "with": nominated, "reply": opts.get("reply") or ""}


def _messages(value, what: str = '"messages"') -> list[dict]:
    if not (isinstance(value, list) and all(isinstance(m, dict) for m in value)):
        raise Unsupported(f"{what} must be a list of message objects")
    return value


def _now() -> int:
    return int(time.time())


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(12)


def sse(data: dict | str, event: str = "") -> bytes:
    """One server-sent event."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return (f"event: {event}\n" if event else "").encode() + f"data: {payload}\n\n".encode()


class Stream:
    """An answer sent as it comes, in one format's stream shape. Each method
    returns the bytes to write: start() right after the 200, text() for each
    piece, then end() or error(), and keepalive() while waiting, so a client
    or proxy with an idle timeout keeps the connection."""
    content_type = "text/event-stream"

    def __init__(self, model: str, input_tokens: int = 0, **_):
        self.model, self.input_tokens, self.said = model, input_tokens, []
        self.started = time.monotonic()

    def start(self) -> bytes:
        return b""

    def text(self, text: str) -> bytes:
        raise NotImplementedError

    def end(self, bench: dict | None = None) -> bytes:
        raise NotImplementedError

    def error(self, status: int, message: str) -> bytes:
        raise NotImplementedError

    def keepalive(self) -> bytes:
        return b": keep-alive\n\n"                     # an SSE comment: every SSE parser skips it

    def output_tokens(self) -> int:
        return estimate("".join(self.said))


# ---- OpenAI chat completions -----------------------------------------------

def chat_ask(body: dict) -> dict:
    """POST /v1/chat/completions. The messages are already OpenAI's; the
    gateway's own image check reads them as they are."""
    messages = _messages(body.get("messages"))
    _no_tools(body.get("tools") or body.get("functions"))
    if any(m.get("role") in ("tool", "function") or m.get("tool_calls") or m.get("function_call")
           for m in messages):
        raise Unsupported(TOOLS)
    return _ask(body, body.get("model") or "", messages, body.get("stream"),
                {"max_tokens": body.get("max_completion_tokens") or body.get("max_tokens"),
                 "temperature": body.get("temperature"), "top_p": body.get("top_p"), "stop": body.get("stop")})


def chat_reply(text: str, model: str, bench: dict | None = None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    """A chat.completion, as the gateway has always answered: no usage, the
    bench beside the OpenAI fields (clients ignore keys they do not know)."""
    return {"id": _new_id("chatcmpl-"), "object": "chat.completion", "created": _now(), "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "bench": bench}


def chat_error(status: int, message: str) -> dict:
    return {"error": {"message": message, "type": "bench_gateway", "code": status}}


class ChatStream(Stream):
    """chat.completion.chunk events, then `data: [DONE]`. The bench rides on
    the last chunk, the one with the finish_reason."""

    def __init__(self, model: str, input_tokens: int = 0, **kw):
        super().__init__(model, input_tokens, **kw)
        self.id, self.created = _new_id("chatcmpl-"), _now()

    def _chunk(self, delta: dict, finish=None, **extra) -> bytes:
        return sse({"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra})

    def start(self) -> bytes:
        return self._chunk({"role": "assistant", "content": ""})

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return self._chunk({"content": text})

    def end(self, bench=None) -> bytes:
        return self._chunk({}, "stop", **({"bench": bench} if bench is not None else {})) + b"data: [DONE]\n\n"

    def error(self, status: int, message: str) -> bytes:
        # The OpenAI SDKs raise on a chunk whose data holds "error".
        return sse(chat_error(status, message)) + b"data: [DONE]\n\n"


# ---- OpenAI legacy completions ---------------------------------------------

def completion_ask(body: dict) -> dict:
    """POST /v1/completions: one prompt, as a user message."""
    prompt = body.get("prompt")
    if isinstance(prompt, list) and len(prompt) == 1 and isinstance(prompt[0], str):
        prompt = prompt[0]
    if not isinstance(prompt, str):
        raise Unsupported('"prompt" must be one string (a list of prompts, or of token ids, is not supported)')
    return _ask(body, body.get("model") or "", [{"role": "user", "content": prompt}], body.get("stream"),
                {"max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
                 "top_p": body.get("top_p"), "stop": body.get("stop")})


def completion_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    return {"id": _new_id("cmpl-"), "object": "text_completion", "created": _now(), "model": model,
            "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
            "bench": bench}


class CompletionStream(ChatStream):
    def _chunk(self, delta: dict, finish=None, **extra) -> bytes:
        return sse({"id": self.id, "object": "text_completion", "created": self.created, "model": self.model,
                    "choices": [{"text": delta.get("content", ""), "index": 0, "logprobs": None,
                                 "finish_reason": finish}], **extra})

    def start(self) -> bytes:
        return b""


# ---- Anthropic Messages ----------------------------------------------------

def _anthropic_blocks(content, where: str) -> str | list[dict]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise Unsupported(f"{where}: content must be a string or a list of content blocks")
    parts = []
    for b in content:
        kind = b.get("type") if isinstance(b, dict) else None
        if kind == "text":
            parts.append(text_part(b.get("text")))
        elif kind == "image":
            src = b.get("source") if isinstance(b.get("source"), dict) else {}
            if src.get("type") == "base64":
                data = str(src.get("data") or "")
                parts.append(image(data_url(_image_mime(src.get("media_type") or sniff(data), "an image block"), data)))
            elif src.get("type") == "url":
                parts.append(image(str(src.get("url") or "")))
            else:
                raise Unsupported(f"an image source of type {src.get('type')!r} cannot be passed on;"
                                  " send it as base64 or a url")
        elif isinstance(kind, str) and kind.endswith(("tool_use", "tool_result")):
            raise Unsupported(TOOLS)
        elif kind in ("thinking", "redacted_thinking"):
            continue                                   # a model's own reasoning from an earlier turn
        else:
            raise Unsupported(f"a {kind!r} content block is not supported here; send text and images")
    return _content(parts)


def anthropic_ask(body: dict) -> dict:
    """POST /v1/messages."""
    _no_tools(body.get("tools"))
    out = []
    if body.get("system"):
        out.append({"role": "system", "content": text_of(_anthropic_blocks(body["system"], "system"))})
    for m in _messages(body.get("messages")):
        if m.get("role") not in ("user", "assistant"):
            raise Unsupported(f'a message\'s "role" must be "user" or "assistant", not {m.get("role")!r}')
        out.append({"role": m["role"], "content": _anthropic_blocks(m.get("content"), m["role"])})
    return _ask(body, body.get("model") or "", out, body.get("stream"),
                {"max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
                 "top_p": body.get("top_p"), "stop": body.get("stop_sequences")})


def anthropic_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    return {"id": _new_id("msg_"), "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}, "bench": bench}


def anthropic_error(status: int, message: str) -> dict:
    return {"type": "error", "error": {"type": ANTHROPIC_ERRORS.get(status, "api_error"), "message": message}}


class AnthropicStream(Stream):
    """message_start, one text block's deltas, message_delta (the bench
    beside its usage), message_stop; `ping` while waiting, as the API does."""

    def start(self) -> bytes:
        message = {"id": _new_id("msg_"), "type": "message", "role": "assistant", "model": self.model,
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": self.input_tokens, "output_tokens": 0}}
        return (sse({"type": "message_start", "message": message}, "message_start")
                + sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      "content_block_start"))

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                   "content_block_delta")

    def end(self, bench=None) -> bytes:
        delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"output_tokens": self.output_tokens()}, **({"bench": bench} if bench is not None else {})}
        return (sse({"type": "content_block_stop", "index": 0}, "content_block_stop")
                + sse(delta, "message_delta") + sse({"type": "message_stop"}, "message_stop"))

    def error(self, status: int, message: str) -> bytes:
        return sse(anthropic_error(status, message), "error")

    def keepalive(self) -> bytes:
        return sse({"type": "ping"}, "ping")


def _anthropic_content(content) -> str | list[dict]:
    if isinstance(content, str):
        return content
    out = []
    for p in parts_of(content):
        if p["type"] == "text":
            out.append({"type": "text", "text": p["text"]})
        elif (data := split_data_url(_image_url(p))):
            out.append({"type": "image", "source": {"type": "base64", "media_type": data[0], "data": data[1]}})
        else:
            out.append({"type": "image", "source": {"type": "url", "url": _image_url(p)}})
    return out


def _turns(messages: list[dict]) -> tuple[str, list[dict]]:
    """(system text, the other turns) of OpenAI messages, for APIs that keep
    the system text apart; refuses tool turns, which have no place there."""
    if any(m.get("role") in ("tool", "function") or m.get("tool_calls") for m in messages):
        raise Unsupported(TOOLS)
    system = "\n\n".join(text_of(m.get("content")) for m in messages if m.get("role") in LEAD)
    return system, [m for m in messages if m.get("role") not in LEAD]


def anthropic_request(messages: list[dict], model: str, options: dict | None = None, stream: bool = False) -> dict:
    """OpenAI messages as an Anthropic Messages request (POST /v1/messages)."""
    options = options or {}
    system, turns = _turns(messages)
    body = {"model": model, "max_tokens": options.get("max_tokens") or ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "assistant" if m.get("role") == "assistant" else "user",
                          "content": _anthropic_content(m.get("content"))} for m in turns]}
    if system:
        body["system"] = system
    for ours, theirs in (("temperature", "temperature"), ("top_p", "top_p"), ("stop", "stop_sequences")):
        if options.get(ours) is not None:
            body[theirs] = options[ours]
    return {**body, **({"stream": True} if stream else {})}


def anthropic_text(reply: dict) -> str:
    """The text of an Anthropic Messages reply; UpstreamError for an error."""
    if reply.get("type") == "error":
        raise UpstreamError(str((reply.get("error") or {}).get("message") or reply))
    return "".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text")


def anthropic_delta(event: dict) -> str:
    """The text one Anthropic stream event adds ("" for the others)."""
    if event.get("type") == "error":
        raise UpstreamError(str((event.get("error") or {}).get("message") or event))
    delta = event.get("delta") or {}
    return delta.get("text", "") if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" else ""


# ---- OpenAI Responses ------------------------------------------------------

def _responses_parts(content, where: str) -> str | list[dict]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise Unsupported(f"{where}: content must be a string or a list of parts")
    parts = []
    for p in content:
        kind = p.get("type") if isinstance(p, dict) else None
        if kind in ("input_text", "output_text", "text", "refusal"):
            parts.append(text_part(p.get("text") if kind != "refusal" else p.get("refusal")))
        elif kind == "input_image":
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if not url:
                raise Unsupported("an input_image must carry an image_url (a file_id names an upload this"
                                  " gateway cannot reach)")
            parts.append(image(str(url)))
        else:
            raise Unsupported(f"a {kind!r} input part is not supported here; send text and images")
    return _content(parts)


def responses_ask(body: dict) -> dict:
    """POST /v1/responses. Nothing is stored, so a previous_response_id (or
    a stored conversation) names nothing here."""
    _no_tools(body.get("tools"))
    if body.get("previous_response_id") or body.get("conversation"):
        raise Unsupported("responses are never stored here, so previous_response_id and conversation name"
                          " nothing; send the whole conversation in input")
    out = []
    if body.get("instructions"):
        out.append({"role": "system", "content": str(body["instructions"])})
    given = body.get("input")
    if isinstance(given, str):
        out.append({"role": "user", "content": given})
    elif isinstance(given, list):
        for item in given:
            if not isinstance(item, dict):
                raise Unsupported('"input" must be a string or a list of items')
            kind = item.get("type") or "message"
            if kind == "reasoning":
                continue                               # a model's own reasoning from an earlier turn
            if kind != "message":
                raise Unsupported(TOOLS if "call" in kind or "tool" in kind else
                                  f"an input item of type {kind!r} is not supported here; send messages")
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                raise Unsupported(f"an input message's role must be user, assistant, system or developer, not {role!r}")
            out.append({"role": role, "content": _responses_parts(item.get("content"), role)})
    else:
        raise Unsupported('"input" must be a string or a list of items')
    return _ask(body, body.get("model") or "", out, body.get("stream"),
                {"max_tokens": body.get("max_output_tokens"), "temperature": body.get("temperature"),
                 "top_p": body.get("top_p")})


def _response(rid: str, model: str, status: str, output: list, usage=(0, 0), created: int = 0, **extra) -> dict:
    return {"id": rid, "object": "response", "created_at": created or _now(), "status": status,
            "error": None, "incomplete_details": None, "instructions": None, "max_output_tokens": None,
            "model": model, "output": output, "parallel_tool_calls": False, "previous_response_id": None,
            "reasoning": {"effort": None, "summary": None}, "store": False, "temperature": None,
            "text": {"format": {"type": "text"}}, "tool_choice": "none", "tools": [], "top_p": None,
            "truncation": "disabled", "metadata": {},
            "usage": {"input_tokens": usage[0], "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens": usage[1], "output_tokens_details": {"reasoning_tokens": 0},
                      "total_tokens": sum(usage)}, **extra}


def _message_item(mid: str, text: str, status: str = "completed") -> dict:
    return {"type": "message", "id": mid, "status": status, "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}] if status == "completed" else []}


def responses_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    return _response(_new_id("resp_"), model, "completed", [_message_item(_new_id("msg_"), text)], usage,
                     bench=bench)


def responses_error(status: int, message: str) -> dict:
    return {"error": {"message": message, "type": "invalid_request_error" if status < 500 else "server_error",
                      "param": None, "code": str(status)}}


class ResponsesStream(Stream):
    """The Responses API's typed events, numbered; response.completed
    carries the whole response, the bench in it."""

    def __init__(self, model: str, input_tokens: int = 0, **kw):
        super().__init__(model, input_tokens, **kw)
        self.rid, self.mid, self.created, self.seq = _new_id("resp_"), _new_id("msg_"), _now(), 0

    def _event(self, kind: str, **data) -> bytes:
        out = sse({"type": kind, "sequence_number": self.seq, **data}, kind)
        self.seq += 1
        return out

    def _where(self) -> dict:
        return {"item_id": self.mid, "output_index": 0, "content_index": 0}

    def start(self) -> bytes:
        pending = _response(self.rid, self.model, "in_progress", [], created=self.created)
        part = {"type": "output_text", "text": "", "annotations": []}
        return (self._event("response.created", response=pending)
                + self._event("response.in_progress", response=pending)
                + self._event("response.output_item.added", output_index=0,
                              item=_message_item(self.mid, "", "in_progress"))
                + self._event("response.content_part.added", **self._where(), part=part))

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return self._event("response.output_text.delta", **self._where(), delta=text, logprobs=[])

    def end(self, bench=None) -> bytes:
        text = "".join(self.said)
        item = _message_item(self.mid, text)
        done = _response(self.rid, self.model, "completed", [item], (self.input_tokens, self.output_tokens()),
                         self.created, **({"bench": bench} if bench is not None else {}))
        return (self._event("response.output_text.done", **self._where(), text=text, logprobs=[])
                + self._event("response.content_part.done", **self._where(), part=item["content"][0])
                + self._event("response.output_item.done", output_index=0, item=item)
                + self._event("response.completed", response=done))

    def error(self, status: int, message: str) -> bytes:
        failed = _response(self.rid, self.model, "failed", [], created=self.created)
        failed["error"] = {"code": "server_error" if status >= 500 else "invalid_request", "message": message}
        return (self._event("error", code=str(status), message=message, param=None)
                + self._event("response.failed", response=failed))


def responses_request(messages: list[dict], model: str, options: dict | None = None, stream: bool = False) -> dict:
    """OpenAI messages as a Responses request (POST /v1/responses), never stored."""
    options = options or {}
    system, turns = _turns(messages)
    items = []
    for m in turns:
        mine = m.get("role") == "assistant"
        items.append({"role": "assistant" if mine else "user", "content": [
            {"type": "output_text" if mine else "input_text", "text": p["text"]} if p["type"] == "text"
            else {"type": "input_image", "image_url": _image_url(p)} for p in parts_of(m.get("content"))]})
    body = {"model": model, "input": items, "store": False}
    if system:
        body["instructions"] = system
    for ours, theirs in (("max_tokens", "max_output_tokens"), ("temperature", "temperature"), ("top_p", "top_p")):
        if options.get(ours) is not None:
            body[theirs] = options[ours]
    return {**body, **({"stream": True} if stream else {})}


def responses_text(reply: dict) -> str:
    if reply.get("error") and reply.get("status") != "completed":
        raise UpstreamError(str(reply["error"].get("message") if isinstance(reply["error"], dict) else reply["error"]))
    return "".join(c.get("text", "") for item in reply.get("output") or [] if item.get("type") == "message"
                   for c in item.get("content") or [] if c.get("type") == "output_text")


def responses_delta(event: dict) -> str:
    kind = event.get("type")
    if kind == "error":
        raise UpstreamError(str(event.get("message") or event))
    if kind == "response.failed":
        raise UpstreamError(str(((event.get("response") or {}).get("error") or {}).get("message") or event))
    return event.get("delta", "") if kind == "response.output_text.delta" else ""


# ---- Ollama ----------------------------------------------------------------

def ollama_model(model) -> str:
    """Ollama names carry a tag, `:latest` when none is given; a collection's
    tag is its reply mode (bench/core:merged), and `latest` is no mode."""
    return model.removesuffix(":latest") if isinstance(model, str) else model


def _ollama_parts(text, pictures, where: str) -> str | list[dict]:
    if pictures is not None and not (isinstance(pictures, list) and all(isinstance(p, str) for p in pictures)):
        raise Unsupported(f"{where}: images must be a list of base64 strings")
    parts = [text_part(text)] if text else []
    parts += [image(data_url(sniff(p), p)) for p in pictures or []]
    return _content(parts) if parts else ""


def _ollama_options(body: dict) -> dict:
    o = body.get("options") if isinstance(body.get("options"), dict) else {}
    return {"max_tokens": o.get("num_predict"), "temperature": o.get("temperature"), "top_p": o.get("top_p"),
            "stop": o.get("stop")}


def ollama_chat_ask(body: dict) -> dict:
    """POST /api/chat. Ollama streams unless told not to."""
    _no_tools(body.get("tools"))
    out = []
    for m in _messages(body.get("messages")):
        if m.get("role") == "tool" or m.get("tool_calls"):
            raise Unsupported(TOOLS)
        if m.get("role") not in ("system", "user", "assistant"):
            raise Unsupported(f'a message\'s "role" must be system, user or assistant, not {m.get("role")!r}')
        out.append({"role": m["role"], "content": _ollama_parts(m.get("content"), m.get("images"), m["role"])})
    return _ask(body, ollama_model(body.get("model") or ""), out, body.get("stream", True), _ollama_options(body))


def ollama_generate_ask(body: dict) -> dict:
    """POST /api/generate: a prompt and an optional system text."""
    if not isinstance(body.get("prompt", ""), str):
        raise Unsupported('"prompt" must be a string')
    if body.get("context"):
        raise Unsupported('"context" carries a model\'s own token state, which a panel cannot take up;'
                          " send the conversation to /api/chat instead")
    out = [{"role": "system", "content": str(body["system"])}] if body.get("system") else []
    out.append({"role": "user", "content": _ollama_parts(body.get("prompt"), body.get("images"), "prompt")})
    return _ask(body, ollama_model(body.get("model") or ""), out, body.get("stream", True), _ollama_options(body))


def _ollama_at() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ollama_done(usage, seconds: float) -> dict:
    # Real durations, in nanoseconds: clients divide by them for tokens a second.
    ns = max(1, int(seconds * 1e9))
    return {"done": True, "done_reason": "stop", "total_duration": ns, "load_duration": 0,
            "prompt_eval_count": usage[0], "prompt_eval_duration": 0, "eval_count": usage[1], "eval_duration": ns}


def ollama_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, generate: bool = False, **_) -> dict:
    said = {"response": text} if generate else {"message": {"role": "assistant", "content": text}}
    return {"model": model, "created_at": _ollama_at(), **said, **_ollama_done(usage, seconds), "bench": bench}


def ollama_generate_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    return ollama_reply(text, model, bench, usage, seconds, generate=True)


def ollama_error(status: int, message: str) -> dict:
    return {"error": message}


class OllamaStream(Stream):
    """One JSON object a line; the last says done, the bench in it. While
    waiting, a line that adds nothing: NDJSON has no comments."""
    content_type = "application/x-ndjson"
    generate = False

    def _line(self, text: str, **extra) -> bytes:
        said = {"response": text} if self.generate else {"message": {"role": "assistant", "content": text}}
        return (json.dumps({"model": self.model, "created_at": _ollama_at(), **said, "done": False, **extra},
                           ensure_ascii=False) + "\n").encode()

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return self._line(text)

    def end(self, bench=None) -> bytes:
        done = _ollama_done((self.input_tokens, self.output_tokens()), time.monotonic() - self.started)
        return self._line("", **done, **({"bench": bench} if bench is not None else {}))

    def error(self, status: int, message: str) -> bytes:
        return (json.dumps({"error": message}, ensure_ascii=False) + "\n").encode()

    def keepalive(self) -> bytes:
        return self._line("")


class OllamaGenerateStream(OllamaStream):
    generate = True


def ollama_tags(models: list[tuple[str, dict]]) -> dict:
    """GET /api/tags: each (name, description) as an Ollama model."""
    at = _ollama_at()
    return {"models": [{"name": n, "model": n, "modified_at": at, "size": 0,
                        "digest": hashlib.sha256(n.encode()).hexdigest(), "details": _ollama_details(d)}
                       for n, d in models]}


def _ollama_details(described: dict) -> dict:
    return {"parent_model": "", "format": "", "family": "benchwarmers", "families": ["benchwarmers"],
            "parameter_size": "", "quantization_level": ""}


def ollama_show(name: str, described: dict) -> dict:
    """POST /api/show: what a client reads to decide what to send (context
    length, whether it takes images)."""
    together = described.get("together") or {}
    caps = together.get("capabilities") or []
    info = {"general.architecture": "benchwarmers", "general.basename": name}
    if together.get("context_window"):
        info["benchwarmers.context_length"] = together["context_window"]
    return {"modelfile": f"# {described.get('description') or name}", "parameters": "", "template": "{{ .Prompt }}",
            "details": _ollama_details(described), "model_info": info, "modified_at": _ollama_at(),
            "capabilities": ["completion"] + (["vision"] if "vision" in caps else [])}


def ollama_request(messages: list[dict], model: str, options: dict | None = None, stream: bool = False) -> dict:
    """OpenAI messages as an Ollama /api/chat request. Ollama takes images
    only as base64, so an http(s) image is refused, not fetched here."""
    out = []
    for m in messages:
        if m.get("role") in ("tool", "function") or m.get("tool_calls"):
            raise Unsupported(TOOLS)
        parts = parts_of(m.get("content"))
        pictures = []
        for p in parts:
            if p["type"] == "image_url":
                data = split_data_url(_image_url(p))
                if not data:
                    raise Unsupported("Ollama takes images as base64 only; send a data: URL")
                pictures.append(data[1])
        role = "system" if m.get("role") in LEAD else m.get("role", "user")
        out.append({"role": role, "content": "\n\n".join(p["text"] for p in parts if p["type"] == "text"),
                    **({"images": pictures} if pictures else {})})
    opts = {theirs: options[ours] for ours, theirs in (("max_tokens", "num_predict"), ("temperature", "temperature"),
                                                       ("top_p", "top_p"), ("stop", "stop"))
            if options and options.get(ours) is not None}
    return {"model": model, "messages": out, "stream": stream, **({"options": opts} if opts else {})}


def ollama_text(reply: dict) -> str:
    if reply.get("error"):
        raise UpstreamError(str(reply["error"]))
    return reply["response"] if "response" in reply else str((reply.get("message") or {}).get("content") or "")


ollama_delta = ollama_text                             # each NDJSON line is a reply's shape, with less in it


# ---- Gemini ----------------------------------------------------------------

def _get(d: dict, camel: str, snake: str):
    """A field by either spelling: Gemini's REST API takes both."""
    return d.get(camel, d.get(snake)) if isinstance(d, dict) else None


def _gemini_parts(parts, where: str) -> str | list[dict]:
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        raise Unsupported(f"{where}: parts must be a list")
    out = []
    for p in parts:
        if not isinstance(p, dict):
            raise Unsupported(f"{where}: each part must be an object")
        if p.get("thought"):
            continue                                   # a model's own reasoning from an earlier turn
        if "text" in p:
            out.append(text_part(p["text"]))
        elif (blob := _get(p, "inlineData", "inline_data")) is not None:
            mime = _image_mime(_get(blob, "mimeType", "mime_type"), "an inlineData part")
            out.append(image(data_url(mime, str(blob.get("data") or ""))))
        elif (ref := _get(p, "fileData", "file_data")) is not None:
            uri = str(_get(ref, "fileUri", "file_uri") or "")
            _image_mime(_get(ref, "mimeType", "mime_type") or mimetypes.guess_type(uri)[0], "a fileData part")
            if not uri.lower().startswith(("https://", "http://")):
                raise Unsupported("a fileData part must name an image at an http(s) URL; an upload to"
                                  " Google's Files API cannot be read by other models")
            out.append(image(uri))
        elif any(k in p for k in ("functionCall", "function_call", "functionResponse", "function_response",
                                  "executableCode", "executable_code", "codeExecutionResult",
                                  "code_execution_result")):
            raise Unsupported(TOOLS)
        else:
            raise Unsupported(f"{where}: a part with {', '.join(sorted(p)) or 'nothing'} is not supported here;"
                              " send text and images")
    return _content(out)


def gemini_ask(body: dict, model: str, stream: bool = False) -> dict:
    """POST /v1beta/models/{model}:generateContent (or :streamGenerateContent):
    the model is in the path, and so is whether it streams."""
    _no_tools(body.get("tools"))
    contents = body.get("contents")
    if isinstance(contents, dict):
        contents = [contents]
    if not (isinstance(contents, list) and all(isinstance(c, dict) for c in contents)):
        raise Unsupported('"contents" must be a list of content objects')
    out = []
    if (system := _get(body, "systemInstruction", "system_instruction")):
        out.append({"role": "system", "content": text_of(_gemini_parts(
            system.get("parts") if isinstance(system, dict) else [{"text": str(system)}], "systemInstruction"))})
    for c in contents:
        role = c.get("role") or "user"
        if role == "function":
            raise Unsupported(TOOLS)
        if role not in ("user", "model"):
            raise Unsupported(f'a content\'s "role" must be "user" or "model", not {role!r}')
        out.append({"role": "assistant" if role == "model" else "user", "content": _gemini_parts(c.get("parts"), role)})
    config = _get(body, "generationConfig", "generation_config") or {}
    return _ask(body, model, out, stream,
                {"max_tokens": _get(config, "maxOutputTokens", "max_output_tokens"),
                 "temperature": config.get("temperature"), "top_p": _get(config, "topP", "top_p"),
                 "stop": _get(config, "stopSequences", "stop_sequences")})


def _gemini_chunk(text: str, model: str, rid: str, usage=None, finish: bool = False) -> dict:
    candidate = {"content": {"role": "model", "parts": [{"text": text}]}, "index": 0,
                 **({"finishReason": "STOP"} if finish else {})}
    out = {"candidates": [candidate], "modelVersion": model, "responseId": rid}
    if usage is not None:
        out["usageMetadata"] = {"promptTokenCount": usage[0], "candidatesTokenCount": usage[1],
                                "totalTokenCount": sum(usage)}
    return out


def gemini_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    """No bench field: Google's SDK refuses fields it does not know, so the
    gateway sends the panel's call ids in headers instead (bench_headers)."""
    return _gemini_chunk(text, model, _new_id(""), usage, finish=True)


def gemini_error(status: int, message: str) -> dict:
    return {"error": {"code": status, "message": message, "status": GEMINI_STATUS.get(status, "INTERNAL")}}


class GeminiStream(Stream):
    """GenerateContentResponse chunks: as server-sent events with ?alt=sse
    (what Google's SDKs ask for), else one JSON array sent as it grows. A
    keep-alive is a chunk with no text, which every client takes in stride:
    the SDKs' readers take any line that is not `data:` for an error."""

    def __init__(self, model: str, input_tokens: int = 0, sse: bool = True, **kw):
        super().__init__(model, input_tokens, **kw)
        self.sse, self.rid, self.first = sse, _new_id(""), True
        if not sse:
            self.content_type = "application/json"

    def _send(self, data: dict, last: bool = False) -> bytes:
        if self.sse:
            return sse(data)
        lead, self.first = ("[" if self.first else ",\r\n"), False
        return (lead + json.dumps(data, ensure_ascii=False) + ("]" if last else "")).encode()

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return self._send(_gemini_chunk(text, self.model, self.rid))

    def end(self, bench=None) -> bytes:
        return self._send(_gemini_chunk("", self.model, self.rid, (self.input_tokens, self.output_tokens()), True),
                          last=True)

    def error(self, status: int, message: str) -> bytes:
        return self._send(gemini_error(status, message), last=True)

    def keepalive(self) -> bytes:
        return self._send(_gemini_chunk("", self.model, self.rid))


def gemini_models(models: list[tuple[str, dict]]) -> dict:
    """GET /v1beta/models: each (name, description) as a Gemini model."""
    return {"models": [gemini_model(n, d) for n, d in models]}


def gemini_model(name: str, described: dict) -> dict:
    together = described.get("together") or {}
    return {"name": f"models/{name}", "baseModelId": name, "version": "1", "displayName": name,
            "description": described.get("description") or "",
            **({"inputTokenLimit": together["context_window"]} if together.get("context_window") else {}),
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"]}


def gemini_route(path: str) -> tuple[str, str] | None:
    """(model, method) of a Gemini POST path, `/v1beta/models/bench/core:generateContent`
    -> ("bench/core", "generateContent"); the model may be URL-encoded. None if it is not one."""
    for root in ("/v1beta/models/", "/v1/models/"):
        if path.startswith(root):
            model, sep, method = urllib.parse.unquote(path[len(root):]).rpartition(":")
            if sep and model:
                return model, method
    return None


def gemini_request(messages: list[dict], options: dict | None = None) -> dict:
    """OpenAI messages as a Gemini generateContent body (the model goes in the
    path: gemini_path)."""
    options = options or {}
    system, turns = _turns(messages)
    contents = []
    for m in turns:
        parts = []
        for p in parts_of(m.get("content")):
            if p["type"] == "text":
                parts.append({"text": p["text"]})
            elif (data := split_data_url(_image_url(p))):
                parts.append({"inlineData": {"mimeType": data[0], "data": data[1]}})
            else:
                url = _image_url(p)
                parts.append({"fileData": {"mimeType": mimetypes.guess_type(urllib.parse.urlsplit(url).path)[0]
                                           or "image/jpeg", "fileUri": url}})
        contents.append({"role": "model" if m.get("role") == "assistant" else "user", "parts": parts})
    body = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    config = {theirs: options[ours] for ours, theirs in (("max_tokens", "maxOutputTokens"), ("temperature", "temperature"),
                                                         ("top_p", "topP"), ("stop", "stopSequences"))
              if options.get(ours) is not None}
    return {**body, **({"generationConfig": config} if config else {})}


def gemini_path(model: str, stream: bool = False) -> str:
    """The path a generateContent call goes to, under the API's base URL."""
    return f"/v1beta/models/{model}:" + ("streamGenerateContent?alt=sse" if stream else "generateContent")


def gemini_text(reply: dict) -> str:
    if reply.get("error"):
        raise UpstreamError(str(reply["error"].get("message") if isinstance(reply["error"], dict) else reply["error"]))
    candidates = reply.get("candidates") or [{}]
    return "".join(p.get("text", "") for p in ((candidates[0].get("content") or {}).get("parts") or [])
                   if not p.get("thought"))


gemini_delta = gemini_text                             # each streamed chunk is a reply's shape


# ---- the gateway's table ---------------------------------------------------

class Format(NamedTuple):
    """One inbound format: how a request is read, an answer written, an
    answer streamed and a refusal shaped."""
    ask: Callable[..., dict]
    reply: Callable[..., dict]
    stream: type
    error: Callable[[int, str], dict]


INBOUND = {
    "chat": Format(chat_ask, chat_reply, ChatStream, chat_error),
    "completion": Format(completion_ask, completion_reply, CompletionStream, chat_error),
    "anthropic": Format(anthropic_ask, anthropic_reply, AnthropicStream, anthropic_error),
    "responses": Format(responses_ask, responses_reply, ResponsesStream, responses_error),
    "ollama_chat": Format(ollama_chat_ask, ollama_reply, OllamaStream, ollama_error),
    "ollama_generate": Format(ollama_generate_ask, ollama_generate_reply, OllamaGenerateStream, ollama_error),
    "gemini": Format(gemini_ask, gemini_reply, GeminiStream, gemini_error),
}

OLLAMA_ROUTES = {"/api/chat": "ollama_chat", "/api/generate": "ollama_generate", "/api/show": "ollama_chat",
                 "/api/tags": "ollama_chat", "/api/version": "ollama_chat"}
POST_ROUTES = {"/v1/chat/completions": "chat", "/chat/completions": "chat", "/v1/completions": "completion",
               "/completions": "completion", "/v1/messages": "anthropic", "/v1/messages/count_tokens": "anthropic",
               "/v1/responses": "responses", "/responses": "responses",
               "/api/chat": "ollama_chat", "/api/generate": "ollama_generate"}


def format_of(path: str) -> str:
    """Which format a route speaks, so even a refusal before the body is read
    (a missing key, a bad Host) comes back in a shape its client reads."""
    if path in OLLAMA_ROUTES:
        return OLLAMA_ROUTES[path]
    if path in POST_ROUTES:
        return POST_ROUTES[path]
    if path.startswith("/v1beta/") or gemini_route(path):
        return "gemini"
    return "chat"
