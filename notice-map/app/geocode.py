"""Address lookup with the free US Census geocoder, for cases that come without a map location."""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)
URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def geocode(client: httpx.Client, address: str, zip_code: str) -> Optional[tuple[float, float]]:
    """Return (lon, lat) or None."""
    if not address or not re.search(r"\d", address):
        return None  # "Near Wadsworth and C-470" style text can't be geocoded reliably
    q = address if re.search(r"\b(CO|Colorado)\b", address, re.I) else f"{address}, CO {zip_code}"
    try:
        r = client.get(URL, params={"address": q, "benchmark": "Public_AR_Current", "format": "json"})
        r.raise_for_status()
        matches = r.json().get("result", {}).get("addressMatches") or []
    except (httpx.HTTPError, ValueError) as e:
        log.warning("geocode failed for %r: %s", address, e)
        return None
    if not matches:
        return None
    c = matches[0]["coordinates"]
    return float(c["x"]), float(c["y"])
