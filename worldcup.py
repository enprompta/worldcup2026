#!/usr/bin/env python3
"""Lightweight CLI that answers questions about the 2026 FIFA World Cup.

Calls the Anthropic API with the built-in web_search tool so answers can reflect
live results (the tournament runs June 11 - July 19, 2026 across the USA, Canada,
and Mexico).

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
    python worldcup.py                    # interactive chat
    python worldcup.py "who won the final?" # one-shot question
"""

import sys

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
    "Keep answers concise and lead with the direct answer."
)

TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]


# --------------------------------------------------------------------------- #
# Shared response helpers (used by both the CLI and the web server)
# --------------------------------------------------------------------------- #
def extract_citations(content) -> list:
    """Pull {title, url} web-search sources from an assistant message, deduped."""
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


# --------------------------------------------------------------------------- #
# The single LLM call site. This is the function students instrument.
# --------------------------------------------------------------------------- #
def ask(client: anthropic.Anthropic, messages: list) -> str:
    """Run one turn (resolving web-search pauses); stream text and return it."""
    answer_parts: list[str] = []
    citations: list = []

    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                answer_parts.append(text)
            final = stream.get_final_message()

        messages.append({"role": "assistant", "content": final.content})
        citations += extract_citations(final.content)

        if final.stop_reason == "pause_turn":
            messages.append({"role": "user", "content": "Please continue."})
            continue
        break

    print()
    if citations:
        seen, unique = set(), []
        for c in citations:
            if c["url"] not in seen:
                seen.add(c["url"])
                unique.append(c)
        print("Sources:")
        for i, c in enumerate(unique, 1):
            print(f"  [{i}] {c['title']} - {c['url']}")
    return "".join(answer_parts)


def main() -> int:
    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not initialize the Anthropic client: {exc}", file=sys.stderr)
        print(
            "Set ANTHROPIC_API_KEY or run `ant auth login`, then try again.",
            file=sys.stderr,
        )
        return 1

    messages: list = []

    if len(sys.argv) > 1:  # one-shot mode
        messages.append({"role": "user", "content": " ".join(sys.argv[1:])})
        try:
            ask(client, messages)
        except anthropic.APIError as exc:
            print(f"\nAPI error: {exc}", file=sys.stderr)
            return 1
        return 0

    print("2026 World Cup assistant. Ask me anything about the tournament.")
    print("Type 'quit' or 'exit' (or Ctrl-C) to leave.\n")

    while True:
        try:
            question = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0

        if not question:
            continue
        if question.lower() in {"quit", "exit"}:
            print("Bye!")
            return 0

        messages.append({"role": "user", "content": question})
        print("\ncup > ", end="", flush=True)
        try:
            ask(client, messages)
        except anthropic.APIError as exc:
            print(f"\nAPI error: {exc}", file=sys.stderr)
            messages.pop()
        print()


if __name__ == "__main__":
    raise SystemExit(main())
