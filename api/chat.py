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
"""

import json
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

TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]

client = anthropic.Anthropic()


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


def stream_answer(history):
    """Yield (event, payload) tuples for one chat turn."""
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and m.get("content")
    ]
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
        yield ("error", str(exc))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or "{}")
            history = payload.get("messages", [])
            if not isinstance(history, list) or not history:
                raise ValueError("messages must be a non-empty list")
        except (ValueError, json.JSONDecodeError) as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event, data in stream_answer(history):
            chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
