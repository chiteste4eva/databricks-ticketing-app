"""
weather_broker.py — adapter module for the weather MCP server.

All HTTP calls to the upstream weather API live here. The MCP @tool
functions in weather_mcp_server.py are thin wrappers over these
functions and must never make raw HTTP requests themselves.

Upstream API: Open-Meteo (https://open-meteo.com) — free, no signup,
no API key required.
  - Geocoding:  https://geocoding-api.open-meteo.com/v1/search
  - Forecast:    https://api.open-meteo.com/v1/forecast

Every public function returns a plain dict. On failure the dict has
an "error" key with a human-readable message (never a stack trace),
so the agent can handle it gracefully.
"""

from __future__ import annotations

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 20.0

# WMO weather-code -> short description (subset used by Open-Meteo).
WEATHER_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def describe_weather(code: int | None) -> str:
    """Map a WMO weather code to a short human description."""
    if code is None:
        return "Unknown"
    return WEATHER_DESCRIPTIONS.get(code, f"Unknown (code {code})")


def geocode(location: str) -> dict:
    """
    Resolve a place name to coordinates via Open-Meteo geocoding.

    Args:
        location: City name, optionally with state/country
                  (e.g. "Boston", "Austin, Texas", "Paris, France").

    Returns:
        Dict with name, latitude, longitude, country, region — or
        {"error": ...} if the place cannot be resolved.
    """
    location = (location or "").strip()
    if not location:
        return {"error": "No location provided. Please name a city, e.g. 'Boston'."}
    try:
        resp = httpx.get(
            GEOCODE_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except httpx.HTTPError:
        return {"error": "The geocoding service is unreachable right now. Please try again shortly."}
    if not results:
        return {"error": f"Could not find a place matching '{location}'. Try a city name like 'Boston' or 'Chicago'."}
    top = results[0]
    return {
        "name": top.get("name"),
        "latitude": top.get("latitude"),
        "longitude": top.get("longitude"),
        "country": top.get("country"),
        "region": top.get("admin1"),
    }


def _fetch_forecast(lat: float, lon: float, days: int) -> dict:
    """Low-level forecast fetch; returns the raw Open-Meteo payload or {"error": ...}."""
    try:
        resp = httpx.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                           "weather_code,wind_speed_10m,wind_direction_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,precipitation_sum",
                "timezone": "auto",
                "forecast_days": days,
                "temperature_unit": "celsius",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return {"error": "The weather service is unreachable right now. Please try again shortly."}


def get_current_weather(location: str) -> dict:
    """
    Current weather for a place.

    Args:
        location: City name (e.g. "Boston").

    Returns:
        Dict with place info, temperature_c, feels_like_c, humidity_pct,
        wind_kph, wind_direction_deg, conditions — or {"error": ...}.
    """
    geo = geocode(location)
    if "error" in geo:
        return geo
    data = _fetch_forecast(geo["latitude"], geo["longitude"], 1)
    if "error" in data:
        return data
    cur = data.get("current") or {}
    code = cur.get("weather_code")
    return {
        "place": geo["name"],
        "country": geo["country"],
        "region": geo["region"],
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "humidity_pct": cur.get("relative_humidity_2m"),
        "wind_kph": cur.get("wind_speed_10m"),
        "wind_direction_deg": cur.get("wind_direction_10m"),
        "conditions": describe_weather(code),
        "observed_at": cur.get("time"),
    }


def get_forecast(location: str, days: int = 3) -> dict:
    """
    Multi-day forecast for a place.

    Args:
        location: City name (e.g. "Boston").
        days: Number of days ahead, 1-7.

    Returns:
        Dict with place info and a list of daily entries
        (date, high_c, low_c, precipitation_chance_pct,
        precipitation_mm, conditions) — or {"error": ...}.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        return {"error": f"'{days}' is not a valid number of days. Use 1-7."}
    days = max(1, min(7, days))
    geo = geocode(location)
    if "error" in geo:
        return geo
    data = _fetch_forecast(geo["latitude"], geo["longitude"], days)
    if "error" in data:
        return data
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    entries = []
    for i, date in enumerate(dates):
        code = (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None
        entries.append({
            "date": date,
            "high_c": (daily.get("temperature_2m_max") or [None])[i],
            "low_c": (daily.get("temperature_2m_min") or [None])[i],
            "precipitation_chance_pct": (daily.get("precipitation_probability_max") or [None])[i],
            "precipitation_mm": (daily.get("precipitation_sum") or [None])[i],
            "conditions": describe_weather(code),
            "weather_code": code,
        })
    return {
        "place": geo["name"],
        "country": geo["country"],
        "region": geo["region"],
        "days": entries,
    }


def predict_umbrella_needed(location: str, date: str | None = None) -> dict:
    """
    Judgment call: should someone in `location` carry an umbrella?

    Rule (applied to real forecast data, not echoed from the API):
      - YES if precipitation chance >= 50%, or the forecast weather
        code is a rain/drizzle/thunderstorm type.
      - Otherwise NO.

    Args:
        location: City name (e.g. "Boston").
        date: "today"/"tomorrow" or YYYY-MM-DD within the next 7 days.
              Defaults to today.

    Returns:
        Dict with place, date, umbrella_needed (bool), reason,
        precipitation_chance_pct, conditions — or {"error": ...}.
    """
    fc = get_forecast(location, 7)
    if "error" in fc:
        return fc
    days = fc["days"]
    if not days:
        return {"error": f"No forecast data available for '{location}'."}

    want = (date or "today").strip().lower()
    if want in ("today",):
        target = days[0]
    elif want in ("tomorrow",):
        target = days[1] if len(days) > 1 else days[0]
    else:
        target = next((d for d in days if d["date"] == want), None)
        if target is None:
            return {"error": f"'{date}' is outside the 7-day forecast window."}

    chance = target.get("precipitation_chance_pct") or 0
    code = target.get("weather_code")
    needs_it = chance >= 50 or (code in RAIN_CODES)
    if needs_it:
        reason = (f"Precipitation chance is {chance}% with "
                  f"{target['conditions'].lower()} expected.")
    else:
        reason = (f"Only a {chance}% chance of precipitation with "
                  f"{target['conditions'].lower()} expected.")
    return {
        "place": fc["place"],
        "country": fc["country"],
        "date": target["date"],
        "umbrella_needed": needs_it,
        "reason": reason,
        "precipitation_chance_pct": chance,
        "conditions": target["conditions"],
    }


def get_travel_recommendation(location: str, date: str | None = None) -> dict:
    """
    Simple travel-day recommendation derived from the forecast.

    Rules:
      - AVOID if thunderstorm codes (95/96/99) or heavy rain/snow.
      - CAUTION if precipitation chance >= 50% or high winds implied
        by heavy-precipitation codes.
      - GOOD otherwise.

    Args:
        location: City name (e.g. "Boston").
        date: "today"/"tomorrow" or YYYY-MM-DD within the next 7 days.

    Returns:
        Dict with place, date, verdict (GOOD/CAUTION/AVOID), reason,
        high_c, low_c, conditions — or {"error": ...}.
    """
    fc = get_forecast(location, 7)
    if "error" in fc:
        return fc
    days = fc["days"]
    if not days:
        return {"error": f"No forecast data available for '{location}'."}

    want = (date or "today").strip().lower()
    if want in ("today",):
        target = days[0]
    elif want in ("tomorrow",):
        target = days[1] if len(days) > 1 else days[0]
    else:
        target = next((d for d in days if d["date"] == want), None)
        if target is None:
            return {"error": f"'{date}' is outside the 7-day forecast window."}

    code = target.get("weather_code")
    chance = target.get("precipitation_chance_pct") or 0
    severe = code in {65, 67, 75, 82, 86, 95, 96, 99}
    if severe:
        verdict, reason = ("AVOID",
            f"Severe weather expected: {target['conditions'].lower()}. Postpone outdoor travel plans.")
    elif chance >= 50 or code in RAIN_CODES:
        verdict, reason = ("CAUTION",
            f"Wet conditions likely ({chance}% precipitation, {target['conditions'].lower()}). Plan indoor alternatives.")
    else:
        verdict, reason = ("GOOD",
            f"Conditions look fine: {target['conditions'].lower()}, {chance}% precipitation chance.")
    return {
        "place": fc["place"],
        "country": fc["country"],
        "date": target["date"],
        "verdict": verdict,
        "reason": reason,
        "high_c": target["high_c"],
        "low_c": target["low_c"],
        "conditions": target["conditions"],
    }
