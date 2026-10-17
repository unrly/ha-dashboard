"""Small geometry helpers. Coordinates are (lon, lat) like GeoJSON."""
from __future__ import annotations

import math
from typing import Iterable

Ring = list[list[float]]


def _point_in_ring(x: float, y: float, ring: Ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def polygons_of(geometry: dict) -> list[list[Ring]]:
    """Return a list of polygons (each a list of rings) from a GeoJSON Polygon/MultiPolygon."""
    t = geometry.get("type")
    if t == "Polygon":
        return [geometry["coordinates"]]
    if t == "MultiPolygon":
        return list(geometry["coordinates"])
    raise ValueError(f"unsupported geometry type: {t}")


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    for poly in polygons_of(geometry):
        if not poly:
            continue
        if _point_in_ring(lon, lat, poly[0]) and not any(_point_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def bbox(geometry: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for poly in polygons_of(geometry):
        for ring in poly:
            for x, y, *_ in ring:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def ring_centroid(ring: Ring) -> tuple[float, float]:
    """Area-weighted centroid of one ring; falls back to the vertex average for degenerate rings."""
    a = cx = cy = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-18:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    a *= 0.5
    return cx / (6 * a), cy / (6 * a)


def esri_to_point(geom: dict | None) -> tuple[float, float] | None:
    """Pick a representative (lon, lat) from an Esri JSON geometry (point, polygon, polyline)."""
    if not geom:
        return None
    if "x" in geom and "y" in geom and geom["x"] is not None:
        return float(geom["x"]), float(geom["y"])
    if geom.get("rings"):
        # The largest ring is usually the outer boundary of the parcel.
        ring = max(geom["rings"], key=lambda r: abs(_ring_area(r)))
        return ring_centroid(ring)
    if geom.get("paths"):
        pts = [p for path in geom["paths"] for p in path]
        return pts[len(pts) // 2][0], pts[len(pts) // 2][1]
    if geom.get("points"):
        p = geom["points"][0]
        return p[0], p[1]
    return None


def esri_rings(geom: dict | None) -> list[Ring] | None:
    if geom and geom.get("rings"):
        return [[[p[0], p[1]] for p in r] for r in geom["rings"]]
    return None


def _ring_area(ring: Ring) -> float:
    return 0.5 * sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))


def circle(lat: float, lon: float, radius_km: float, steps: int = 72) -> dict:
    pts = []
    for i in range(steps + 1):
        a = 2 * math.pi * i / steps
        dlat = (radius_km / 111.32) * math.sin(a)
        dlon = (radius_km / (111.32 * math.cos(math.radians(lat)))) * math.cos(a)
        pts.append([lon + dlon, lat + dlat])
    return {"type": "Polygon", "coordinates": [pts]}


def distance_to_boundary_km(lon: float, lat: float, geometry: dict) -> float:
    """Shortest distance from a point to the geometry's edges, in km (flat-earth approximation)."""
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    px, py = lon * kx, lat * ky
    best = float("inf")
    for poly in polygons_of(geometry):
        for ring in poly:
            for i in range(len(ring) - 1):
                ax, ay = ring[i][0] * kx, ring[i][1] * ky
                bx, by = ring[i + 1][0] * kx, ring[i + 1][1] * ky
                dx, dy = bx - ax, by - ay
                L = dx * dx + dy * dy
                t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
                best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def within(lon: float, lat: float, geometry: dict, buffer_km: float = 0.0) -> bool:
    if point_in_geometry(lon, lat, geometry):
        return True
    return buffer_km > 0 and distance_to_boundary_km(lon, lat, geometry) <= buffer_km


def expand_bbox(b: tuple[float, float, float, float], km: float) -> tuple[float, float, float, float]:
    dlat = km / 110.57
    dlon = km / (111.32 * math.cos(math.radians((b[1] + b[3]) / 2)))
    return b[0] - dlon, b[1] - dlat, b[2] + dlon, b[3] + dlat


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def flatten(items: Iterable[Iterable]) -> list:
    return [x for it in items for x in it]
