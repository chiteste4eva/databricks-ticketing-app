# Weather MCP Server — Day 3 Homework

An MCP (Model Context Protocol) server that exposes weather-forecast tools,
built with FastMCP and backed by the free Open-Meteo API (no signup, no key).
A Databricks Agent Bricks agent uses this server as an external tool to answer
natural-language weather questions.

## Architecture

```
User question (e.g. "Will it rain this weekend in Boston?")
        │
        ▼
Databricks Agent Bricks agent  (system prompt in agent/system_prompt.md)
        │  calls tools over streamable HTTP
        ▼
weather_mcp_server.py  (FastMCP, 4 @tool functions — thin wrappers)
        │
        ▼
weather_broker.py  (all HTTP + parsing; Open-Meteo geocoding & forecast)
        │
        ▼
Open-Meteo API (free, no key)
```

Key design rule (per the assignment): no raw HTTP calls inside `@tool`
functions — everything network-related lives in `weather_broker.py`.

## Tools

| Tool | What it does |
|---|---|
| `get_current_weather(location)` | Current temp, feels-like, humidity, wind, conditions |
| `get_forecast(location, days=3)` | 1–7 day forecast: highs/lows, precipitation chance, conditions |
| `predict_umbrella_needed(location, date)` | Judgment call: umbrella if precip chance ≥ 50% or rain/drizzle/thunderstorm codes |
| `get_travel_recommendation(location, date)` | GOOD / CAUTION / AVOID verdict from forecast rules |

All tools return JSON dicts; failures return `{"error": "..."}` (no stack
traces) so the agent degrades gracefully.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python weather_mcp_server.py        # serves on http://127.0.0.1:8000/mcp
```

No API keys or secrets needed (Open-Meteo is keyless).

## Deploy (Databricks Apps)

`app.yaml` runs `python weather_mcp_server.py`; the server reads the port
from `$PORT`. Deploy the repo as its own Databricks App, then register the
app's `/mcp` URL as an external tool on an Agent Bricks agent using
`agent/system_prompt.md` as the system prompt.

## Demo

See `demo/test_queries.md` for three natural-language questions and the
agent's answers.
