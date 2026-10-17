"""Find the county's land use case layer.

Usage:
    python -m app.discover                 # search the county GIS servers and rank candidate layers
    python -m app.discover --layer URL     # show one layer's fields, field mapping, and a sample record
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys

import httpx

from . import arcgis
from .cases import resolve_fields
from .config import Settings, load_settings

log = logging.getLogger(__name__)

NAME_SCORES = [
    (r"active.?case", 8), (r"land.?use.?case", 8), (r"development.?(application|review|case)", 7),
    (r"case", 4), (r"application", 4), (r"hearing", 3), (r"notice", 3), (r"pending", 2), (r"proposal", 2),
    (r"planning", 1), (r"\bpz\b|p&z|p_z", 1),
]
NEGATIVE = [r"label", r"anno", r"historic", r"zoning$", r"school", r"election", r"voting", r"sheriff", r"road"]


def score_name(name: str) -> int:
    n = name.lower()
    s = sum(w for pat, w in NAME_SCORES if re.search(pat, n))
    s -= sum(3 for pat in NEGATIVE if re.search(pat, n))
    return s


def score_fields(fields: list[str]) -> int:
    up = [f.upper() for f in fields]
    s = 0
    if any("CASE" in f for f in up):
        s += 4
    if any("STATUS" in f for f in up):
        s += 2
    if any("PLANNER" in f for f in up):
        s += 3
    if any("HEAR" in f for f in up):
        s += 2
    if any("TYPE" in f for f in up):
        s += 1
    return s


def find_candidates(client: httpx.Client, roots: list[str], top: int = 8) -> list[dict]:
    """Rank layers by how much their names look like land use cases, then check fields of the best ones."""
    found = []
    for root in roots:
        for surl, sj in arcgis.walk_services(client, root):
            svc_name = surl.split("/rest/services/")[-1]
            for lyr in (sj.get("layers") or []) + (sj.get("tables") or []):
                if lyr.get("subLayerIds"):
                    continue  # group layer
                s = score_name(f"{svc_name} {lyr.get('name', '')}")
                if s > 0:
                    found.append({"url": f"{surl}/{lyr['id']}", "name": f"{svc_name} / {lyr.get('name')}", "score": s})
    found.sort(key=lambda c: -c["score"])
    checked = []
    for c in found[: top * 2]:
        try:
            info = arcgis.layer_info(client, c["url"])
        except (httpx.HTTPError, arcgis.ArcGISError) as e:
            log.warning("skip %s: %s", c["url"], e)
            continue
        names = [f["name"] for f in info.get("fields") or []]
        c["fields"] = names
        c["geometry"] = info.get("geometryType")
        c["score"] += score_fields(names)
        checked.append(c)
    checked.sort(key=lambda c: -c["score"])
    return checked[:top]


def http_client(settings: Settings) -> httpx.Client:
    return httpx.Client(timeout=settings.http_timeout, headers={"User-Agent": settings.user_agent},
                        follow_redirects=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layer", help="inspect one layer URL")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = load_settings()
    with http_client(s) as client:
        if args.layer:
            info = arcgis.layer_info(client, args.layer)
            names = [f["name"] for f in info.get("fields") or []]
            print(f"Layer: {info.get('name')}  geometry: {info.get('geometryType')}")
            print("Fields:", ", ".join(names))
            print("Guessed mapping:", json.dumps(resolve_fields(names, s.source.fields), indent=2))
            sample = next(arcgis.query_features(client, args.layer, page_size=1, max_features=1), None)
            print("Sample record:", json.dumps((sample or {}).get("attributes"), indent=2, default=str))
            return 0
        cands = find_candidates(client, s.source.gis_roots)
        if not cands:
            print("No candidate layers found. Check that the county GIS server is reachable.")
            return 1
        for c in cands:
            print(f"[{c['score']:>2}] {c['name']}\n     {c['url']}\n     fields: {', '.join(c.get('fields', [])[:18])}")
        print("\nPut the best one in config.yaml under source.layer_url, then check it with --layer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
