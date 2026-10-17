"""Minimal ArcGIS REST client: service directory walking and paged layer queries."""
from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

import httpx

log = logging.getLogger(__name__)


class ArcGISError(RuntimeError):
    pass


def get_json(client: httpx.Client, url: str, params: Optional[dict] = None) -> dict:
    params = {**(params or {}), "f": "json"}
    r = client.get(url, params=params)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as e:
        raise ArcGISError(f"{url} did not return JSON") from e
    # ArcGIS reports errors with HTTP 200 and an "error" object.
    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        raise ArcGISError(f"{url}: {err.get('code')} {err.get('message')} {err.get('details') or ''}".strip())
    return data


def layer_info(client: httpx.Client, layer_url: str) -> dict:
    return get_json(client, layer_url)


def query_features(
    client: httpx.Client,
    layer_url: str,
    where: str = "1=1",
    envelope: Optional[tuple[float, float, float, float]] = None,
    page_size: Optional[int] = None,
    max_features: int = 20000,
) -> Iterator[dict]:
    """Yield features (attributes + geometry in WGS84) from a layer, following pagination."""
    info = layer_info(client, layer_url)
    page = page_size or int(info.get("maxRecordCount") or 1000)
    supports_paging = bool((info.get("advancedQueryCapabilities") or {}).get("supportsPagination", True))
    params: dict[str, Any] = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
    }
    fields = info.get("fields") or []
    has_oid = info.get("objectIdField") or any(f.get("type") == "esriFieldTypeOID" for f in fields)
    if supports_paging and not has_oid:
        # Paging needs a sort order when the layer has no object ID (true of Jeffco's AMANDA layers).
        sortable = [f["name"] for f in fields if f.get("type") not in ("esriFieldTypeGeometry", "esriFieldTypeBlob")]
        if sortable:
            params["orderByFields"] = ",".join(sortable[:3])
        else:
            supports_paging = False
    if envelope:
        xmin, ymin, xmax, ymax = envelope
        params.update(
            geometry=f"{xmin},{ymin},{xmax},{ymax}",
            geometryType="esriGeometryEnvelope",
            inSR=4326,
            spatialRel="esriSpatialRelIntersects",
        )
    offset = 0
    seen = 0
    while True:
        p = dict(params)
        if supports_paging:
            p.update(resultOffset=offset, resultRecordCount=page)
        data = get_json(client, layer_url.rstrip("/") + "/query", p)
        feats = data.get("features") or []
        for f in feats:
            yield f
            seen += 1
            if seen >= max_features:
                return
        if not supports_paging or not feats or not data.get("exceededTransferLimit"):
            return
        offset += len(feats)


def walk_services(client: httpx.Client, root: str, max_services: int = 400) -> Iterator[tuple[str, dict]]:
    """Yield (service_url, service_json) for every MapServer/FeatureServer under a services root."""
    root = root.rstrip("/")
    queue = [root]
    count = 0
    while queue:
        url = queue.pop(0)
        try:
            d = get_json(client, url)
        except (httpx.HTTPError, ArcGISError) as e:
            log.warning("skip %s: %s", url, e)
            continue
        for folder in d.get("folders") or []:
            queue.append(f"{root}/{folder}")
        for svc in d.get("services") or []:
            if svc.get("type") not in ("MapServer", "FeatureServer"):
                continue
            surl = f"{root}/{svc['name']}/{svc['type']}"
            try:
                yield surl, get_json(client, surl)
            except (httpx.HTTPError, ArcGISError) as e:
                log.warning("skip %s: %s", surl, e)
            count += 1
            if count >= max_services:
                return
