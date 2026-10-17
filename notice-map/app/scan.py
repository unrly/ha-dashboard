"""The on-demand scan: county cases -> map locations -> keep the ones inside the area -> save."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Optional

import httpx

from . import arcgis, area, docs, geo, geocode, interpret
from .cases import Case, from_feature, resolve_fields
from .config import Settings
from .discover import find_candidates, http_client
from .store import Store, now

log = logging.getLogger(__name__)


class ScanState:
    def __init__(self) -> None:
        self.running = False
        self.step = ""
        self.log: list[str] = []
        self.error: Optional[str] = None
        self.result: Optional[dict] = None
        self._lock = threading.Lock()

    def say(self, msg: str) -> None:
        log.info(msg)
        self.step = msg
        self.log.append(msg)

    def snapshot(self) -> dict:
        return {"running": self.running, "step": self.step, "log": self.log[-30:], "error": self.error,
                "result": self.result}


def resolve_layer(settings: Settings, store: Store, client: httpx.Client, state: ScanState) -> str:
    if settings.source.layer_url:
        return settings.source.layer_url
    cached = store.get("auto_layer_url")
    if cached:
        return cached
    state.say("Looking for the county's land use case layer (first scan only)…")
    cands = find_candidates(client, settings.source.gis_roots)
    if not cands:
        raise RuntimeError("Couldn't find a land use case layer on the county GIS server. "
                           "Run `python -m app.discover` and set source.layer_url in config.yaml.")
    best = cands[0]
    state.say(f"Using layer: {best['name']}")
    store.put("auto_layer_url", best["url"])
    store.put("auto_layer_candidates", json.dumps(cands))
    return best["url"]


def run_scan(settings: Settings, store: Store, state: ScanState) -> dict:
    scan_id = store.start_scan()
    scan_time = now()
    had_prior = store.get("last_scan_time") is not None  # only successful scans count
    try:
        with http_client(settings) as client:
            state.say("Loading the ZIP boundary…")
            region = area.load_area(settings, client)
            g = region["geometry"]
            env = geo.bbox(g)

            layer_url = resolve_layer(settings, store, client, state)
            state.say("Fetching active cases from the county…")
            info = arcgis.layer_info(client, layer_url)
            fmap = resolve_fields([f["name"] for f in info.get("fields") or []], settings.source.fields)
            if "case_number" not in fmap:
                raise RuntimeError(f"Can't tell which field holds the case number in {layer_url}. "
                                   "Set source.fields.case_number in config.yaml (see `python -m app.discover --layer`).")
            has_geom = bool(info.get("geometryType"))
            feats = list(arcgis.query_features(client, layer_url, where=settings.source.where,
                                               envelope=env if has_geom else None))
            state.say(f"Got {len(feats)} cases. Placing them on the map…")

            kept: list[Case] = []
            geocoded = 0
            for f in feats:
                pt = geo.esri_to_point(f.get("geometry"))
                c = from_feature(f, fmap, pt, geo.esri_rings(f.get("geometry")))
                if c is None:
                    continue
                if c.lat is None and c.address:
                    p = geocode.geocode(client, c.address, settings.area.zip)
                    if p:
                        c.lon, c.lat = p
                        geocoded += 1
                if c.lat is None or not geo.point_in_geometry(c.lon, c.lat, g):
                    continue
                kept.append(c)
            # One case can span several parcels; merge records that share a case number.
            merged: dict[str, Case] = {}
            for c in kept:
                if c.id in merged:
                    m = merged[c.id]
                    m.parcel = (m.parcel or []) + (c.parcel or [])
                else:
                    merged[c.id] = c
            kept = list(merged.values())
            state.say(f"{len(kept)} cases are inside ZIP {settings.area.zip}. Saving…")
            new_ids = store.upsert_cases(kept, scan_time)

            state.say("Finding case documents…")
            for c in kept:
                links = docs.collect_links(client, c, settings.source.case_page_template)
                store.set_docs(c.id, links)

        if settings.ai.enabled and settings.ai.interpret_on_scan and new_ids:
            for i, cid in enumerate(new_ids, 1):
                state.say(f"Summarizing plans with Claude ({i} of {len(new_ids)})…")
                try:
                    summarize_case(settings, store, cid)
                except Exception as e:  # keep scanning other cases
                    log.warning("summary for %s failed: %s", cid, e)

        store.put("last_scan_time", scan_time)
        store.put("new_ids", json.dumps(new_ids if had_prior else []))
        msg = f"{len(kept)} notices · {len(new_ids) if had_prior else 0} new"
        store.finish_scan(scan_id, True, msg, len(feats), len(kept), len(new_ids))
        return {"found": len(feats), "kept": len(kept), "new": len(new_ids) if had_prior else 0,
                "geocoded": geocoded, "message": msg}
    except Exception as e:
        store.finish_scan(scan_id, False, str(e))
        raise


def docs_key(links: list[dict]) -> str:
    return hashlib.sha1(json.dumps(sorted(d["url"] for d in links)).encode()).hexdigest()


def summarize_case(settings: Settings, store: Store, case_id: str, force: bool = False) -> dict:
    row = store.case(case_id)
    if not row:
        raise KeyError(case_id)
    links = row.get("docs") or []
    key = docs_key(links)
    existing = store.summary(case_id)
    if existing and existing.get("docs_key") == key and not force:
        return existing
    client = interpret.make_client()
    if client is None:
        raise RuntimeError("No Claude API key configured. Set ANTHROPIC_API_KEY to turn on plan summaries.")
    pdfs = []
    folder = settings.data_dir / "docs" / case_id
    with http_client(settings) as http:
        for d in links[: settings.ai.max_docs_per_case]:
            p = docs.download(http, d["url"], folder, settings.ai.max_doc_mb)
            if p:
                pdfs.append((d["title"], p))
    case = Case(**{k: v for k, v in row.items() if k in Case.model_fields})
    data = interpret.interpret(client, settings.ai, case, pdfs)
    store.save_summary(case_id, key, data)
    return store.summary(case_id)


def start_background(settings: Settings, store: Store, state: ScanState) -> bool:
    with state._lock:
        if state.running:
            return False
        state.running, state.error, state.result, state.log = True, None, None, []

    def work() -> None:
        try:
            state.result = run_scan(settings, store, state)
            state.say("Done · " + state.result["message"])
        except Exception as e:
            log.exception("scan failed")
            state.error = str(e)
            state.say("Scan failed")
        finally:
            state.running = False

    threading.Thread(target=work, daemon=True).start()
    return True
