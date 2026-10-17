"""Settings, loaded from config.yaml (optional) with environment overrides."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class AreaConfig(BaseModel):
    # "zcta" looks up the ZIP boundary from the Census; "radius" uses a circle; "geojson" reads a file.
    mode: str = "zcta"
    zip: str = "80128"
    center: tuple[float, float] = (39.5866, -105.0850)  # lat, lon (used for radius mode and map start)
    radius_km: float = 6.0
    # Also include notices this close to the outside of the boundary.
    buffer_km: float = 1.0
    geojson_path: Optional[str] = None
    # Census TIGERweb service that holds ZIP Code Tabulation Areas. The layer is found by name.
    zcta_service: str = (
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer"
    )


class SourceConfig(BaseModel):
    # County ArcGIS server roots, searched by `python -m app.discover` and by auto-discovery.
    gis_roots: list[str] = Field(
        default_factory=lambda: [
            "https://gisportal.jeffco.us/server2/rest/services",
            "https://gisportal.jeffco.us/server/rest/services",
        ]
    )
    # The county's AMANDA development review service: one layer per case type (Rezoning, Special Use, ...).
    service_url: Optional[str] = (
        "https://gisportal.jeffco.us/server/rest/services/PZ_Amanda7_Development_Review/MapServer"
    )
    # Or a single layer, e.g. ".../MapServer/7". If both are empty the first scan searches the GIS server.
    layer_url: Optional[str] = None
    # Extra filter applied on the server, e.g. "FOLDERTYPE = 'RZ'".
    where: str = "1=1"
    # Skip cases filed longer ago than this; the county service still lists open cases from decades ago.
    max_age_years: float = 3
    # County posts for high interest cases: plain-language summaries, case managers, document folders.
    news_list_url: Optional[str] = "https://www.jeffco.us/CivicAlerts.aspx?CID=64"
    # Map our fields to the layer's field names. Anything left out is guessed from the field names.
    fields: dict[str, str] = Field(default_factory=dict)
    # Optional case page, used to find PDF links. {case} is replaced with the case number.
    case_page_template: Optional[str] = None
    # Keep closed/approved cases on the map this many days after they were last seen as active.
    keep_closed_days: int = 90


class AIConfig(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    # Interpret plans for every case during a scan (costs more) or only when you open a case.
    interpret_on_scan: bool = False
    max_docs_per_case: int = 6
    max_doc_mb: float = 25.0


class Settings(BaseModel):
    area: AreaConfig = Field(default_factory=AreaConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    data_dir: Path = ROOT / "data"
    user_agent: str = "jeffco-notice-map/1.0 (personal, on-demand)"
    http_timeout: float = 45.0


def load_settings(path: Optional[Path] = None) -> Settings:
    path = path or Path(os.environ.get("NOTICE_MAP_CONFIG", ROOT / "config.yaml"))
    raw: dict = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text()) or {}
    s = Settings(**raw)
    if os.environ.get("NOTICE_MAP_DATA"):
        s.data_dir = Path(os.environ["NOTICE_MAP_DATA"])
    if os.environ.get("NOTICE_MAP_LAYER_URL"):
        s.source.layer_url = os.environ["NOTICE_MAP_LAYER_URL"]
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
