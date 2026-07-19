#!/usr/bin/env python3
"""Client-side tools for the 2026 FIFA World Cup assistant.

`worldcup.py` calls Claude with the built-in ``web_search`` tool for live and
breaking information. This module adds a set of *client-side* tools backed by a
small, deterministic sample dataset — standings, fixtures, squads, player stats,
venues, a timezone helper, a qualification calculator, and a knowledge-base
lookup. Claude decides when to call them; the loop in ``worldcup.py`` executes
them and feeds the results back.

Why this matters for the course: each client-tool call is a discrete step you
can *trace* and *evaluate*. Where the plain web_search agent produced one flat
run, an agent that composes these tools produces a deep, typed span tree
(agent -> tool -> tool -> retrieval -> llm). The tools themselves stay
deliberately *uninstrumented* — adding the tracing is the course.

The data here is illustrative sample data for a tournament that is still being
played; it is meant to be realistic and reproducible, not authoritative. For
anything live (actual results, standings, injuries) the agent should still use
``web_search``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Sample dataset (illustrative — see module docstring)
# --------------------------------------------------------------------------- #
# 48 teams, 12 groups of 4. Top two of each group plus the eight best
# third-placed teams advance to a 32-team knockout round.
GROUPS: dict[str, list[str]] = {
    "A": ["Mexico", "Poland", "Saudi Arabia", "New Zealand"],
    "B": ["Belgium", "Canada", "Morocco", "Uzbekistan"],
    "C": ["USA", "Wales", "Egypt", "Jordan"],
    "D": ["Argentina", "Australia", "Ivory Coast", "Panama"],
    "E": ["France", "Norway", "Japan", "Curacao"],
    "F": ["Brazil", "Croatia", "Senegal", "Qatar"],
    "G": ["England", "Ecuador", "Ghana", "New Caledonia"],
    "H": ["Spain", "Colombia", "Iran", "Haiti"],
    "I": ["Portugal", "Uruguay", "South Korea", "Cape Verde"],
    "J": ["Germany", "Switzerland", "Nigeria", "Honduras"],
    "K": ["Netherlands", "Austria", "Cameroon", "Costa Rica"],
    "L": ["Italy", "Denmark", "Algeria", "Paraguay"],
}

# Played-out sample standings for the two groups the course uses. Any other
# group falls back to a "not yet played" table computed from GROUPS.
# Each row: played, won, drawn, lost, goals for, goals against, points.
STANDINGS: dict[str, list[dict]] = {
    "A": [
        {"team": "Mexico", "pld": 2, "w": 2, "d": 0, "l": 0, "gf": 4, "ga": 1, "pts": 6},
        {"team": "Poland", "pld": 2, "w": 1, "d": 0, "l": 1, "gf": 3, "ga": 3, "pts": 3},
        {"team": "Saudi Arabia", "pld": 2, "w": 1, "d": 0, "l": 1, "gf": 2, "ga": 2, "pts": 3},
        {"team": "New Zealand", "pld": 2, "w": 0, "d": 0, "l": 2, "gf": 1, "ga": 4, "pts": 0},
    ],
    "B": [
        {"team": "Belgium", "pld": 2, "w": 1, "d": 1, "l": 0, "gf": 3, "ga": 1, "pts": 4},
        {"team": "Canada", "pld": 2, "w": 1, "d": 1, "l": 0, "gf": 2, "ga": 1, "pts": 4},
        {"team": "Morocco", "pld": 2, "w": 0, "d": 1, "l": 1, "gf": 1, "ga": 2, "pts": 1},
        {"team": "Uzbekistan", "pld": 2, "w": 0, "d": 1, "l": 1, "gf": 1, "ga": 3, "pts": 1},
    ],
}

# A handful of fixtures, including played and upcoming Group A matches. Kickoff
# times are in UTC so the timezone tool has something real to convert.
FIXTURES: list[dict] = [
    {"id": "A1", "group": "A", "home": "Mexico", "away": "New Zealand",
     "kickoff_utc": "2026-06-13T19:00:00Z", "venue": "Estadio Azteca",
     "status": "played", "score": "3-1"},
    {"id": "A2", "group": "A", "home": "Poland", "away": "Saudi Arabia",
     "kickoff_utc": "2026-06-13T22:00:00Z", "venue": "SoFi Stadium",
     "status": "played", "score": "2-1"},
    {"id": "A3", "group": "A", "home": "Mexico", "away": "Poland",
     "kickoff_utc": "2026-06-19T19:00:00Z", "venue": "Estadio Azteca",
     "status": "played", "score": "1-0"},
    {"id": "A4", "group": "A", "home": "Saudi Arabia", "away": "New Zealand",
     "kickoff_utc": "2026-06-19T22:00:00Z", "venue": "SoFi Stadium",
     "status": "played", "score": "1-0"},
    {"id": "A5", "group": "A", "home": "Mexico", "away": "Saudi Arabia",
     "kickoff_utc": "2026-06-24T19:00:00Z", "venue": "Estadio Azteca",
     "status": "scheduled", "score": None},
    {"id": "A6", "group": "A", "home": "New Zealand", "away": "Poland",
     "kickoff_utc": "2026-06-24T19:00:00Z", "venue": "SoFi Stadium",
     "status": "scheduled", "score": None},
    {"id": "C1", "group": "C", "home": "USA", "away": "Wales",
     "kickoff_utc": "2026-06-14T23:00:00Z", "venue": "MetLife Stadium",
     "status": "played", "score": "2-0"},
    {"id": "FIN", "group": "Final", "home": "TBD", "away": "TBD",
     "kickoff_utc": "2026-07-19T19:00:00Z", "venue": "MetLife Stadium",
     "status": "scheduled", "score": None},
]

VENUES: dict[str, dict] = {
    "Estadio Azteca": {"stadium": "Estadio Azteca", "city": "Mexico City",
                       "country": "Mexico", "capacity": 87000},
    "MetLife Stadium": {"stadium": "MetLife Stadium", "city": "New York/New Jersey",
                        "country": "USA", "capacity": 82500},
    "SoFi Stadium": {"stadium": "SoFi Stadium", "city": "Los Angeles",
                     "country": "USA", "capacity": 70000},
    "AT&T Stadium": {"stadium": "AT&T Stadium", "city": "Dallas",
                     "country": "USA", "capacity": 80000},
    "BC Place": {"stadium": "BC Place", "city": "Vancouver",
                 "country": "Canada", "capacity": 54500},
    "BMO Field": {"stadium": "BMO Field", "city": "Toronto",
                  "country": "Canada", "capacity": 45000},
}

SQUADS: dict[str, list[dict]] = {
    "Mexico": [
        {"name": "Guillermo Ochoa", "pos": "GK"},
        {"name": "Cesar Montes", "pos": "DF"},
        {"name": "Edson Alvarez", "pos": "MF"},
        {"name": "Hirving Lozano", "pos": "FW"},
        {"name": "Santiago Gimenez", "pos": "FW"},
    ],
    "USA": [
        {"name": "Matt Turner", "pos": "GK"},
        {"name": "Sergino Dest", "pos": "DF"},
        {"name": "Tyler Adams", "pos": "MF"},
        {"name": "Weston McKennie", "pos": "MF"},
        {"name": "Christian Pulisic", "pos": "FW"},
    ],
    "Canada": [
        {"name": "Maxime Crepeau", "pos": "GK"},
        {"name": "Alistair Johnston", "pos": "DF"},
        {"name": "Stephen Eustaquio", "pos": "MF"},
        {"name": "Alphonso Davies", "pos": "FW"},
        {"name": "Jonathan David", "pos": "FW"},
    ],
}

PLAYERS: dict[str, dict] = {
    "Santiago Gimenez": {"team": "Mexico", "pos": "FW", "goals": 3, "assists": 1, "apps": 3},
    "Christian Pulisic": {"team": "USA", "pos": "FW", "goals": 2, "assists": 2, "apps": 2},
    "Jonathan David": {"team": "Canada", "pos": "FW", "goals": 2, "assists": 0, "apps": 2},
    "Hirving Lozano": {"team": "Mexico", "pos": "FW", "goals": 1, "assists": 2, "apps": 3},
    "Alphonso Davies": {"team": "Canada", "pos": "FW", "goals": 1, "assists": 1, "apps": 2},
}

# Small knowledge base: the "static" facts an assistant should not need a live
# search for. search_knowledge_base() retrieves from this corpus.
KB_DOCS: list[dict] = [
    {"id": "format", "title": "Tournament format",
     "text": "The 2026 FIFA World Cup is the first with 48 teams, drawn into 12 "
             "groups of four. The top two from each group and the eight "
             "best third-placed teams advance to a 32-team knockout round of 32."},
    {"id": "tiebreakers", "title": "Group tie-breakers",
     "text": "If two or more teams finish level on points, ranking is decided by: "
             "1) goal difference, 2) goals scored, 3) points in head-to-head "
             "matches, 4) goal difference in head-to-head matches, 5) goals "
             "scored in head-to-head matches, 6) a fair-play score, and finally "
             "7) a drawing of lots by FIFA."},
    {"id": "hosts", "title": "Host nations and cities",
     "text": "The tournament is co-hosted by the United States, Canada, and "
             "Mexico from June 11 to July 19, 2026 across 16 host cities. "
             "Mexico City's Estadio Azteca hosts the opening match; the final is "
             "at MetLife Stadium in the New York/New Jersey area."},
    {"id": "final", "title": "The final",
     "text": "The 2026 World Cup final is scheduled for July 19, 2026 at MetLife "
             "Stadium in East Rutherford, New Jersey."},
    {"id": "history", "title": "Firsts and records",
     "text": "2026 is the first World Cup hosted by three nations and the first "
             "with a 48-team field, expanding from 32. It is the first time "
             "Canada hosts World Cup matches."},
    {"id": "slots", "title": "Confederation slots",
     "text": "The 48 places are allocated across confederations: UEFA 16, CAF 9, "
             "AFC 8, CONMEBOL 6, CONCACAF 6 (including three host places), OFC 1, "
             "plus two via an inter-confederation play-off tournament."},
    {"id": "ball", "title": "Match ball and rules",
     "text": "Group matches that finish level are simply drawn; knockout matches "
             "level after 90 minutes go to extra time and, if needed, a penalty "
             "shoot-out. Each team may name a 26-player squad."},
    {"id": "opening", "title": "Opening match",
     "text": "The opening match is on June 11, 2026 at Estadio Azteca in Mexico "
             "City, featuring the host nation Mexico."},
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _find_group_of(team: str) -> str | None:
    t = _norm(team)
    for g, teams in GROUPS.items():
        if any(_norm(x) == t for x in teams):
            return g
    return None


def _standings_for(group: str) -> list[dict]:
    """Return a standings table for a group, sorted best-first.

    Uses the played-out sample table when we have one, otherwise a fresh
    all-zeros table derived from the group's teams.
    """
    g = (group or "").strip().upper()
    if g in STANDINGS:
        rows = [dict(r) for r in STANDINGS[g]]
    elif g in GROUPS:
        rows = [{"team": t, "pld": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
                for t in GROUPS[g]]
    else:
        return []
    for r in rows:
        r["gd"] = r["gf"] - r["ga"]
    rows.sort(key=lambda r: (r["pts"], r["gd"], r["gf"]), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


# --------------------------------------------------------------------------- #
# Tool implementations. Each returns a plain, JSON-serialisable dict.
# --------------------------------------------------------------------------- #
def get_group_standings(group: str) -> dict:
    g = (group or "").strip().upper()
    rows = _standings_for(g)
    if not rows:
        return {"error": f"Unknown group '{group}'. Groups are A through L."}
    played = any(r["pld"] for r in rows)
    return {"group": g, "played": played, "standings": rows,
            "note": None if played else "Sample data: these group matches have not been played yet."}


def get_fixtures(team: str | None = None, group: str | None = None) -> dict:
    matches = FIXTURES
    if group:
        g = group.strip().upper()
        matches = [m for m in matches if m.get("group", "").upper() == g]
    if team:
        t = _norm(team)
        matches = [m for m in matches if _norm(m["home"]) == t or _norm(m["away"]) == t]
    return {"count": len(matches), "fixtures": [dict(m) for m in matches]}


def get_match_result(match_id: str) -> dict:
    for m in FIXTURES:
        if _norm(m["id"]) == _norm(match_id):
            if m["status"] != "played":
                return {"match_id": m["id"], "status": m["status"],
                        "note": "This match has not been played yet."}
            return {"match_id": m["id"], "home": m["home"], "away": m["away"],
                    "score": m["score"], "venue": m["venue"], "status": "played"}
    return {"error": f"No fixture with id '{match_id}'."}


def get_team_squad(team: str) -> dict:
    for name, roster in SQUADS.items():
        if _norm(name) == _norm(team):
            return {"team": name, "squad": [dict(p) for p in roster],
                    "group": _find_group_of(name)}
    return {"error": f"No sample squad for '{team}'.",
            "available": sorted(SQUADS.keys())}


def get_player_stats(player: str) -> dict:
    for name, stats in PLAYERS.items():
        if _norm(name) == _norm(player):
            return {"player": name, **stats}
    return {"error": f"No sample stats for '{player}'.",
            "available": sorted(PLAYERS.keys())}


def get_venue_info(venue: str) -> dict:
    v = _norm(venue)
    for name, info in VENUES.items():
        if v in _norm(name) or v in _norm(info["city"]):
            return dict(info)
    return {"error": f"No venue matching '{venue}'.",
            "available": sorted(VENUES.keys())}


def convert_kickoff_to_timezone(kickoff_utc: str, timezone_name: str) -> dict:
    """Convert an ISO-8601 UTC kickoff time to an IANA timezone (e.g. 'America/Los_Angeles')."""
    try:
        dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return {"error": f"Could not parse '{kickoff_utc}'. Use ISO-8601, e.g. 2026-06-24T19:00:00Z."}
    try:
        from zoneinfo import ZoneInfo
        local = dt.astimezone(ZoneInfo(timezone_name))
    except Exception:  # noqa: BLE001 — unknown tz or zoneinfo unavailable
        return {"error": f"Unknown timezone '{timezone_name}'. Use an IANA name like 'Europe/London'."}
    return {"kickoff_utc": kickoff_utc, "timezone": timezone_name,
            "local_time": local.strftime("%Y-%m-%d %H:%M"),
            "local_time_iso": local.isoformat()}


def calculate_qualification_scenario(group: str, team: str) -> dict:
    """A simplified 'can this team still finish in the top two?' check.

    Each team plays three group games. A team's best possible finish is its
    current points plus three per game remaining. We compare that ceiling
    against the points the second-placed team is guaranteed. This is a
    heuristic for the demo, not a full permutation of every remaining result.
    """
    g = (group or "").strip().upper()
    rows = _standings_for(g)
    if not rows:
        return {"error": f"Unknown group '{group}'."}
    me = next((r for r in rows if _norm(r["team"]) == _norm(team)), None)
    if not me:
        return {"error": f"{team} is not in group {g}."}

    games_left = max(0, 3 - me["pld"])
    my_ceiling = me["pts"] + 3 * games_left
    # The floor of whoever currently sits 2nd — a proxy for the bar to clear.
    second = rows[1] if len(rows) > 1 else me
    second_floor = second["pts"]

    already_top2 = me["rank"] <= 2 and games_left == 0
    still_possible = my_ceiling >= second_floor
    verdict = ("qualified" if already_top2
               else "possible" if still_possible
               else "eliminated")
    return {
        "group": g, "team": me["team"], "current_rank": me["rank"],
        "points": me["pts"], "games_left": games_left,
        "max_possible_points": my_ceiling,
        "second_place_points": second_floor,
        "verdict": verdict,
        "explanation": (
            f"{me['team']} sits {me['rank']} on {me['pts']} pts with {games_left} "
            f"game(s) left (ceiling {my_ceiling}). Second place currently holds "
            f"{second_floor} pts. Tie-breakers may still decide close cases — see "
            f"search_knowledge_base('tie-breakers')."
        ),
    }


def search_knowledge_base(query: str, k: int = 3) -> dict:
    """Keyword retrieval over the static KB corpus (rules, hosts, format, history).

    Deliberately dependency-free and deterministic so it runs anywhere. When you
    instrument the agent, wrap this call as a RETRIEVAL span; the course notebook
    also shows the nested embedding/rerank steps a vector store would add.
    """
    terms = [w for w in _norm(query).split() if len(w) > 2]
    scored = []
    for doc in KB_DOCS:
        hay = _norm(doc["title"] + " " + doc["text"])
        score = sum(hay.count(t) for t in terms)
        if score:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(1, k)]
    return {
        "query": query,
        "count": len(top),
        "results": [{"title": d["title"], "text": d["text"], "score": s} for s, d in top],
    }


# --------------------------------------------------------------------------- #
# Anthropic tool schemas + dispatch table
# --------------------------------------------------------------------------- #
CLIENT_TOOLS: list[dict] = [
    {"name": "get_group_standings",
     "description": "Current standings for a World Cup group (A-L): played, won, "
                    "drawn, lost, goals, and points, ranked. Use for group tables.",
     "input_schema": {"type": "object",
                      "properties": {"group": {"type": "string", "description": "Group letter A-L"}},
                      "required": ["group"]}},
    {"name": "get_fixtures",
     "description": "List matches, optionally filtered by team and/or group. "
                    "Returns kickoff times (UTC), venues, and status/score.",
     "input_schema": {"type": "object",
                      "properties": {"team": {"type": "string"},
                                     "group": {"type": "string", "description": "Group letter A-L"}}}},
    {"name": "get_match_result",
     "description": "Final score and venue for a specific match id (e.g. 'A3').",
     "input_schema": {"type": "object",
                      "properties": {"match_id": {"type": "string"}},
                      "required": ["match_id"]}},
    {"name": "get_team_squad",
     "description": "Sample squad list (name, position) for a national team.",
     "input_schema": {"type": "object",
                      "properties": {"team": {"type": "string"}},
                      "required": ["team"]}},
    {"name": "get_player_stats",
     "description": "Tournament stats for a player: goals, assists, appearances.",
     "input_schema": {"type": "object",
                      "properties": {"player": {"type": "string"}},
                      "required": ["player"]}},
    {"name": "get_venue_info",
     "description": "Stadium details (city, country, capacity) by stadium or city name.",
     "input_schema": {"type": "object",
                      "properties": {"venue": {"type": "string"}},
                      "required": ["venue"]}},
    {"name": "convert_kickoff_to_timezone",
     "description": "Convert an ISO-8601 UTC kickoff time to a local IANA timezone "
                    "(e.g. 'America/Los_Angeles'). Pair with get_fixtures.",
     "input_schema": {"type": "object",
                      "properties": {"kickoff_utc": {"type": "string"},
                                     "timezone_name": {"type": "string"}},
                      "required": ["kickoff_utc", "timezone_name"]}},
    {"name": "calculate_qualification_scenario",
     "description": "Can a team still reach the top two of its group? Returns a "
                    "verdict (qualified/possible/eliminated) with the arithmetic.",
     "input_schema": {"type": "object",
                      "properties": {"group": {"type": "string"},
                                     "team": {"type": "string"}},
                      "required": ["group", "team"]}},
    {"name": "search_knowledge_base",
     "description": "Look up static tournament facts (format, tie-breakers, host "
                    "cities, history, the final). Prefer this over web_search for "
                    "rules and background that do not change.",
     "input_schema": {"type": "object",
                      "properties": {"query": {"type": "string"},
                                     "k": {"type": "integer", "description": "How many passages (default 3)"}},
                      "required": ["query"]}},
]

TOOL_IMPLS = {
    "get_group_standings": get_group_standings,
    "get_fixtures": get_fixtures,
    "get_match_result": get_match_result,
    "get_team_squad": get_team_squad,
    "get_player_stats": get_player_stats,
    "get_venue_info": get_venue_info,
    "convert_kickoff_to_timezone": convert_kickoff_to_timezone,
    "calculate_qualification_scenario": calculate_qualification_scenario,
    "search_knowledge_base": search_knowledge_base,
}


def run_tool(name: str, tool_input: dict):
    """Execute one client tool by name. Returns a JSON-serialisable result."""
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"error": f"Unknown tool '{name}'."}
    return impl(**(tool_input or {}))


def run_tool_uses(content) -> list[dict]:
    """Execute every client tool_use block in an assistant message.

    Returns the matching ``tool_result`` blocks to append as the next user turn.
    Server tools (web_search) are handled by Anthropic and skipped here.
    """
    results = []
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        name = getattr(block, "name", None)
        if name not in TOOL_IMPLS:
            continue  # not one of ours (e.g. a server tool)
        try:
            out = run_tool(name, getattr(block, "input", None) or {})
            payload, is_error = json.dumps(out, default=str), False
        except Exception as exc:  # noqa: BLE001 — surface tool errors to the model
            payload, is_error = f"Tool error: {exc}", True
        results.append({"type": "tool_result", "tool_use_id": block.id,
                        "content": payload, "is_error": is_error})
    return results


if __name__ == "__main__":
    # Offline smoke test — no API key needed:  python worldcup_tools.py
    _checks = [
        ("get_group_standings", {"group": "A"}),
        ("get_fixtures", {"team": "Poland"}),
        ("get_match_result", {"match_id": "A3"}),
        ("get_team_squad", {"team": "Mexico"}),
        ("get_player_stats", {"player": "Santiago Gimenez"}),
        ("get_venue_info", {"venue": "New York"}),
        ("convert_kickoff_to_timezone",
         {"kickoff_utc": "2026-06-24T19:00:00Z", "timezone_name": "America/Los_Angeles"}),
        ("calculate_qualification_scenario", {"group": "A", "team": "Poland"}),
        ("search_knowledge_base", {"query": "group tie-breakers if level on points"}),
    ]
    _ok = 0
    for _name, _args in _checks:
        try:
            _out = run_tool(_name, _args)
            _bad = isinstance(_out, dict) and _out.get("error")
            print("FAIL" if _bad else "ok  ", _name, "->", json.dumps(_out, default=str)[:110])
            _ok += 0 if _bad else 1
        except Exception as _exc:  # noqa: BLE001
            print("ERR ", _name, "->", _exc)
    _teams = [t for teams in GROUPS.values() for t in teams]
    assert len(_teams) == len(set(_teams)) == 48, "expected 48 unique teams across the groups"
    print(f"\n{_ok}/{len(_checks)} tools returned data; {len(_teams)} unique teams across {len(GROUPS)} groups.")
