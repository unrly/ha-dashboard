"""The on-demand scan: county cases -> map locations -> keep the ones inside the area -> save."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
from typing import Optional

import httpx

from . import arcgis, area, docs, geo, geocode, interpret, news
from .cases import Case, from_feature, resolve_fields, status_kind
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


def resolve_layers(settings: Settings, store: Store, client: httpx.Client, state: ScanState) -> list[tuple[str, str]]:
    """Return [(layer_url, layer_name)] to query."""
    src = settings.source
    if src.layer_url:
        return [(src.layer_url, "")]
    if src.service_url:
        svc = arcgis.get_json(client, src.service_url)
        layers = [(f"{src.service_url.rstrip('/')}/{l['id']}", l.get("name", ""))
                  for l in svc.get("layers") or [] if not l.get("subLayerIds")]
        if not layers:
            raise RuntimeError(f"No layers found in {src.service_url}")
        return layers
    cached = store.get("auto_layer_url")
    if cached:
        return [(cached, "")]
    state.say("Looking for the county's land use case layer (first scan only)…")
    cands = find_candidates(client, src.gis_roots)
    if not cands:
        raise RuntimeError("Couldn't find a land use case layer on the county GIS server. "
                           "Run `python -m app.discover` and set source.layer_url in config.yaml.")
    best = cands[0]
    state.say(f"Using layer: {best['name']}")
    store.put("auto_layer_url", best["url"])
    store.put("auto_layer_candidates", json.dumps(cands))
    return [(best["url"], "")]


def _layer_where(settings: Settings, info: dict, fmap: dict[str, str]) -> str:
    where = settings.source.where or "1=1"
    filed = fmap.get("filed")
    ftype = next((f.get("type") for f in info.get("fields") or [] if f["name"] == filed), None)
    if filed and ftype == "esriFieldTypeDate" and settings.source.max_age_years:
        cutoff = dt.date.today() - dt.timedelta(days=int(365.25 * settings.source.max_age_years))
        where = f"({where}) AND {filed} >= DATE '{cutoff.isoformat()}'"
    return where


def fetch_cases(settings: Settings, store: Store, client: httpx.Client, state: ScanState,
                env: tuple[float, float, float, float]) -> tuple[list[tuple[dict, dict, str]], int]:
    """Query every case layer. Returns ([(feature, field_map, layer_name)], total)."""
    out = []
    layers = resolve_layers(settings, store, client, state)
    for i, (url, name) in enumerate(layers, 1):
        state.say(f"Fetching cases from the county ({i} of {len(layers)}{': ' + name if name else ''})…")
        info = arcgis.layer_info(client, url)
        fields = [f["name"] for f in info.get("fields") or []]
        fmap = resolve_fields(fields, settings.source.fields)
        amanda = {"FOLDERYEAR", "FOLDERSEQUENCE", "FOLDERTYPE"} <= set(fields)
        if "case_number" not in fmap and not amanda:
            if len(layers) == 1:
                raise RuntimeError(f"Can't tell which field holds the case number in {url}. Set "
                                   "source.fields.case_number in config.yaml (see `python -m app.discover --layer`).")
            log.warning("skip %s: no case number field", url)
            continue
        has_geom = bool(info.get("geometryType"))
        for f in arcgis.query_features(client, url, where=_layer_where(settings, info, fmap),
                                       envelope=env if has_geom else None):
            out.append((f, fmap, name or info.get("name", "")))
    return out, len(out)


def enrich_from_posts(c: Case, post: dict) -> None:
    if post.get("title"):
        c.name = post["title"]
    if len(post.get("summary", "")) > len(c.description):
        c.description = post["summary"]
    if post.get("manager"):
        c.planner = post["manager"]
    c.hearings_note = post.get("hearings", "")
    c.news_url = post.get("url", "")
    for k, v in (post.get("hearing_dates") or {}).items():
        c.dates.setdefault(k, v)
    known = {l["url"] for l in c.links}
    for d in post.get("docs") or []:
        if d["url"] not in known:
            c.links.append({"title": d["title"], "url": d["url"]})
    if c.news_url and c.news_url not in known:
        c.links.append({"title": "County case page", "url": c.news_url, "kind": "page"})
    c.status_kind = status_kind(c.status, c.dates)


def run_scan(settings: Settings, store: Store, state: ScanState) -> dict:
    scan_id = store.start_scan()
    scan_time = now()
    had_prior = store.get("last_scan_time") is not None  # only successful scans count
    try:
        with http_client(settings) as client:
            state.say("Loading the ZIP boundary…")
            region = area.load_area(settings, client)
            g = region["geometry"]
            buf = settings.area.buffer_km
            env = geo.expand_bbox(geo.bbox(g), buf)

            feats, total = fetch_cases(settings, store, client, state, env)
            state.say(f"Got {total} records. Placing them on the map…")

            kept: list[Case] = []
            geocoded = 0
            for f, fmap, layer_name in feats:
                pt = geo.esri_to_point(f.get("geometry"))
                c = from_feature(f, fmap, pt, geo.esri_rings(f.get("geometry")), layer_name)
                if c is None:
                    continue
                if c.lat is None and c.address:
                    p = geocode.geocode(client, c.address, settings.area.zip)
                    if p:
                        c.lon, c.lat = p
                        geocoded += 1
                if c.lat is None or not geo.within(c.lon, c.lat, g, buf):
                    continue
                kept.append(c)
            # One case can span several parcels (one record each); merge records that share a case number.
            merged: dict[str, list[Case]] = {}
            for c in kept:
                merged.setdefault(c.id, []).append(c)
            kept = []
            for group in merged.values():
                m = group[0]
                if len(group) > 1:
                    m.lat = sum(x.lat for x in group) / len(group)
                    m.lon = sum(x.lon for x in group) / len(group)
                    rings = [r for x in group for r in (x.parcel or [])]
                    m.parcel = rings or None
                    m.raw["parcel_count"] = len(group)
                kept.append(m)

            if settings.source.news_list_url:
                state.say("Reading the county's high interest case posts…")
                posts = news.fetch_posts(client, settings.source.news_list_url)
                for c in kept:
                    if c.case_number in posts:
                        enrich_from_posts(c, posts[c.case_number])

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
        store.finish_scan(scan_id, True, msg, total, len(kept), len(new_ids))
        return {"found": total, "kept": len(kept), "new": len(new_ids) if had_prior else 0,
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
        for d in [x for x in links if x.get("kind", "pdf") == "pdf"][: settings.ai.max_docs_per_case]:
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
