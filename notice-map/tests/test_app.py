import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import interpret, scan
from app.cases import Case
from app.config import Settings
from app.store import Store
from tests import fake_county as fc


@pytest.fixture
def env(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path)
    s.area.zcta_service = fc.ZCTA_SVC
    s.area.buffer_km = 0
    s.source.gis_roots = [fc.ROOT]
    s.source.service_url = None  # these tests use the single-layer discovery path
    s.source.news_list_url = None
    monkeypatch.setattr(scan, "http_client", lambda settings: fc.client())
    return s, Store(tmp_path / "t.db"), scan.ScanState()


def test_full_scan_discovers_layer_filters_and_merges(env):
    s, store, state = env
    r = scan.run_scan(s, store, state)
    assert r["found"] == 4 and r["kept"] == 2 and r["new"] == 0  # first scan marks nothing as new
    assert store.get("auto_layer_url") == fc.LAYER  # picked over "Case Labels" and "Zoning"
    rows = {c["id"]: c for c in store.cases(90)}
    assert set(rows) == {"26-100001RZ", "26-100002SU"}
    assert len(rows["26-100001RZ"]["parcel"]) == 2  # two parcels merged
    assert rows["26-100002SU"]["status_kind"] == "closed"
    titles = [d["title"] for d in rows["26-100001RZ"]["docs"]]
    assert titles == ["Application narrative", "Site plan"]
    # The ZIP boundary is cached for the map.
    assert (s.data_dir / "zcta_80128.geojson").exists()


def test_second_scan_flags_new_and_deactivates_missing(env, monkeypatch):
    s, store, state = env
    scan.run_scan(s, store, state)
    extra = {"attributes": {"CASE_NUMBER": "26-100009PF", "CASE_NAME": "New Plat", "STATUS": "In Review"},
             "geometry": fc.parcel(-105.10, 39.59)}
    monkeypatch.setattr(fc, "FEATURES", [fc.FEATURES[0], extra])
    r = scan.run_scan(s, store, state)
    assert r["new"] == 1
    assert json.loads(store.get("new_ids")) == ["26-100009PF"]
    su = store.case("26-100002SU")
    assert su["active"] is False


def test_missing_case_field_gives_clear_error(env):
    s, store, state = env
    s.source.layer_url = f"{fc.ROOT}/Zoning/MapServer/0"
    with pytest.raises(RuntimeError, match="case number"):
        scan.run_scan(s, store, state)
    assert store.last_scan()["ok"] == 0


# ---------- Claude summary (fake client, no network)
class FakeStream:
    def __init__(self, calls, payload):
        self.calls, self.payload = calls, payload

    @contextmanager
    def stream(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                              content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])
        yield SimpleNamespace(get_final_message=lambda: msg)


SUMMARY = {"summary": "24 townhomes.", "request": "A-1 to PD", "key_numbers": [{"value": "24", "label": "units", "source": "p.1"}],
           "lot": {"width_m": 90, "depth_m": 100}, "buildings": [{"label": "A", "x_m": 0, "y_m": 0, "width_m": 48, "depth_m": 12, "floors": 2}],
           "watch": [], "confidence": "medium", "notes": ""}


def test_interpret_sends_pdfs_and_parses(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    calls = []
    client = SimpleNamespace(beta=SimpleNamespace(messages=FakeStream(calls, SUMMARY)))
    case = Case(id="x", case_number="26-1RZ", name="Test")
    out = interpret.interpret(client, Settings().ai, case, [("Site plan", pdf)])
    assert out["summary"] == "24 townhomes." and out["documents"] == ["Site plan"]
    req = calls[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"
    blocks = req["messages"][0]["content"]
    assert blocks[0]["type"] == "document" and blocks[0]["source"]["media_type"] == "application/pdf"
    assert blocks[-1]["type"] == "text"


def test_summary_is_cached_until_documents_change(env, monkeypatch):
    s, store, state = env
    scan.run_scan(s, store, state)
    calls = []
    monkeypatch.setattr(interpret, "make_client",
                        lambda: SimpleNamespace(beta=SimpleNamespace(messages=FakeStream(calls, SUMMARY))))
    monkeypatch.setattr(scan.docs, "download", lambda *a, **k: None)
    scan.summarize_case(s, store, "26-100001RZ")
    scan.summarize_case(s, store, "26-100001RZ")
    assert len(calls) == 1
    assert store.case("26-100001RZ")["summary"]["request"] == "A-1 to PD"
    scan.summarize_case(s, store, "26-100001RZ", force=True)
    assert len(calls) == 2


# ---------- HTTP API
def test_api_lists_cases(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    s, store, state = env
    scan.run_scan(s, store, state)
    monkeypatch.setenv("NOTICE_MAP_DATA", str(tmp_path))
    monkeypatch.setenv("NOTICE_MAP_CONFIG", str(tmp_path / "none.yaml"))
    store.db.close()
    import os
    os.replace(tmp_path / "t.db", tmp_path / "notices.db")
    from app.main import create_app
    c = TestClient(create_app())
    body = c.get("/api/cases").json()
    assert {x["case_number"] for x in body} == {"26-100001RZ", "26-100002SU"}
    assert "raw" not in body[0]
    assert c.get("/api/cases/26-100001RZ").json()["raw"]["PLANNER"] == "Pat Planner"
    assert c.get("/api/status").json()["zip"] == "80128"
    assert c.get("/api/area").json()["geometry"]["type"] == "Polygon"
    assert c.get("/").status_code == 200


# ---------- the county's real data shape (AMANDA service: one layer per case type, no object IDs)
def test_amanda_service_scan_with_posts_and_buffer(env):
    s, store, state = env
    s.source.service_url = fc.AMANDA
    s.source.news_list_url = "https://news.example/list"
    r = scan.run_scan(s, store, state)
    assert r["kept"] == 2  # the edge case sits outside the ZIP
    rz = store.case("26-103768RZ")
    assert rz["type_code"] == "RZ" and rz["layer"] == "Rezoning"
    assert rz["raw"]["parcel_count"] == 2
    assert rz["lat"] == pytest.approx(39.581) and rz["lon"] == pytest.approx(-105.081)
    # Enriched from the county's post.
    assert rz["name"] == "6859 S Iris Street (Dutch Creek) Rezoning"
    assert rz["planner"] == "Pat Planner"
    assert "47 single-family homes" in rz["description"]
    assert rz["dates"]["pc_hearing"] == "2099-11-12" and rz["dates"]["bcc_hearing"] == "2099-12-09"
    assert rz["status_kind"] == "hearing"
    assert [d["kind"] for d in rz["docs"]] == ["folder", "page"]
    sd = store.case("26-110298SD")
    assert sd["name"] == "10482 West Toller Drive Site Development" and sd["type_code"] == "SDP"

    s.area.buffer_km = 1.0
    r = scan.run_scan(s, store, state)
    assert r["kept"] == 3 and store.case("26-110299SD")["name"] == "Edge Business Park"
