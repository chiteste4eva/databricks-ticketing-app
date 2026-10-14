"""
weather_agent.py — minimal demo agent for the weather MCP server.

Connects to the MCP server over streamable HTTP (FastMCP Client) and
answers natural-language weather questions by routing each question to
the right tool, then formatting a short conversational answer.

This mirrors what the Databricks Agent Bricks agent would do with the
system prompt in agent/system_prompt.md: every weather fact comes from
a tool call; the agent never invents data.

Usage:
    python weather_agent.py "Will I need an umbrella in Seattle tomorrow?"
    # or interactive:
    python weather_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

from fastmcp import Client

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


def pick_tool(question: str) -> tuple[str, dict]:
    """Route a question to a tool + arguments (simple keyword routing)."""
    q = question.lower()
    # location: crude "in <Place>" / "for <Place>" extraction
    m = re.search(r"\b(?:in|for|near)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", question)
    location = m.group(1) if m else "Boston"
    # date
    date = "today"
    if "tomorrow" in q:
        date = "tomorrow"
    elif "weekend" in q or "saturday" in q:
        date = "2026-10-17"  # upcoming Saturday
    elif "sunday" in q:
        date = "2026-10-18"

    if "umbrella" in q or "rain" in q:
        return "predict_umbrella_needed", {"location": location, "date": date}
    if "weekend" in q or "forecast" in q or "days" in q or "week" in q:
        return "get_forecast", {"location": location, "days": 3}
    if any(w in q for w in ("hike", "outdoor", "travel", "trip", "good day")):
        return "get_travel_recommendation", {"location": location, "date": date}
    return "get_current_weather", {"location": location}


def format_answer(tool: str, result: dict, question: str) -> str:
    """Turn a tool result into a short conversational answer."""
    if "error" in result:
        return f"Sorry — {result['error']}"
    if tool == "get_current_weather":
        return (f"Right now in {result['place']}: {result['conditions'].lower()}, "
                f"{result['temperature_c']}°C (feels like {result['feels_like_c']}°C), "
                f"humidity {result['humidity_pct']}%, wind {result['wind_kph']} kph.")
    if tool == "get_forecast":
        lines = [f"{d['date']}: {d['conditions'].lower()}, "
                 f"{d['low_c']}–{d['high_c']}°C, {d['precipitation_chance_pct']}% rain"
                 for d in result["days"]]
        return f"{len(result['days'])}-day forecast for {result['place']}:\n" + "\n".join(lines)
    if tool == "predict_umbrella_needed":
        verdict = "Yes, bring one." if result["umbrella_needed"] else "No need."
        return f"{verdict} {result['reason']} ({result['place']}, {result['date']})."
    if tool == "get_travel_recommendation":
        return f"{result['verdict']} — {result['reason']} ({result['place']}, {result['date']})."
    return json.dumps(result, indent=2)


async def answer(question: str) -> str:
    tool, args = pick_tool(question)
    async with Client(MCP_URL) as client:
        res = await client.call_tool(tool, args)
        data = json.loads(res.content[0].text)
    return f"[tool: {tool} {json.dumps(args)}]\n" + format_answer(tool, data, question)


async def main() -> None:
    questions = sys.argv[1:] or []
    if not questions:
        print("Ask a weather question (blank line to quit).")
        while True:
            q = input("> ").strip()
            if not q:
                break
            questions.append(q)
    for q in questions:
        print(f"\nQ: {q}")
        print("A:", await answer(q))


if __name__ == "__main__":
    asyncio.run(main())
