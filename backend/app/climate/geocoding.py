"""Geocoding adapters (cached) with manual-coordinate fallback.

* OpenStreetMap Nominatim — usage policy: ≤ 1 request/s, identifying User-Agent, caching required,
  no bulk geocoding. https://operations.osmfoundation.org/policies/nominatim/
* Open-Meteo Geocoding API (GeoNames-based, returns elevation).
Results are cached on disk; when both fail the caller must enter latitude/longitude manually.
"""

from __future__ import annotations

import threading
import time

from app.climate.http import CachedClient
from app.core.config import get_settings
from app.core.errors import ProviderError

_lock = threading.Lock()
_last_call = [0.0]


def _throttle(min_interval_s: float = 1.0):
    with _lock:
        wait = _last_call[0] + min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def geocode_nominatim(query: str, client: CachedClient | None = None, limit: int = 5) -> list[dict]:
    client = client or CachedClient("nominatim")
    _throttle()
    body, meta = client.get_json(get_settings().nominatim_url, {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1})
    out = []
    for r in body if isinstance(body, list) else []:
        out.append({"name": r.get("display_name"), "latitude": float(r["lat"]), "longitude": float(r["lon"]),
                    "country": (r.get("address") or {}).get("country"), "elevation_m": None, "timezone": None,
                    "source": "OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL)", "cached": meta["cached"]})
    return out


def geocode_open_meteo(query: str, client: CachedClient | None = None, limit: int = 5) -> list[dict]:
    client = client or CachedClient("open_meteo_geocoding")
    body, meta = client.get_json("https://geocoding-api.open-meteo.com/v1/search", {"name": query, "count": limit, "format": "json"})
    out = []
    for r in body.get("results", []) or []:
        out.append({"name": ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x),
                    "latitude": r["latitude"], "longitude": r["longitude"], "country": r.get("country"),
                    "elevation_m": r.get("elevation"), "timezone": r.get("timezone"),
                    "source": "Open-Meteo Geocoding (GeoNames, CC BY 4.0)", "cached": meta["cached"]})
    return out


def geocode(query: str, preferred: str | None = None) -> dict:
    order = [preferred or get_settings().geocoder]
    order += [g for g in ("open_meteo", "nominatim") if g not in order]
    errors = []
    for g in order:
        try:
            res = geocode_open_meteo(query) if g == "open_meteo" else geocode_nominatim(query)
            if res:
                return {"results": res, "provider": g, "errors": errors}
            errors.append(f"{g}: no results")
        except ProviderError as e:
            errors.append(e.message)
    return {"results": [], "provider": None, "errors": errors,
            "fallback": "Geocoding unavailable — enter latitude and longitude manually or pick the site on the map."}
