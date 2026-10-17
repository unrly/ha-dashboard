"""Web server: the map page plus a small JSON API."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import area as area_mod
from .config import load_settings
from .scan import ScanState, start_background, summarize_case
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    settings = load_settings()
    store = Store(settings.data_dir / "notices.db")
    state = ScanState()
    app = FastAPI(title="Jeffco Notice Map")
    app.state.settings, app.state.store, app.state.scan = settings, store, state

    @app.get("/api/status")
    def status():
        return {
            "zip": settings.area.zip,
            "center": settings.area.center,
            "last_scan": store.last_scan(),
            "scan": state.snapshot(),
            "ai_enabled": settings.ai.enabled,
            "layer_url": settings.source.layer_url or store.get("auto_layer_url"),
        }

    @app.get("/api/area")
    def get_area():
        try:
            return area_mod.load_area(settings)
        except Exception as e:
            raise HTTPException(404, str(e))

    @app.get("/api/cases")
    def get_cases():
        new_ids = set(json.loads(store.get("new_ids") or "[]"))
        out = []
        for c in store.cases(settings.source.keep_closed_days):
            c.pop("raw", None)
            c["is_new"] = c["id"] in new_ids
            out.append(c)
        return out

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str):
        c = store.case(case_id)
        if not c:
            raise HTTPException(404, "No such case")
        return c

    @app.post("/api/scan")
    def scan():
        started = start_background(settings, store, state)
        return {"started": started, **state.snapshot()}

    @app.get("/api/scan")
    def scan_status():
        return state.snapshot()

    @app.post("/api/cases/{case_id}/summarize")
    def summarize(case_id: str, force: bool = False):
        if not settings.ai.enabled:
            raise HTTPException(400, "Plan summaries are turned off in config.yaml")
        try:
            return summarize_case(settings, store, case_id, force=force)
        except KeyError:
            raise HTTPException(404, "No such case")
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.get("/files/{case_id}/{idx}")
    def cached_doc(case_id: str, idx: int):
        """Serve the locally cached copy of a case PDF (downloaded when the case was summarized)."""
        c = store.case(case_id)
        if not c or idx >= len(c["docs"]):
            raise HTTPException(404)
        url = c["docs"][idx]["url"]
        p = settings.data_dir / "docs" / c["id"] / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
        if not p.exists():
            raise HTTPException(404, "Not downloaded yet")
        return FileResponse(p, media_type="application/pdf")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()
