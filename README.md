# 2026 World Cup Assistant

A lightweight assistant that answers questions about the 2026 FIFA World Cup
(USA / Canada / Mexico, June 11 – July 19, 2026). It calls Claude with the
built-in web-search tool, so answers can reflect live results and fixtures.

Comes in two forms — a web app and a command-line tool — sharing one prompt and
one dependency.

The app is intentionally left uninstrumented; adding instrumentation is a
student exercise.
## Instrument it with Enprompta

Want to see exactly what instrumentation adds? Open the quickstart notebook in
Colab — it clones this app, wires OpenTelemetry tracing, a versioned prompt, and
evals around the single LLM call site, then streams the traces to your Enprompta
project.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Enprompta/worldcup2026/blob/main/notebooks/enprompta_quickstart.ipynb)

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or run: ant auth login
```

## Web app (front end)

```bash
python server.py
```

Then open http://localhost:8000 in your browser. Set a different port with
`PORT=9000 python server.py`.

## Command line

Interactive chat:

```bash
python worldcup.py
```

One-shot question:

```bash
python worldcup.py "which teams are in the final?"
```

Type `quit` or `exit` (or press Ctrl-C) to leave interactive mode.

## Files

- `index.html` — self-contained chat UI (inline CSS/JS, no build step).
- `server.py` — standard-library web server; serves the page and proxies chat
  to Claude, keeping your API key server-side (never sent to the browser).
- `worldcup.py` — the CLI, and the shared model/prompt/tool config.

## How it works

- Responses come from `claude-opus-4-8`, streamed token-by-token (Server-Sent
  Events in the web app, `text_stream` in the CLI).
- The `web_search` server tool is enabled so the model can look up current
  results, standings, and schedules instead of guessing; sources are shown as
  citations under each answer.
- Conversation history gives follow-up questions context (kept in memory in the
  CLI; held by the browser and resent per request in the web app).

