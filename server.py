#!/usr/bin/env python3
"""Streaming web backend for the 2026 World Cup assistant.

Serves the chat UI (index.html) and streams chat responses from Claude over
Server-Sent Events, keeping the API key server-side. Uses only the Python
standard library plus the anthropic SDK - no web framework.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
    python server.py                      # then open http://localhost:8000
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic

from worldcup import ALL_TOOLS, MODEL, SYSTEM_PROMPT, extract_citations
from worldcup_tools import run_tool_uses

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))

client = anthropic.Anthropic()


def stream_answer(history: list):
    """Generator yielding (event, payload) for one chat turn.

    Events: ("token", str), ("citations", list), ("error", str).
    This is the single LLM call site - the function students instrument.
    """
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and m.get("content")
    ]
    citations: list = []

    try:
        for _ in range(8):  # cap web_search pauses + client tool rounds
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=ALL_TOOLS,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield ("token", text)
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": final.content})
            citations += extract_citations(final.content)

            if final.stop_reason == "pause_turn":  # web_search running server-side
                messages.append({"role": "user", "content": "Please continue."})
                continue
            if final.stop_reason == "tool_use":  # client tools requested
                tool_results = run_tool_uses(final.content)
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _send(self, code, body, content_type="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_event(self, event, payload):
        chunk = f"event: {event}\ndata: {json.dumps(payload)}\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, "index.html not found", "text/plain")
        else:
            self._send(404, "Not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/chat":
            self._send(404, json.dumps({"error": "Not found"}))
            return

        # Validate the body before switching to a streaming response.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or "{}")
            history = payload.get("messages", [])
            if not isinstance(history, list) or not history:
                raise ValueError("messages must be a non-empty list")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}))
            return

        # Stream the answer as Server-Sent Events.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        gen = stream_answer(history)
        try:
            for event, data in gen:
                self._sse_event(event, data)
        except (BrokenPipeError, ConnectionResetError):
            gen.close()  # client left


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"2026 World Cup assistant running at http://localhost:{PORT}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
