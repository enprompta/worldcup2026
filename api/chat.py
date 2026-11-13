"""Vercel serverless function for the World Cup chat endpoint (POST /api/chat).

Streams the answer as Server-Sent Events, keeping the API key server-side.
Set ANTHROPIC_API_KEY in the Vercel project's environment variables.

Self-contained (no cross-file imports) so it bundles cleanly on Vercel. The
config here mirrors worldcup.py, which is the source of truth for local use.

Scope note: to stay self-contained on Vercel this endpoint intentionally uses
only the built-in web_search tool. The client-side tools in worldcup_tools.py
(standings, fixtures, qualification, knowledge base, ...) power the CLI, the
local server, and the course notebook. If we later want them here too, vendor
worldcup_tools into the bundle rather than adding a cross-file import.

Note: on Vercel's Python runtime the SSE frames are buffered and delivered when
the function returns, so the browser renders the full answer at once rather than
token-by-token. The local server (server.py) streams live.

This endpoint is public and unauthenticated, and every call spends money on the
account behind ANTHROPIC_API_KEY - output tokens plus up to MAX_SEARCHES billed
web searches per turn. See the "Abuse controls" block below for what limits that
exposure, and what those limits do not cover.
"""

import json
import os
import sys
import time
from collections import deque
from http.server import BaseHTTPRequestHandler

import anthropic

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "You are a friendly, knowledgeable guide to the 2026 FIFA World Cup, "
    "co-hosted by the United States, Canada, and Mexico (June 11 - July 19, 2026). "
    "It is the first 48-team World Cup. Answer questions about matches, scores, "
    "the schedule, groups, venues, teams, players, and records.\n\n"
    "Use the web_search tool whenever a question depends on live or recent "
    "information (results, standings, upcoming fixtures, injuries). Cite the key "
    "facts you find. If something has not happened yet, say so plainly rather "
    "than guessing. If a question is not about the 2026 World Cup, briefly say "
    "that's outside your focus and offer to help with the tournament instead. "
    "Keep answers concise and lead with the direct answer. Do not use emojis."
)

MAX_SEARCHES = 5
TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}]

client = anthropic.Anthropic()


# --------------------------------------------------------------------------- #
# Abuse controls
#
# Without these the endpoint is an open, unmetered proxy to a paid API: anyone
# who finds the URL can POST in a loop, and each request can trigger several
# billed web searches on top of the output tokens. The caller also supplies the
# whole conversation, so an unbounded history is both a cost multiplier and a
# way to push the system prompt out of the model's attention.
#
# IMPORTANT - what this is not: the window below lives in this process's
# memory. Vercel runs several instances and recycles them, so the effective
# ceiling is per-instance and resets on every cold start. Treat it as a cost
# brake, not a security boundary. If this needs to be a real limit, back it
# with a shared store (e.g. Upstash Redis) rather than tightening these
# numbers - a lower number here just moves the same hole around.
# --------------------------------------------------------------------------- #
def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


RATE_LIMIT_REQUESTS = _int_env("CHAT_RATE_LIMIT_REQUESTS", 10)
RATE_LIMIT_WINDOW_S = _int_env("CHAT_RATE_LIMIT_WINDOW_S", 60)
MAX_MESSAGES = _int_env("CHAT_MAX_MESSAGES", 20)
MAX_MESSAGE_CHARS = _int_env("CHAT_MAX_MESSAGE_CHARS", 4000)
MAX_TOTAL_CHARS = _int_env("CHAT_MAX_TOTAL_CHARS", 12000)
MAX_BODY_BYTES = _int_env("CHAT_MAX_BODY_BYTES", 64 * 1024)

# ip -> deque of request timestamps inside the current window.
_hits: dict[str, deque] = {}
# Bound the dict itself; otherwise a spray of spoofed IPs grows it forever.
MAX_TRACKED_IPS = 10_000


def client_ip(headers) -> str:
    """Best-effort caller identity. Vercel sets both of these at the edge."""
    real = headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = headers.get("x-forwarded-for") or ""
    # Leftmost entry is the original client; the rest are proxies.
    return fwd.split(",")[0].strip() or "unknown"


def rate_limited(ip: str, now: float | None = None) -> int:
    """Record a hit for `ip`. Returns seconds to wait, or 0 if allowed."""
    now = time.time() if now is None else now
    cutoff = now - RATE_LIMIT_WINDOW_S

    if len(_hits) > MAX_TRACKED_IPS:
        for stale in [k for k, v in _hits.items() if not v or v[-1] <= cutoff]:
            del _hits[stale]
        if len(_hits) > MAX_TRACKED_IPS:  # still full of live callers
            _hits.clear()

    seen = _hits.setdefault(ip, deque())
    while seen and seen[0] <= cutoff:
        seen.popleft()

    if len(seen) >= RATE_LIMIT_REQUESTS:
        return max(1, int(seen[0] + RATE_LIMIT_WINDOW_S - now) + 1)

    seen.append(now)
    return 0


def validate_history(history) -> list:
    """Return the usable turns, or raise ValueError describing what was wrong."""
    if not isinstance(history, list) or not history:
        raise ValueError("messages must be a non-empty list")
    if len(history) > MAX_MESSAGES:
        raise ValueError(f"too many messages (max {MAX_MESSAGES})")

    total = 0
    turns = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message too long (max {MAX_MESSAGE_CHARS} characters)")
        total += len(content)
        if total > MAX_TOTAL_CHARS:
            raise ValueError(f"conversation too long (max {MAX_TOTAL_CHARS} characters)")
        turns.append({"role": role, "content": content})

    if not turns:
        raise ValueError("no usable user or assistant turns in messages")
    return turns


def extract_citations(content):
    seen, out = set(), []

    def add(url, title):
        if url and url not in seen:
            seen.add(url)
            out.append({"title": title or url, "url": url})

    for block in content:
        btype = getattr(block, "type", None)
        if btype == "text":
            for cite in getattr(block, "citations", None) or []:
                add(getattr(cite, "url", None), getattr(cite, "title", None))
        elif btype == "web_search_tool_result":
            results = getattr(block, "content", None)
            if isinstance(results, list):
                for r in results:
                    add(getattr(r, "url", None), getattr(r, "title", None))
    return out


def stream_answer(turns):
    """Yield (event, payload) tuples for one chat turn.

    `turns` must already have been through validate_history().
    """
    messages = list(turns)
    citations = []

    try:
        for _ in range(4):  # cap pause_turn continuations
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield ("token", text)
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": final.content})
            citations += extract_citations(final.content)

            if final.stop_reason == "pause_turn":
                messages.append({"role": "user", "content": "Please continue."})
                continue
            break

        seen, unique = set(), []
        for c in citations:
            if c["url"] not in seen:
                seen.add(c["url"])
                unique.append(c)
        yield ("citations", unique)
    except Exception as exc:  # noqa: BLE001
        # The raw exception can carry request details and upstream message
        # text. Log it where only we can read it, and tell the caller nothing
        # it could use to probe the endpoint.
        print(f"chat error: {type(exc).__name__}: {exc}", file=sys.stderr)
        yield ("error", "Something went wrong generating that answer. Please try again.")


class handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload, extra_headers=()):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        retry_after = rate_limited(client_ip(self.headers))
        if retry_after:
            self._send_json(
                429,
                {"error": "Too many requests. Please slow down."},
                (("Retry-After", str(retry_after)),),
            )
            return

        # Reject an oversized body from the header, before reading it in.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": f"request too large (max {MAX_BODY_BYTES} bytes)"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or "{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            turns = validate_history(payload.get("messages", []))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        # Everything past here streams, so the status is already committed.
        # Later failures reach the client as an SSE "error" event instead.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        gen = stream_answer(turns)
        try:
            for event, data in gen:
                chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(chunk.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            gen.close()  # client left mid-answer; stop paying for the rest
