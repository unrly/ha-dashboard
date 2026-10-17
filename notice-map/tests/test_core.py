import datetime as dt

import pytest

from app import arcgis, area, cases, docs, geo
from tests import fake_county as fc


# ---------- geometry
def test_point_in_polygon_with_hole():
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    g = {"type": "Polygon", "coordinates": [outer, hole]}
    assert geo.point_in_geometry(1, 1, g)
    assert not geo.point_in_geometry(5, 5, g)
    assert not geo.point_in_geometry(11, 5, g)


def test_esri_parcel_centroid():
    lon, lat = geo.esri_to_point(fc.parcel(-105.08, 39.58))
    assert lon == pytest.approx(-105.08) and lat == pytest.approx(39.58)
    assert geo.esri_to_point({"x": -105.0, "y": 39.5}) == (-105.0, 39.5)
    assert geo.esri_to_point(None) is None


def test_circle_contains_center():
    g = geo.circle(39.58, -105.08, 5)
    assert geo.point_in_geometry(-105.08, 39.58, g)
    assert not geo.point_in_geometry(-105.20, 39.58, g)


# ---------- case mapping
def test_resolve_fields_guesses_and_overrides():
    names = ["OBJECTID", "CASE_NUMBER", "PROJECT_NAME", "Case_Type", "STATUS_DESC", "SITE_ADDR", "PC_HEARING"]
    m = cases.resolve_fields(names, {"planner": "STAFF_X"})
    assert m["case_number"] == "CASE_NUMBER"
    assert m["name"] == "PROJECT_NAME"
    assert m["case_type"] == "Case_Type"
    assert m["status"] == "STATUS_DESC"
    assert m["address"] == "SITE_ADDR"
    assert m["planner"] == "STAFF_X"
    assert m["pc_hearing"] == "PC_HEARING"


@pytest.mark.parametrize("num,text,code", [
    ("26-118402RZ", "", "RZ"), ("26-1SU", "", "SU"), ("26-1SP", "", "SDP"), ("X", "Minor Subdivision", "PF"),
    ("X", "Variance request", "BOA"), ("X", "Pre-Application Meeting", "PA"), ("X", "Something", "OTHER"),
])
def test_normalize_type(num, text, code):
    assert cases.normalize_type(num, text) == code


def test_status_kind():
    t = dt.date(2026, 10, 17)
    assert cases.status_kind("Approved with conditions", {}, t) == "closed"
    assert cases.status_kind("In review", {"pc_hearing": "2026-11-12"}, t) == "hearing"
    assert cases.status_kind("In review", {"pc_hearing": "2026-01-01"}, t) == "review"


def test_from_feature_reads_dates_links_and_type():
    f = fc.FEATURES[0]
    fmap = cases.resolve_fields(list(f["attributes"]), {})
    c = cases.from_feature(f, fmap, (-105.08, 39.58), None)
    assert c.case_number == "26-100001RZ" and c.type_code == "RZ" and c.type_label == "Rezoning"
    assert c.dates["pc_hearing"] == "2100-01-01"
    assert c.links[0]["url"] == "https://county.example/cases/26-100001RZ"
    assert c.status_kind == "hearing"
    assert cases.from_feature({"attributes": {}}, fmap, None, None) is None


# ---------- ArcGIS client
def test_query_follows_pagination():
    with fc.client() as c:
        feats = list(arcgis.query_features(c, fc.LAYER))
    assert len(feats) == len(fc.FEATURES)


def test_arcgis_error_object_raises():
    with fc.client() as c, pytest.raises(arcgis.ArcGISError, match="Invalid query"):
        arcgis.get_json(c, "https://gis.example/error")


def test_walk_services_finds_nested():
    with fc.client() as c:
        urls = [u for u, _ in arcgis.walk_services(c, fc.ROOT)]
    assert f"{fc.ROOT}/Planning/PZ_Cases/MapServer" in urls


# ---------- area
def test_fetch_zcta_picks_boundary_layer_and_field():
    with fc.client() as c:
        g = area.fetch_zcta(c, fc.ZCTA_SVC, "80128")
    assert g["type"] == "Polygon"
    assert geo.point_in_geometry(-105.08, 39.58, g)


def test_esri_holes_attach_to_outer_ring():
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]  # clockwise
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]  # counter-clockwise
    g = area._esri_polygon_to_geojson({"rings": [outer, hole]})
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2


# ---------- documents
def test_doc_links_in_html():
    links = docs.doc_links_in_html(fc.CASE_PAGE, "https://county.example/cases/x")
    assert [l["url"] for l in links] == ["https://county.example/DocumentCenter/View/111/Narrative",
                                         "https://county.example/files/site-plan.pdf"]
    assert links[0]["title"] == "Application narrative"
