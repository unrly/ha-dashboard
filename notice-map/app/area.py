"""The area of interest: a ZIP boundary from the Census, a radius, or your own GeoJSON."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from . import arcgis, geo
from .config import Settings

log = logging.getLogger(__name__)


def _esri_polygon_to_geojson(geom: dict) -> dict:
    rings = [[[p[0], p[1]] for p in r] for r in geom["rings"]]
    # Esri: clockwise rings are outer boundaries, counter-clockwise are holes.
    polys: list[list] = []
    for r in rings:
        if geo._ring_area(r) < 0 or not polys:  # clockwise in lon/lat => negative signed area
            polys.append([r])
        else:
            polys[-1].append(r)
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def fetch_zcta(client: httpx.Client, service_url: str, zip_code: str) -> dict:
    svc = arcgis.get_json(client, service_url)
    layer = None
    for lyr in svc.get("layers") or []:
        name = (lyr.get("name") or "").lower()
        if "zip code tabulation" in name and "label" not in name:
            layer = lyr
            break
    if layer is None:
        raise RuntimeError(f"No ZIP Code Tabulation Areas layer found in {service_url}")
    layer_url = f"{service_url.rstrip('/')}/{layer['id']}"
    info = arcgis.layer_info(client, layer_url)
    names = {f["name"].upper(): f["name"] for f in info.get("fields") or []}
    key = next((names[k] for k in ("ZCTA5", "ZCTA5CE20", "ZCTA5CE10", "GEOID", "BASENAME") if k in names), None)
    if key is None:
        raise RuntimeError(f"Can't find a ZIP field in {layer_url}")
    feats = list(arcgis.query_features(client, layer_url, where=f"{key}='{zip_code}'"))
    if not feats:
        raise RuntimeError(f"ZIP {zip_code} not found in {layer_url}")
    return _esri_polygon_to_geojson(feats[0]["geometry"])


def load_area(settings: Settings, client: httpx.Client | None = None) -> dict:
    """Return the area as a GeoJSON Feature. ZIP lookups are cached in the data folder."""
    a = settings.area
    props = {"mode": a.mode, "zip": a.zip, "center": list(a.center)}
    if a.mode == "radius":
        return {"type": "Feature", "properties": {**props, "radius_km": a.radius_km},
                "geometry": geo.circle(a.center[0], a.center[1], a.radius_km)}
    if a.mode == "geojson":
        data = json.loads(Path(a.geojson_path).read_text())
        g = data.get("geometry") or (data.get("features") or [{}])[0].get("geometry") or data
        return {"type": "Feature", "properties": props, "geometry": g}

    cache = settings.data_dir / f"zcta_{a.zip}.geojson"
    if cache.exists():
        return json.loads(cache.read_text())
    if client is None:
        raise RuntimeError("ZIP boundary not cached yet; a scan will download it")
    g = fetch_zcta(client, a.zcta_service, a.zip)
    feat = {"type": "Feature", "properties": props, "geometry": g}
    cache.write_text(json.dumps(feat))
    log.info("cached ZIP %s boundary", a.zip)
    return feat
