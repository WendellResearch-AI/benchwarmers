"""Other APIs' shapes, translated to and from the OpenAI chat shape the bench speaks.

    python scripts/test_formats.py      # the round trips, and every route against fake seats

The gateway answers clients that speak OpenAI chat completions, OpenAI's
legacy completions, OpenAI Responses, Anthropic Messages, Ollama and Gemini.
Each of those is translated here into OpenAI-style messages (images as
`image_url` parts with data: or http(s) URLs, so the gateway's vision
filtering sees them), and the panel's answer back into the format's own
response and streaming events. Pure functions, stdlib only, no I/O: the
gateway does the HTTP.

The same shapes run the other way for the built-in client, which calls an
endpoint whose `api` is `anthropic` or `gemini` with them (Ollama's and the
Responses API's outbound halves are not wired; metrics/client.py speaks
chat completions and ChatGPT's Responses flow itself):

    inbound, the gateway answering a client          outbound, the client calling an API
    <fmt>_ask(body)      native request -> Ask       <fmt>_request(messages, ...)  OpenAI messages -> native request
    <fmt>_reply(text...) an answer -> native reply   <fmt>_text(reply)             native reply -> its text
    <Fmt>Stream          an answer as it comes ->    <fmt>_delta(event)            one native stream event -> its text
                         native stream bytes
    <fmt>_error(status, message) -> native error body

An Ask is a dict: `model` (as the client named it), `messages` (OpenAI
style, tool calls and results included), `stream`, `options` (max_tokens,
temperature, top_p, stop, when given), the bench's own `with` and `reply`,
read from any format's body (an SDK's extra_body), `upstream` (the request
as OpenAI chat completions fields beyond messages: tools, tool_choice,
parallel_tool_calls, response_format and the options) and `agentic` (it
carries tools, tool calls or tool results). A panel reads `options` but does
not pass them on: each seat uses its own call options
(config/model-options.json). Route mode passes `upstream` on.

ROUTE MODE (one member, tools and all)
--------------------------------------
`Turn` reads the member's OpenAI chunks; each format's route stream
(ROUTE) writes them in its own shape and route_reply() the whole answer.
OpenAI chat completions relays the chunks as they came, tool-call deltas
exactly. The other formats stream the text as it comes and each tool call
whole once the turn ends, so a call whose deltas interleave with another's
still arrives intact. Tool call ids pass through both ways; Gemini and
Ollama carry none, so they are numbered per request (`_Ids`), and a result
is matched to the oldest open call of its name.

WHAT IS REFUSED, NOT DROPPED
----------------------------
A panel refuses an agentic Ask (the gateway answers 400 with TOOLS): its
reply is several models' text, not a tool call, and an agent that waits for
one would hang or loop. Every format refuses what would be lost on the way:
a PDF or audio part, an uploaded file id, a stored previous response, a
tool that only its own vendor runs (Anthropic's web search, OpenAI's
hosted tools, Gemini's Google Search and code execution). A model's own
reasoning from an earlier turn (Anthropic thinking blocks, Responses
reasoning items, Gemini thought parts) is dropped: each model reasons for
itself, and none could read another's signed thinking. Structured-output
settings (Ollama `format`, Gemini's responseSchema, Responses `text.format`)
reach route mode as response_format, and are ignored by a panel, which
answers in markdown.

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

TOOLS = ("this request carries tools, tool calls or tool results, and a panel answers with several models'"
         " text, not a tool call. Ask for the collection's route mode instead (bench/<collection>:route, or"
         ' "bench": {"reply": "route"}), which hands the request to one member, tools and all.')
REPLIES = ("all", "merged", "route")   # bench_collections.REPLIES; not imported, so this file stands alone
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


def _function(name, description, parameters) -> dict:
    """One OpenAI tool definition."""
    if not isinstance(name, str) or not name:
        raise Unsupported("every tool needs a name")
    return {"type": "function", "function": {"name": name, "description": str(description or ""),
                                             "parameters": parameters or {"type": "object", "properties": {}}}}


def _call(cid, name, arguments) -> dict:
    """One OpenAI tool call in an assistant message; `arguments` a JSON string or an object."""
    return {"id": str(cid or _new_id("call_")), "type": "function",
            "function": {"name": str(name or ""),
                         "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {})}}


def _assistant(text, calls: list[dict]) -> dict:
    """An assistant message, with its tool calls when it made any."""
    if not calls:
        return {"role": "assistant", "content": text}
    return {"role": "assistant", "content": text_of(text) or None, "tool_calls": calls}


class _Ids:
    """Tool call ids for formats whose calls carry none (Gemini, Ollama):
    numbered in order through one request, and a result given to the oldest
    open call of its name (or the oldest open call, when it names none). The
    model never sees a past turn's ids from its own reply, so any that match
    within the request will do."""

    def __init__(self):
        self.n, self.open = 0, []

    def call(self, name, given=None) -> str:
        cid = str(given or f"call_{self.n}")
        self.n += 1
        self.open.append((name, cid))
        return cid

    def result(self, name=None, given=None) -> str:
        for i, (n, cid) in enumerate(self.open):
            if (given and cid == given) or (not given and (not name or n == name)):
                del self.open[i]
                return cid
        return str(given or f"call_{self.n}")


def _ask(body: dict, model, messages: list[dict], stream, options: dict, upstream: dict | None = None,
         **extra) -> dict:
    """The Ask every format's request becomes, with the bench's own fields
    checked as POST /v1/chat/completions always checked them. `upstream`:
    the request's OpenAI chat fields beyond messages, for route mode; the
    options when not given."""
    if not isinstance(model, str):
        raise Unsupported('"model" must be a collection name such as bench/core')
    nominated, opts = body.get("with") or [], body.get("bench") or {}
    if not (isinstance(nominated, list) and all(isinstance(a, str) for a in nominated)):
        raise Unsupported('"with" must be a list of model aliases')
    if not isinstance(opts, dict) or opts.get("reply", "all") not in REPLIES:
        raise Unsupported('"bench" must be an object whose "reply" is "all", "merged" or "route"')
    stop = options.get("stop")
    if isinstance(stop, str):
        options = {**options, "stop": [stop]}
    options = {k: v for k, v in options.items() if v is not None}
    upstream = {k: v for k, v in (options if upstream is None else upstream).items() if v is not None}
    agentic = bool(upstream.get("tools")) or any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)
    return {"model": model, "messages": messages, "stream": bool(stream), "options": options,
            "with": nominated, "reply": opts.get("reply") or "", "upstream": upstream, "agentic": agentic, **extra}


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
        self.started, self.turn = time.monotonic(), Turn()

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

    def chunk(self, chunk: dict) -> bytes:
        """Route mode: one of the member's OpenAI chunks, its text sent on as
        it comes; the rest (tool calls, usage) is kept for end()."""
        text, _ = self.turn.feed(chunk)
        return self.text(text) if text else b""

    def output_tokens(self) -> int:
        return estimate("".join(self.said))


# ---- OpenAI chat completions -----------------------------------------------

CHAT_OWN = ("model", "messages", "stream", "stream_options", "with", "bench")


def chat_ask(body: dict) -> dict:
    """POST /v1/chat/completions. The messages are already OpenAI's; the
    gateway's own image check reads them as they are, and route mode passes
    every other field on as it came."""
    messages = _messages(body.get("messages"))
    if body.get("functions") or body.get("function_call") or any(
            m.get("role") == "function" or m.get("function_call") for m in messages):
        raise Unsupported('the legacy "functions" fields are not supported; send "tools" and "tool_calls"')
    return _ask(body, body.get("model") or "", messages, body.get("stream"),
                {"max_tokens": body.get("max_completion_tokens") or body.get("max_tokens"),
                 "temperature": body.get("temperature"), "top_p": body.get("top_p"), "stop": body.get("stop")},
                upstream={k: v for k, v in body.items() if k not in CHAT_OWN})


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
            # server_tool_use, web_search_tool_result, mcp_tool_use...: what
            # Anthropic's own servers ran, which no other model can continue.
            raise Unsupported(f"a {kind!r} block comes from a tool Anthropic runs on its own servers, which"
                              " no other model can take up; send your own tools' tool_use and tool_result")
        elif kind in ("thinking", "redacted_thinking"):
            continue                                   # a model's own reasoning from an earlier turn
        else:
            raise Unsupported(f"a {kind!r} content block is not supported here; send text and images")
    return _content(parts)


def _anthropic_message(m: dict) -> list[dict]:
    """One Anthropic message as OpenAI messages: an assistant's tool_use
    blocks become its tool_calls; a user's tool_result blocks become tool
    messages, ahead of whatever else it said (OpenAI wants each result right
    after the call). An image in a result goes in that user message after
    them, since a tool message carries text only; is_error marks the result."""
    role, content = m["role"], m.get("content")
    tool = [b for b in content if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result")] \
        if isinstance(content, list) else []
    if not tool:
        return [{"role": role, "content": _anthropic_blocks(content, role)}]
    rest = [b for b in content if not (isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result"))]
    said = _anthropic_blocks(rest, role) if rest else ""
    wrong = "tool_result" if role == "assistant" else "tool_use"
    if any(b["type"] == wrong for b in tool):
        raise Unsupported(f"a {wrong} block cannot be in a{'n' * (role == 'assistant')} {role} message")
    if role == "assistant":
        return [_assistant(said, [_call(b.get("id"), b.get("name"), b.get("input") or {}) for b in tool])]
    out, pictures = [], []
    for b in tool:
        parts = parts_of(_anthropic_blocks(b.get("content") or "", "a tool_result"))
        pictures += [p for p in parts if p["type"] == "image_url"]
        text = "\n\n".join(p["text"] for p in parts if p["type"] == "text")
        out.append({"role": "tool", "tool_call_id": str(b.get("tool_use_id") or ""),
                    "content": ("Error: " if b.get("is_error") else "") + text})
    if (more := parts_of(said) + pictures):
        out.append({"role": "user", "content": _content(more)})
    return out


def _anthropic_tools(body: dict) -> dict:
    """Anthropic's tools and tool_choice as OpenAI's."""
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        raise Unsupported('"tools" must be a list')
    out = []
    for t in tools:
        kind = t.get("type") if isinstance(t, dict) else t
        if not isinstance(t, dict) or kind not in (None, "custom"):
            raise Unsupported(f"the {kind!r} tool is one Anthropic runs on its own servers, which no other"
                              " model can; send only your own tools (name, description, input_schema)")
        out.append(_function(t.get("name"), t.get("description"), t.get("input_schema")))
    up = {"tools": out} if out else {}
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and out:
        kind = choice.get("type")
        up["tool_choice"] = ({"type": "function", "function": {"name": choice.get("name")}} if kind == "tool"
                             else {"any": "required", "none": "none"}.get(kind, "auto"))
        if choice.get("disable_parallel_tool_use"):
            up["parallel_tool_calls"] = False
    return up


def anthropic_ask(body: dict) -> dict:
    """POST /v1/messages. Parameters with no OpenAI counterpart (top_k,
    thinking, metadata, service_tier) are not passed on."""
    out = []
    if body.get("system"):
        out.append({"role": "system", "content": text_of(_anthropic_blocks(body["system"], "system"))})
    for m in _messages(body.get("messages")):
        if m.get("role") not in ("user", "assistant"):
            raise Unsupported(f'a message\'s "role" must be "user" or "assistant", not {m.get("role")!r}')
        out += _anthropic_message(m)
    options = {"max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
               "top_p": body.get("top_p"), "stop": body.get("stop_sequences")}
    return _ask(body, body.get("model") or "", out, body.get("stream"), options,
                {**options, **_anthropic_tools(body)})


def anthropic_reply(text: str, model: str, bench=None, usage=(0, 0), seconds: float = 0, **_) -> dict:
    return {"id": _new_id("msg_"), "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}, "bench": bench}


def anthropic_error(status: int, message: str) -> dict:
    return {"type": "error", "error": {"type": ANTHROPIC_ERRORS.get(status, "api_error"), "message": message}}


class AnthropicStream(Stream):
    """message_start, one text block's deltas, message_delta (the bench
    beside its usage), message_stop; `ping` while waiting, as the API does."""

    def _head(self) -> bytes:
        message = {"id": _new_id("msg_"), "type": "message", "role": "assistant", "model": self.model,
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": self.input_tokens, "output_tokens": 0}}
        return sse({"type": "message_start", "message": message}, "message_start")

    @staticmethod
    def _block(index: int, block: dict) -> bytes:
        return sse({"type": "content_block_start", "index": index, "content_block": block}, "content_block_start")

    def start(self) -> bytes:
        return self._head() + self._block(0, {"type": "text", "text": ""})

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
    the system text apart. ponytail: refuses tool turns; the built-in client
    sends native APIs a panel seat's text, and route mode proxies chat
    completions only. Translate them when a native endpoint is routed to."""
    if any(m.get("role") in ("tool", "function") or m.get("tool_calls") for m in messages):
        raise Unsupported("tool calls and results are sent to chat completions only, not a native API")
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


def _responses_tools(body: dict) -> tuple[dict, list[str]]:
    """The Responses API's tools, tool_choice, parallel_tool_calls and
    text.format as OpenAI chat fields, and the names of its custom tools.
    A custom tool (free-form input, as Codex's apply_patch) becomes a
    function taking one string, `input`, its grammar in the description."""
    tools, custom, out = body.get("tools") or [], [], []
    if not isinstance(tools, list):
        raise Unsupported('"tools" must be a list')
    for t in tools:
        kind = t.get("type") if isinstance(t, dict) else t
        if kind == "function":
            fn = _function(t.get("name"), t.get("description"), t.get("parameters"))
            if t.get("strict") is not None:
                fn["function"]["strict"] = t["strict"]
            out.append(fn)
        elif kind == "custom":
            fmt = t.get("format") if isinstance(t.get("format"), dict) else {}
            grammar = (f"\n\nThe input must follow this {fmt.get('syntax', '')} grammar:\n{fmt.get('definition', '')}"
                       if fmt.get("type") == "grammar" else "")
            out.append(_function(t.get("name"), f"{t.get('description') or ''}{grammar}",
                                 {"type": "object", "required": ["input"], "properties": {
                                     "input": {"type": "string", "description": "the tool's whole input, as free text"}}}))
            custom.append(t.get("name"))
        else:
            raise Unsupported(f"a {kind!r} tool is one OpenAI runs itself, which no other model can; send"
                              " function or custom tools")
    up: dict = {"tools": out} if out else {}
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        choice = {"type": "function", "function": {"name": choice["name"]}} if choice.get("name") else \
            choice.get("mode") or "auto"
    if choice and out:
        up["tool_choice"] = choice
    if body.get("parallel_tool_calls") is not None and out:
        up["parallel_tool_calls"] = body["parallel_tool_calls"]
    fmt = (body.get("text") or {}).get("format") if isinstance(body.get("text"), dict) else None
    if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        up["response_format"] = {"type": "json_schema", "json_schema": {
            k: fmt[k] for k in ("name", "schema", "strict", "description") if k in fmt}}
    elif isinstance(fmt, dict) and fmt.get("type") == "json_object":
        up["response_format"] = {"type": "json_object"}
    return up, custom


def responses_ask(body: dict) -> dict:
    """POST /v1/responses. Nothing is stored, so a previous_response_id (or
    a stored conversation) names nothing here. Function and custom tool calls
    and their outputs become OpenAI tool calls and tool messages."""
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
            if kind in ("function_call", "custom_tool_call"):
                call = _call(item.get("call_id") or item.get("id"), item.get("name"),
                             item.get("arguments") or "{}" if kind == "function_call"
                             else {"input": item.get("input") or ""})
                if out and out[-1]["role"] == "assistant":         # the same turn's text, or its other calls
                    out[-1]["tool_calls"] = out[-1].get("tool_calls", []) + [call]
                    out[-1]["content"] = text_of(out[-1]["content"]) or None
                else:
                    out.append(_assistant("", [call]))
                continue
            if kind in ("function_call_output", "custom_tool_call_output"):
                output = item.get("output")
                out.append({"role": "tool", "tool_call_id": str(item.get("call_id") or ""),
                            "content": output if isinstance(output, str) else
                            text_of(_responses_parts(output or "", "a tool output"))})
                continue
            if kind != "message":
                raise Unsupported(f"an input item of type {kind!r} is not supported here; send messages,"
                                  " function calls and their outputs")
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                raise Unsupported(f"an input message's role must be user, assistant, system or developer, not {role!r}")
            out.append({"role": role, "content": _responses_parts(item.get("content"), role)})
    else:
        raise Unsupported('"input" must be a string or a list of items')
    options = {"max_tokens": body.get("max_output_tokens"), "temperature": body.get("temperature"),
               "top_p": body.get("top_p")}
    tools, custom = _responses_tools(body)
    return _ask(body, body.get("model") or "", out, body.get("stream"), options, {**options, **tools},
                custom=custom)


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

    def _head(self) -> bytes:
        pending = _response(self.rid, self.model, "in_progress", [], created=self.created)
        return (self._event("response.created", response=pending)
                + self._event("response.in_progress", response=pending))

    def _open(self) -> bytes:
        part = {"type": "output_text", "text": "", "annotations": []}
        return (self._event("response.output_item.added", output_index=0,
                            item=_message_item(self.mid, "", "in_progress"))
                + self._event("response.content_part.added", **self._where(), part=part))

    def _close(self) -> tuple[bytes, dict]:
        text = "".join(self.said)
        item = _message_item(self.mid, text)
        return (self._event("response.output_text.done", **self._where(), text=text, logprobs=[])
                + self._event("response.content_part.done", **self._where(), part=item["content"][0])
                + self._event("response.output_item.done", output_index=0, item=item)), item

    def start(self) -> bytes:
        return self._head() + self._open()

    def text(self, text: str) -> bytes:
        self.said.append(text)
        return self._event("response.output_text.delta", **self._where(), delta=text, logprobs=[])

    def end(self, bench=None) -> bytes:
        out, item = self._close()
        done = _response(self.rid, self.model, "completed", [item], (self.input_tokens, self.output_tokens()),
                         self.created, **({"bench": bench} if bench is not None else {}))
        return out + self._event("response.completed", response=done)

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
    """POST /api/chat. Ollama streams unless told not to. Its tools are
    already OpenAI's shape; its tool calls carry arguments as an object and
    usually no id (numbered here, _Ids); `format` becomes response_format."""
    out, ids = [], _Ids()
    for m in _messages(body.get("messages")):
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": ids.result(m.get("tool_name") or m.get("name"),
                                                                   m.get("tool_call_id")),
                        "content": str(m.get("content") or "")})
            continue
        if role not in ("system", "user", "assistant"):
            raise Unsupported(f'a message\'s "role" must be system, user, assistant or tool, not {role!r}')
        content = _ollama_parts(m.get("content"), m.get("images"), role)
        calls = m.get("tool_calls") or []
        if calls and (role != "assistant" or not isinstance(calls, list)):
            raise Unsupported("tool_calls must be a list, in an assistant message")
        out.append(_assistant(content, [_call(ids.call(fn.get("name"), tc.get("id")), fn.get("name"), fn.get("arguments"))
                                        for tc in calls if isinstance(tc, dict)
                                        for fn in [tc.get("function") or {}]]) if calls else
                   {"role": role, "content": content})
    options = _ollama_options(body)
    up = dict(options)
    if (tools := body.get("tools")):
        if not (isinstance(tools, list) and all(isinstance(t, dict) and t.get("type") == "function" for t in tools)):
            raise Unsupported('"tools" must be a list of {"type": "function", "function": {...}} objects')
        up["tools"] = tools
    fmt = body.get("format")
    if fmt == "json":
        up["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        up["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
    return _ask(body, ollama_model(body.get("model") or ""), out, body.get("stream", True), options, up)


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
            raise Unsupported("tool calls and results are sent to chat completions only, not a native API")
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
        elif any(k in p for k in ("executableCode", "executable_code", "codeExecutionResult", "code_execution_result")):
            raise Unsupported("a code execution part is Gemini's own tool, which no other model runs")
        elif any(k in p for k in ("functionCall", "function_call", "functionResponse", "function_response")):
            raise Unsupported(f"{where}: a function call or response cannot be here")
        else:
            raise Unsupported(f"{where}: a part with {', '.join(sorted(p)) or 'nothing'} is not supported here;"
                              " send text and images")
    return _content(out)


def _gemini_schema(schema):
    """Gemini's OpenAPI-style schema as JSON Schema: its types are upper case
    (OBJECT, STRING), JSON Schema's lower."""
    if isinstance(schema, list):
        return [_gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    return {k: v.lower() if k == "type" and isinstance(v, str) else _gemini_schema(v) for k, v in schema.items()}


def _gemini_tools(body: dict) -> dict:
    """Gemini's function declarations, toolConfig and JSON output settings as OpenAI chat fields."""
    out = []
    for t in body.get("tools") or []:
        decls = _get(t, "functionDeclarations", "function_declarations") if isinstance(t, dict) else None
        if decls is None or set(t) - {"functionDeclarations", "function_declarations"}:
            raise Unsupported(f"a tool with {', '.join(sorted(t)) if isinstance(t, dict) else t} is one Gemini runs"
                              " itself (Google Search, code execution, URL context), which no other model can;"
                              " send functionDeclarations")
        for d in decls if isinstance(decls, list) else ():
            out.append(_function(d.get("name"), d.get("description"),
                                 _get(d, "parametersJsonSchema", "parameters_json_schema")
                                 or _gemini_schema(d.get("parameters"))))
    up: dict = {"tools": out} if out else {}
    config = _get(_get(body, "toolConfig", "tool_config") or {}, "functionCallingConfig", "function_calling_config") or {}
    mode, names = str(config.get("mode") or "").upper(), _get(config, "allowedFunctionNames", "allowed_function_names") or []
    if out and mode == "NONE":
        up["tool_choice"] = "none"
    elif out and mode in ("ANY", "VALIDATED"):
        up["tool_choice"] = {"type": "function", "function": {"name": names[0]}} if len(names) == 1 else "required"
    gen = _get(body, "generationConfig", "generation_config") or {}
    if _get(gen, "responseMimeType", "response_mime_type") == "application/json":
        schema = _get(gen, "responseJsonSchema", "response_json_schema") or \
            _gemini_schema(_get(gen, "responseSchema", "response_schema"))
        up["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}} \
            if schema else {"type": "json_object"}
    return up


def gemini_ask(body: dict, model: str, stream: bool = False) -> dict:
    """POST /v1beta/models/{model}:generateContent (or :streamGenerateContent):
    the model is in the path, and so is whether it streams. A model turn's
    functionCall parts become its tool calls, a functionResponse a tool
    message (ids numbered when absent, _Ids)."""
    contents = body.get("contents")
    if isinstance(contents, dict):
        contents = [contents]
    if not (isinstance(contents, list) and all(isinstance(c, dict) for c in contents)):
        raise Unsupported('"contents" must be a list of content objects')
    out, ids = [], _Ids()
    if (system := _get(body, "systemInstruction", "system_instruction")):
        out.append({"role": "system", "content": text_of(_gemini_parts(
            system.get("parts") if isinstance(system, dict) else [{"text": str(system)}], "systemInstruction"))})
    for c in contents:
        role = c.get("role") or "user"
        if role not in ("user", "model", "function"):
            raise Unsupported(f'a content\'s "role" must be "user" or "model", not {role!r}')
        parts = c.get("parts")
        parts = [parts] if isinstance(parts, dict) else parts
        if not isinstance(parts, list):
            _gemini_parts(parts, role)                 # says why
        calls = [fc for p in parts if isinstance(p, dict) and (fc := _get(p, "functionCall", "function_call"))]
        results = [fr for p in parts if isinstance(p, dict) and (fr := _get(p, "functionResponse", "function_response"))]
        rest = [p for p in parts if not (isinstance(p, dict) and any(
            k in p for k in ("functionCall", "function_call", "functionResponse", "function_response")))]
        if (calls and role != "model") or (results and role == "model"):
            raise Unsupported("a functionCall belongs in a model turn, a functionResponse in a user turn")
        if role == "model":
            said = _gemini_parts(rest, role) if rest or not calls else ""
            out.append(_assistant(said, [_call(ids.call(fc.get("name"), fc.get("id")), fc.get("name"), fc.get("args") or {})
                                         for fc in calls]) if calls else {"role": "assistant", "content": said})
            continue
        out += [{"role": "tool", "tool_call_id": ids.result(fr.get("name"), fr.get("id")),
                 "content": json.dumps(fr.get("response") or {}, ensure_ascii=False)} for fr in results]
        if rest or not results:
            out.append({"role": "user", "content": _gemini_parts(rest, role)})
    config = _get(body, "generationConfig", "generation_config") or {}
    options = {"max_tokens": _get(config, "maxOutputTokens", "max_output_tokens"),
               "temperature": config.get("temperature"), "top_p": _get(config, "topP", "top_p"),
               "stop": _get(config, "stopSequences", "stop_sequences")}
    return _ask(body, model, out, stream, options, {**options, **_gemini_tools(body)})


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


# ---- route mode: one member's turn, in each format ---------------------------

ANTHROPIC_STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def _piece(content) -> str:
    """A delta's text: a string, or the parts some providers stream."""
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


class Turn:
    """One assistant turn read from an OpenAI chat-completions stream, chunk
    by chunk (or from the one chunk a whole reply makes): the answer, the
    reasoning, each tool call whole, why it stopped and the usage the
    upstream counted. A call is keyed by its id when it brings a new one,
    else its index, else it continues the last: providers differ in which
    of those they send after the first piece."""

    def __init__(self):
        self.text, self.reasoning, self.calls = [], [], []
        self.finish, self.usage, self.model = "", {}, ""
        self._by_id, self._by_index, self._last = {}, {}, None

    def feed(self, chunk: dict) -> tuple[str, bool]:
        """Add one chunk: (the answer text it adds, whether it adds to the
        answer: text or a tool call). Reasoning alone does not: a model that
        reasons and then says nothing has not answered."""
        if not isinstance(chunk, dict):
            return "", False
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            self.usage = chunk["usage"]
        self.model = chunk.get("model") or self.model
        choices = chunk.get("choices")
        if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
            return "", False
        self.finish = choices[0].get("finish_reason") or self.finish
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        if not isinstance(delta, dict):
            return "", False
        thought, text = _piece(delta.get("reasoning_content") or delta.get("reasoning")), _piece(delta.get("content"))
        self.reasoning.append(thought)
        self.text.append(text)
        called = False
        for d in delta.get("tool_calls") or ():
            if not isinstance(d, dict):
                continue
            cid, idx, fn = d.get("id"), d.get("index"), d.get("function") or {}
            if cid and cid in self._by_id:
                k = self._by_id[cid]
            elif cid or not self.calls or (idx is not None and idx not in self._by_index):
                k = len(self.calls)
                self.calls.append({"id": str(cid or _new_id("call_")), "name": "", "arguments": ""})
                if cid:
                    self._by_id[cid] = k
            else:
                k = self._by_index[idx] if idx is not None else self._last
            if idx is not None:
                self._by_index[idx] = k
            self._last, call, args = k, self.calls[k], fn.get("arguments")
            call["name"] = call["name"] or str(fn.get("name") or "")
            call["arguments"] += args if isinstance(args, str) else json.dumps(args) if args is not None else ""
            called = True
        return text, bool(text or called)

    def answer(self) -> str:
        return "".join(self.text)

    def stop(self) -> str:
        """Why it stopped, in chat completions' words. A turn that called a
        tool says tool_calls whatever the provider said (some say stop)."""
        return "tool_calls" if self.calls else self.finish or "stop"

    def tokens(self, input_tokens: int) -> tuple[int, int]:
        """(in, out): the upstream's counts, else estimates."""
        u = self.usage
        return (u.get("prompt_tokens") or u.get("input_tokens") or input_tokens,
                u.get("completion_tokens") or u.get("output_tokens")
                or estimate(self.answer() + "".join(c["arguments"] for c in self.calls)))


def _arguments(call: dict) -> dict:
    """A call's arguments as an object, for the formats that send one. Text
    that is not a JSON object is kept under "arguments" rather than lost."""
    try:
        value = json.loads(call["arguments"] or "{}")
    except ValueError:
        return {"arguments": call["arguments"]}
    return value if isinstance(value, dict) else {"arguments": value}


def _call_item(call: dict, custom: bool) -> dict:
    """A tool call as a Responses output item."""
    if custom:
        return {"type": "custom_tool_call", "id": _new_id("ctc_"), "call_id": call["id"], "name": call["name"],
                "input": str(_arguments(call).get("input", call["arguments"])), "status": "completed"}
    return {"type": "function_call", "id": _new_id("fc_"), "call_id": call["id"], "name": call["name"],
            "arguments": call["arguments"] or "{}", "status": "completed"}


def _gemini_calls(turn: Turn) -> list[dict]:
    return [{"functionCall": {"id": c["id"], "name": c["name"], "args": _arguments(c)}} for c in turn.calls]


def _ollama_calls(turn: Turn) -> list[dict]:
    return [{"function": {"name": c["name"], "arguments": _arguments(c)}} for c in turn.calls]


def _bench(bench) -> dict:
    return {"bench": bench} if bench is not None else {}


class ChatRoute(ChatStream):
    """The member's own chunks as they came, tool-call deltas exactly as
    sent, under the name asked for; then a closing chunk with no choice
    carrying the bench, as include_usage's last chunk has none."""

    def start(self) -> bytes:
        return b""

    def chunk(self, chunk: dict) -> bytes:
        self.turn.feed(chunk)
        self.id = chunk.get("id") or self.id
        return sse({**chunk, "object": "chat.completion.chunk", "created": chunk.get("created") or self.created,
                    "model": self.model})

    def end(self, bench=None) -> bytes:
        return sse({"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
                    "choices": [], **_bench(bench)}) + b"data: [DONE]\n\n"


class AnthropicRoute(AnthropicStream):
    """message_start; the text as one text block as it comes; each tool call
    as a tool_use block once the turn is done; the stop reason and the
    member's own token counts."""
    opened = False

    def start(self) -> bytes:
        return self._head()

    def text(self, text: str) -> bytes:
        out = b"" if self.opened else self._block(0, {"type": "text", "text": ""})
        self.opened = True
        return out + super().text(text)

    def end(self, bench=None) -> bytes:
        out, i = b"", 0
        if self.opened or not self.turn.calls:         # a turn with nothing to say still has its block
            out += (b"" if self.opened else self._block(0, {"type": "text", "text": ""})) \
                + sse({"type": "content_block_stop", "index": 0}, "content_block_stop")
            i = 1
        for call in self.turn.calls:
            out += (self._block(i, {"type": "tool_use", "id": call["id"], "name": call["name"], "input": {}})
                    + sse({"type": "content_block_delta", "index": i, "delta": {
                        "type": "input_json_delta", "partial_json": json.dumps(_arguments(call))}}, "content_block_delta")
                    + sse({"type": "content_block_stop", "index": i}, "content_block_stop"))
            i += 1
        used = self.turn.tokens(self.input_tokens)
        delta = {"type": "message_delta", "delta": {"stop_reason": ANTHROPIC_STOP.get(self.turn.stop(), "end_turn"),
                                                    "stop_sequence": None},
                 "usage": {"input_tokens": used[0], "output_tokens": used[1]}, **_bench(bench)}
        return out + sse(delta, "message_delta") + sse({"type": "message_stop"}, "message_stop")


class ResponsesRoute(ResponsesStream):
    """The text as one message item as it comes, then each tool call as a
    function_call (or custom_tool_call) item once the turn is done."""
    opened = False

    def __init__(self, model: str, input_tokens: int = 0, custom=(), **kw):
        super().__init__(model, input_tokens, **kw)
        self.custom = set(custom or ())

    def start(self) -> bytes:
        return self._head()

    def text(self, text: str) -> bytes:
        out = b"" if self.opened else self._open()
        self.opened = True
        return out + super().text(text)

    def end(self, bench=None) -> bytes:
        out, items = self._close() if self.opened else (b"", None)
        items = [items] if items else []
        for call in self.turn.calls:
            item, n = _call_item(call, call["name"] in self.custom), len(items)
            pending = {**item, "status": "in_progress", **({"arguments": ""} if item["type"] == "function_call" else {})}
            out += self._event("response.output_item.added", output_index=n, item=pending)
            if item["type"] == "function_call":
                out += (self._event("response.function_call_arguments.delta", item_id=item["id"], output_index=n,
                                    delta=item["arguments"])
                        + self._event("response.function_call_arguments.done", item_id=item["id"], output_index=n,
                                      arguments=item["arguments"]))
            out += self._event("response.output_item.done", output_index=n, item=item)
            items.append(item)
        done = _response(self.rid, self.model, "completed", items, self.turn.tokens(self.input_tokens), self.created,
                         **_bench(bench))
        return out + self._event("response.completed", response=done)


class GeminiRoute(GeminiStream):
    """The text as it comes; the tool calls as functionCall parts, whole, in
    the last chunk (Gemini streams each call whole anyway)."""

    def end(self, bench=None) -> bytes:
        last = _gemini_chunk("", self.model, self.rid, self.turn.tokens(self.input_tokens), True)
        if self.turn.calls:
            last["candidates"][0]["content"]["parts"] = _gemini_calls(self.turn)
        if self.turn.finish == "length":
            last["candidates"][0]["finishReason"] = "MAX_TOKENS"
        return self._send(last, last=True)


class OllamaRoute(OllamaStream):
    """The text as it comes; the tool calls in one line of their own, then
    the done line (Ollama streams each call whole anyway)."""

    def end(self, bench=None) -> bytes:
        out = b""
        if self.turn.calls:
            out = (json.dumps({"model": self.model, "created_at": _ollama_at(), "message": {
                "role": "assistant", "content": "", "tool_calls": _ollama_calls(self.turn)}, "done": False},
                ensure_ascii=False) + "\n").encode()
        done = _ollama_done(self.turn.tokens(self.input_tokens), time.monotonic() - self.started)
        if self.turn.finish == "length":
            done["done_reason"] = "length"
        return out + self._line("", **done, **_bench(bench))


def route_reply(fmt: str, turn: Turn, model: str, bench: dict, input_tokens: int, seconds: float,
                custom=()) -> dict:
    """A whole route-mode turn as the format's reply: the text reply, with
    the tool calls, stop reason and the member's own token counts put in."""
    used, calls, said = turn.tokens(input_tokens), turn.calls, turn.answer()
    reply = INBOUND[fmt].reply(said, model, bench, used, seconds)
    if fmt == "chat":
        choice = reply["choices"][0]
        choice["finish_reason"] = turn.stop()
        if calls:
            choice["message"].update(content=said or None, tool_calls=[
                _call(c["id"], c["name"], c["arguments"] or "{}") for c in calls])
        if (thought := "".join(turn.reasoning)):
            choice["message"]["reasoning_content"] = thought
        reply["usage"] = turn.usage or {"prompt_tokens": used[0], "completion_tokens": used[1], "total_tokens": sum(used)}
    elif fmt == "anthropic":
        reply["content"] = ([{"type": "text", "text": said}] if said or not calls else []) + [
            {"type": "tool_use", "id": c["id"], "name": c["name"], "input": _arguments(c)} for c in calls]
        reply["stop_reason"] = ANTHROPIC_STOP.get(turn.stop(), "end_turn")
    elif fmt == "responses":
        reply["output"] = (reply["output"] if said or not calls else []) + [
            _call_item(c, c["name"] in set(custom or ())) for c in calls]
    elif fmt == "gemini":
        candidate = reply["candidates"][0]
        if calls:
            candidate["content"]["parts"] = ([{"text": said}] if said else []) + _gemini_calls(turn)
        if turn.finish == "length":
            candidate["finishReason"] = "MAX_TOKENS"
    elif fmt == "ollama_chat" and calls:
        reply["message"]["tool_calls"] = _ollama_calls(turn)
    if fmt.startswith("ollama") and turn.finish == "length":
        reply["done_reason"] = "length"
    return reply


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

# Each format's stream in route mode; completions and Ollama's generate carry no tools.
ROUTE = {"chat": ChatRoute, "completion": CompletionStream, "anthropic": AnthropicRoute,
         "responses": ResponsesRoute, "ollama_chat": OllamaRoute, "ollama_generate": OllamaGenerateStream,
         "gemini": GeminiRoute}

OLLAMA_ROUTES = {"/api/chat":"ollama_chat", "/api/generate": "ollama_generate", "/api/show": "ollama_chat",
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
