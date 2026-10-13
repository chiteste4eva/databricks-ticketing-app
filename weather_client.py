"""
Client for the National Weather Service API (api.weather.gov).

Free, no API key, generous rate limits. NWS requires a descriptive
User-Agent header. Mirrors the shape of massive_client.py: a thin
session wrapper plus domain helpers that normalize responses into
weather document dicts ready for Lakebase upsert.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import requests

_BASE_URL = os.environ.get("NWS_API_BASE_URL", "https://api.weather.gov")
_DEFAULT_TIMEOUT = 30

# Built-in city -> (lat, lon) so location strings like "Chicago, IL" resolve
# without an external geocoding dependency. "lat,lon" strings are also accepted.
_CITY_COORDS = {
    "new york, ny": (40.7128, -74.0060),
    "los angeles, ca": (34.0522, -118.2437),
    "chicago, il": (41.8781, -87.6298),
    "houston, tx": (29.7604, -95.3698),
    "phoenix, az": (33.4484, -112.0740),
    "philadelphia, pa": (39.9526, -75.1652),
    "san antonio, tx": (29.4241, -98.4936),
    "san diego, ca": (32.7157, -117.1611),
    "dallas, tx": (32.7767, -96.7970),
    "austin, tx": (30.2672, -97.7431),
    "san jose, ca": (37.3382, -121.8863),
    "fort worth, tx": (32.7555, -97.3308),
    "columbus, oh": (39.9612, -82.9988),
    "charlotte, nc": (35.2271, -80.8431),
    "san francisco, ca": (37.7749, -122.4194),
    "indianapolis, in": (39.7684, -86.1581),
    "seattle, wa": (47.6062, -122.3321),
    "denver, co": (39.7392, -104.9903),
    "oklahoma city, ok": (35.4676, -97.5164),
    "nashville, tn": (36.1627, -86.7816),
    "el paso, tx": (31.7619, -106.4850),
    "washington, dc": (38.9072, -77.0369),
    "boston, ma": (42.3601, -71.0589),
    "miami, fl": (25.7617, -80.1918),
    "atlanta, ga": (33.7490, -84.3880),
    "minneapolis, mn": (44.9778, -93.2650),
    "detroit, mi": (42.3314, -83.0458),
    "tampa, fl": (27.9506, -82.4572),
    "new orleans, la": (29.9511, -90.0715),
    "kansas city, mo": (39.0997, -94.5786),
}


class WeatherClient:
    """Thin wrapper around the NWS API with a proper User-Agent."""

    def __init__(self, base_url: str | None = None, timeout: int = _DEFAULT_TIMEOUT,
                 user_agent: str = "dataexpert-weather-app/1.0 (homework contact: student)"):
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/geo+json"}
        )

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ---- location resolution ----

    @staticmethod
    def resolve_location(location: str) -> tuple[float, float, str]:
        """Turn "City, ST" or "lat,lon" into (lat, lon, canonical_name)."""
        text = location.strip()
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
            # Try "lat,lon" numeric form first.
            try:
                return float(parts[0]), float(parts[1]), text
            except (ValueError, IndexError):
                pass
            key = text.lower()
            if key in _CITY_COORDS:
                lat, lon = _CITY_COORDS[key]
                return lat, lon, text
        raise ValueError(
            f"Cannot resolve location {location!r}: use 'City, ST' (e.g. 'Chicago, IL') "
            "or 'lat,lon' (e.g. '41.88,-87.63')."
        )

    # ---- harvest ----

    def get_active_alerts(self, state: str) -> list[dict]:
        """Active NWS alerts for a 2-letter state code."""
        data = self.get("/alerts/active", params={"area": state.upper()})
        return data.get("features", [])

    def get_forecast(self, lat: float, lon: float) -> list[dict]:
        """Narrative forecast periods for a lat/lon via the gridpoint lookup."""
        point = self.get(f"/points/{lat:.4f},{lon:.4f}")
        forecast_url = (point.get("properties") or {}).get("forecast")
        if not forecast_url:
            return []
        path = forecast_url.replace(self.base_url, "")
        data = self.get(path)
        return (data.get("properties") or {}).get("periods", [])

    def harvest_location(self, location: str, alert_limit: int = 20) -> list[dict]:
        """
        Harvest unstructured weather text for one location.
        Returns normalized document dicts ready for upsert into weather_documents.
        """
        lat, lon, name = self.resolve_location(location)
        now = datetime.now(timezone.utc).isoformat()
        docs: list[dict] = []

        # 1) Active alerts (description + instruction are rich narrative text).
        state = name.split(",")[-1].strip() if "," in name else ""
        if len(state) == 2:
            try:
                for feature in self.get_active_alerts(state)[:alert_limit]:
                    props = feature.get("properties", {})
                    alert_id = feature.get("id") or props.get("id", "")
                    narrative = "\n\n".join(
                        p for p in [props.get("description"), props.get("instruction")] if p
                    ).strip()
                    if not narrative:
                        continue
                    docs.append({
                        "id": f"alert:{alert_id}",
                        "location": name,
                        "source_type": "alert",
                        "headline": props.get("event"),
                        "narrative_text": narrative,
                        "issued_at": props.get("sent") or props.get("effective"),
                        "payload": json.dumps(feature),
                        "synced_at": now,
                    })
            except requests.HTTPError:
                pass  # alerts are best-effort; forecasts still valuable

        # 2) Narrative forecast (detailedForecast per period).
        try:
            for period in self.get_forecast(lat, lon):
                detail = (period.get("detailedForecast") or "").strip()
                if not detail:
                    continue
                raw = f"{period.get('name', '')}|{period.get('startTime', '')}"
                doc_id = "forecast:" + hashlib.sha256(raw.encode()).hexdigest()[:32]
                docs.append({
                    "id": doc_id,
                    "location": name,
                    "source_type": "forecast",
                    "headline": f"{period.get('name')}: {period.get('shortForecast')}",
                    "narrative_text": detail,
                    "issued_at": period.get("startTime"),
                    "payload": json.dumps(period),
                    "synced_at": now,
                })
        except requests.HTTPError:
            pass

        return docs
