"""A fake ArcGIS server for tests. Every record here is made up."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = "https://gis.example/server/rest/services"
LAYER = f"{ROOT}/Planning/PZ_Cases/MapServer/2"
ZCTA_SVC = "https://census.example/arcgis/rest/services/TIGERweb/ZCTA/MapServer"

# A square "ZIP" around (39.58, -105.08). Esri outer rings are clockwise.
ZIP_RING = [[-105.12, 39.56], [-105.12, 39.60], [-105.04, 39.60], [-105.04, 39.56], [-105.12, 39.56]]


def parcel(lon, lat, d=0.001):
    return {"rings": [[[lon - d, lat - d], [lon - d, lat + d], [lon + d, lat + d], [lon + d, lat - d], [lon - d, lat - d]]]}


FEATURES = [
    {"attributes": {"CASE_NUMBER": "26-100001RZ", "CASE_NAME": "Test Townhomes", "CASE_TYPE": "Rezoning",
                    "STATUS": "In Review", "ADDRESS": "100 Test St", "PLANNER": "Pat Planner", "ACRES": 3.2,
                    "PC_HEARING": 4102444800000, "CASE_URL": "https://county.example/cases/26-100001RZ"},
     "geometry": parcel(-105.08, 39.58)},
    {"attributes": {"CASE_NUMBER": "26-100002SU", "CASE_NAME": "Test Tower", "CASE_TYPE": "Special Use",
                    "STATUS": "Approved", "ADDRESS": "200 Test St"},
     "geometry": parcel(-105.06, 39.57)},
    # Outside the ZIP square: must be dropped.
    {"attributes": {"CASE_NUMBER": "26-100003SDP", "CASE_NAME": "Far Away Plan", "CASE_TYPE": "Site Plan",
                    "STATUS": "In Review"},
     "geometry": parcel(-105.20, 39.70)},
    # Same case on a second parcel: must merge into the first.
    {"attributes": {"CASE_NUMBER": "26-100001RZ", "CASE_NAME": "Test Townhomes", "CASE_TYPE": "Rezoning",
                    "STATUS": "In Review"},
     "geometry": parcel(-105.079, 39.581)},
]

AMANDA = "https://gis.example/server/rest/services/PZ_Amanda7_Development_Review/MapServer"
AMANDA_FIELDS = [{"name": n, "type": t} for n, t in [
    ("FOLDERRSN", "esriFieldTypeInteger"), ("INDATE", "esriFieldTypeDate"), ("FINALDATE", "esriFieldTypeDate"),
    ("FOLDERTYPE", "esriFieldTypeString"), ("STATUSCODE", "esriFieldTypeInteger"),
    ("PROPERTYNAME", "esriFieldTypeString"), ("WORKDESC", "esriFieldTypeString"), ("FOLDERDESC", "esriFieldTypeString"),
    ("FOLDERYEAR", "esriFieldTypeString"), ("FOLDERSEQUENCE", "esriFieldTypeString"), ("Shape", "esriFieldTypeGeometry")]]


def amanda(y, seq, t, name, desc, lon, lat, final=None):
    return {"attributes": {"FOLDERRSN": int(seq), "INDATE": 1767225600000, "FINALDATE": final, "FOLDERTYPE": t,
                           "STATUSCODE": 60020, "PROPERTYNAME": name, "WORKDESC": "Planned Development",
                           "FOLDERDESC": desc, "FOLDERYEAR": y, "FOLDERSEQUENCE": seq},
            "geometry": {"x": lon, "y": lat}}


AMANDA_LAYERS = {
    7: ("Rezoning", [amanda("26", "103768", "RZ", "6859 S Iris ST", "Rezoning from A-1 to PD", -105.080, 39.580),
                     amanda("26", "103768", "RZ", "6859 S Iris ST", "Rezoning from A-1 to PD", -105.082, 39.582)]),
    10: ("Site Development", [amanda("26", "110298", "SD", "10482 West Toller Drive", "SDP for industrial", -105.07, 39.57),
                              # 0.5 km outside the east edge of the ZIP square: kept only with a buffer.
                              amanda("26", "110299", "SD", "Edge Business Park", "SDP", -105.0342, 39.58)]),
}

NEWS_LIST = """<html><body><a href="/m/newsflash/Home/Detail/2621">6859 S Iris Street (Dutch Creek) Rezoning</a>
<a href="/m/newsflash/Home/Detail/2665">Weigh in on water supply</a></body></html>"""
NEWS_POST = """<html><body><nav>Location Services</nav><h1>6859 S Iris Street (Dutch Creek) Rezoning</h1>
<p>Posted on May 14, 2026</p><h3>Location</h3><p>6859 S Iris Street and PIN 300503777</p>
<h3>Case Summary</h3><p>The application proposes to Rezone from A-1 to PD to allow for up to 47 single-family homes.</p>
<h3>Case Number</h3><p>26-103768RZ</p><h3>Documents</h3>
<p><a href="https://county.sharepoint.com/:f:/s/AmandaDocLib_Public/abc">View the current referral documents.</a></p>
<h3>Case Manager</h3><p>Pat Planner; <a href="mailto:x">email the Case Manager here</a></p>
<h3>Hearings</h3><p>The Planning Commission hearing is scheduled for November 12, 2099. The Board of County Commissioners hearing is December 9, 2099.</p>
<h3>Related News</h3><a href="/m/newsflash/Home/Detail/1">Other</a></body></html>"""

CASE_PAGE = """<html><body><h1>26-100001RZ</h1>
<a href="/DocumentCenter/View/111/Narrative">Application narrative</a>
<a href="/files/site-plan.pdf">Site plan</a>
<a href="/about">About</a></body></html>"""


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url).split("?")[0]
    q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}

    def js(obj):
        return httpx.Response(200, json=obj)

    if url == ROOT:
        return js({"folders": ["Planning"], "services": [{"name": "Zoning", "type": "MapServer"}]})
    if url == f"{ROOT}/Planning":
        return js({"services": [{"name": "Planning/PZ_Cases", "type": "MapServer"}]})
    if url == f"{ROOT}/Zoning/MapServer":
        return js({"layers": [{"id": 0, "name": "Zoning"}]})
    if url == f"{ROOT}/Planning/PZ_Cases/MapServer":
        return js({"layers": [{"id": 1, "name": "Case Labels"}, {"id": 2, "name": "Active Land Use Cases"}]})
    if url in (f"{ROOT}/Planning/PZ_Cases/MapServer/1", f"{ROOT}/Zoning/MapServer/0"):
        return js({"name": "x", "fields": [{"name": "OBJECTID"}], "geometryType": "esriGeometryPolygon"})
    if url == LAYER:
        return js({"name": "Active Land Use Cases", "geometryType": "esriGeometryPolygon", "maxRecordCount": 2,
                   "advancedQueryCapabilities": {"supportsPagination": True},
                   "fields": [{"name": n} for n in ["OBJECTID", "CASE_NUMBER", "CASE_NAME", "CASE_TYPE", "STATUS",
                                                     "ADDRESS", "PLANNER", "ACRES", "PC_HEARING", "CASE_URL"]]})
    if url == LAYER + "/query":
        off, n = int(q.get("resultOffset", 0)), int(q.get("resultRecordCount", 2))
        page = FEATURES[off:off + n]
        return js({"features": page, "exceededTransferLimit": off + n < len(FEATURES)})
    if url == ZCTA_SVC:
        return js({"layers": [{"id": 0, "name": "2020 ZIP Code Tabulation Areas Labels"},
                              {"id": 1, "name": "2020 ZIP Code Tabulation Areas"}]})
    if url == ZCTA_SVC + "/1":
        return js({"fields": [{"name": "GEOID"}, {"name": "ZCTA5"}], "maxRecordCount": 10})
    if url == ZCTA_SVC + "/1/query":
        assert q["where"] == "ZCTA5='80128'"
        return js({"features": [{"attributes": {"ZCTA5": "80128"}, "geometry": {"rings": [ZIP_RING]}}]})
    if url == "https://county.example/cases/26-100001RZ":
        return httpx.Response(200, text=CASE_PAGE, headers={"content-type": "text/html"})
    if url == AMANDA:
        return js({"layers": [{"id": k, "name": v[0]} for k, v in AMANDA_LAYERS.items()]})
    for k, (name, feats) in AMANDA_LAYERS.items():
        if url == f"{AMANDA}/{k}":
            return js({"name": name, "geometryType": "esriGeometryPoint", "maxRecordCount": 1,
                       "advancedQueryCapabilities": {"supportsPagination": True}, "fields": AMANDA_FIELDS})
        if url == f"{AMANDA}/{k}/query":
            if "resultOffset" in q and not q.get("orderByFields"):
                return js({"error": {"code": 400, "message": "Pagination request requires either orderBy field"}})
            assert "INDATE >= DATE '" in q["where"]
            off = int(q.get("resultOffset", 0))
            return js({"features": feats[off:off + 1], "exceededTransferLimit": off + 1 < len(feats)})
    if url == "https://news.example/list":
        return httpx.Response(200, text=NEWS_LIST, headers={"content-type": "text/html"})
    if url == "https://news.example/m/newsflash/Home/Detail/2621":
        return httpx.Response(200, text=NEWS_POST, headers={"content-type": "text/html"})
    if url == "https://news.example/m/newsflash/Home/Detail/2665":
        return httpx.Response(200, text="<html><p>Posted on Oct 1</p>No case here</html>", headers={"content-type": "text/html"})
    if url.endswith("/error"):
        return js({"error": {"code": 400, "message": "Invalid query"}})
    return httpx.Response(404, text="not found: " + url)


def client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def dumps(o):
    return json.dumps(o)
