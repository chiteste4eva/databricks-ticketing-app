"""
weather_mcp_server.py — MCP server exposing weather-forecast tools.

Built with FastMCP using the streamable-HTTP transport so it can run
as a Databricks App and be registered as an external tool on a
Databricks Agent Bricks agent.

The @tool functions below are intentionally thin: all HTTP calls and
parsing live in weather_broker.py. Each tool returns a JSON-serializable
dict; failures come back as {"error": "..."} so the agent can respond
gracefully instead of surfacing a stack trace.

Run locally:
    python weather_mcp_server.py
Then point an MCP client at http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

import weather_broker

mcp = FastMCP("weather-mcp-server")


@mcp.tool()
def get_current_weather(location: str) -> dict:
    """
    Get the current weather for a location.

    Args:
        location: City name, e.g. "Boston", "Austin, Texas", "Paris, France".

    Returns:
        Current temperature, feels-like, humidity, wind and conditions,
        or {"error": "..."} if the location or service is unavailable.
    """
    try:
        return weather_broker.get_current_weather(location)
    except Exception:
        return {"error": f"Could not retrieve current weather for '{location}'. Please try again."}


@mcp.tool()
def get_forecast(location: str, days: int = 3) -> dict:
    """
    Get a multi-day weather forecast for a location.

    Args:
        location: City name, e.g. "Boston".
        days: Number of days ahead (1-7). Defaults to 3.

    Returns:
        Daily high/low temperatures, precipitation chance and conditions,
        or {"error": "..."} if the location or service is unavailable.
    """
    try:
        return weather_broker.get_forecast(location, days)
    except Exception:
        return {"error": f"Could not retrieve the forecast for '{location}'. Please try again."}


@mcp.tool()
def predict_umbrella_needed(location: str, date: str | None = None) -> dict:
    """
    Judge whether someone in a location should carry an umbrella.

    This is a derived judgment built from real forecast data: umbrella
    recommended when precipitation chance is >= 50% or the forecast
    calls for rain/drizzle/thunderstorm.

    Args:
        location: City name, e.g. "Boston".
        date: "today", "tomorrow", or YYYY-MM-DD within 7 days.
              Defaults to today.

    Returns:
        umbrella_needed (bool) plus the reason behind the call,
        or {"error": "..."} if the data is unavailable.
    """
    try:
        return weather_broker.predict_umbrella_needed(location, date)
    except Exception:
        return {"error": f"Could not assess umbrella need for '{location}'. Please try again."}


@mcp.tool()
def get_travel_recommendation(location: str, date: str | None = None) -> dict:
    """
    Recommend whether a date is good for outdoor travel in a location.

    Derived from the forecast: AVOID on severe weather (thunderstorms,
    heavy rain/snow), CAUTION when rain is likely, GOOD otherwise.

    Args:
        location: City name, e.g. "Boston".
        date: "today", "tomorrow", or YYYY-MM-DD within 7 days.
              Defaults to today.

    Returns:
        verdict (GOOD/CAUTION/AVOID) plus the reason behind the call,
        or {"error": "..."} if the data is unavailable.
    """
    try:
        return weather_broker.get_travel_recommendation(location, date)
    except Exception:
        return {"error": f"Could not assess travel conditions for '{location}'. Please try again."}


if __name__ == "__main__":
    # Databricks Apps injects the port via $PORT; default for local runs.
    port = int(os.environ.get("PORT", "8000"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path="/mcp")
