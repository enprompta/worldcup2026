# 2026 World Cup Assistant

A lightweight assistant that answers questions about the 2026 FIFA World Cup
(USA / Canada / Mexico, June 11 – July 19, 2026). It calls Claude with the
built-in web-search tool, so answers can reflect live results and fixtures, plus
a set of client-side tools for standings, fixtures, squads, venues, timezone
conversion and qualification scenarios.

Comes in two forms — a web app and a command-line tool — sharing one dependency.
The local app and CLI share a prompt; the deployed endpoint (`api/chat.py`)
carries its own, because it ships without the client-side tools.

The app is intentionally left uninstrumented; adding instrumentation is a
student exercise.
## Instrument it with Enprompta

Want to see exactly what instrumentation adds? Open the quickstart notebook in
Colab — it clones this app, wires OpenTelemetry tracing, a versioned prompt, and
evals around the single LLM call site, then streams the traces to your Enprompta
project.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Enprompta/worldcup2026/blob/main/notebooks/enprompta_quickstart.ipynb)

## Take the course: Evaluating AI Agents

Once you've traced it, learn to **evaluate** it. This app is the running example in
Enprompta's free course on agent evaluation — you'll score it across the three
surfaces every agent has (**router · skill · path**), catch real failures
(answering live questions with no citation, over-searching, off-topic leakage),
and prove a fix with an experiment. Every lab runs in one notebook — no local
setup.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Enprompta/worldcup2026/blob/main/notebooks/evaluating-ai-agents.ipynb)

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
- `worldcup.py` — the CLI, and the model/prompt config shared with `server.py`.
- `worldcup_tools.py` — the client-side tools and their sample dataset. Run it
  directly (`python worldcup_tools.py`) for an offline smoke test, no API key
  needed.
- `api/chat.py` — the deployed serverless endpoint. Self-contained so it bundles
  cleanly, which is why it repeats the model and prompt config rather than
  importing it.
- `vercel.json` — deployment config for that endpoint.

## How it works

- Responses come from `claude-opus-4-8`, streamed token-by-token (Server-Sent
  Events in the web app, `text_stream` in the CLI).
- The `web_search` server tool is enabled so the model can look up current
  results, standings, and schedules instead of guessing; sources are shown as
  citations under each answer.
- Conversation history gives follow-up questions context (kept in memory in the
  CLI; held by the browser and resent per request in the web app).
- The client-side tools answer from a small sample dataset, not a live feed.

## About the sample data

`worldcup_tools.py` ships a **frozen pre-tournament snapshot** — plausible
standings, fixtures, squads and player stats, written to be realistic and
reproducible so the tools always return something stable to trace and evaluate.

It is not real and it does not update, so it drifts further from the actual
tournament over time. Every tool that returns a fabricated outcome stamps a
note saying so, which travels into the model's context alongside the data —
`get_venue_info` and `search_knowledge_base` are exempt, since stadiums and
competition rules are real facts that don't go stale.

For anything factual, the agent should reach for `web_search`. If you want the
tools to answer from real results, replace the tables at the top of
`worldcup_tools.py`.

## Deployment

`api/chat.py` runs as a Vercel serverless function. It is public and
unauthenticated, and every call spends money on the account behind
`ANTHROPIC_API_KEY` — tokens plus up to five billed web searches per turn — so
it applies a per-IP rate limit (10 requests/minute) and caps the conversation it
will accept (20 turns, 12,000 characters, 64KB body). Over the limit returns
`429` with `Retry-After`.

Those limits are in-memory and per-instance, which makes them a cost brake
rather than a real boundary: Vercel runs several instances and cold starts reset
the window. Back them with a shared store if that is not good enough. Each one
is overridable by environment variable — see the top of `api/chat.py`.

