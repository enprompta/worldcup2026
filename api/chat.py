"""Vercel serverless function for the World Cup chat endpoint (POST /api/chat).

Streams the answer as Server-Sent Events, keeping the API key server-side.
Set ANTHROPIC_API_KEY in the Vercel project's environment variables.

Note: on Vercel's Python runtime the SSE frames are buffered and delivered when
the function returns, so the browser renders the full answer at once rather than
token-by-token. The local server (server.py) streams live.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make the shared config/helpers at the repo root importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic

from worldcup import MODEL, SYSTEM_PROMPT, TOOLS, extract_citations

client = anthropic.Anthropic()


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
                max_tokens=2048,
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
