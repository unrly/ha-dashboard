"""Turn raw ArcGIS case records into a consistent Case shape."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

# Our field -> candidate field names (upper case). First match wins; then fuzzy contains-matching.
FIELD_GUESSES: dict[str, list[str]] = {
    "case_number": ["CASE_NUMBER", "CASENUMBER", "CASE_NO", "CASENO", "CASE_NUM", "CASE_ID", "CASEID", "CASE",
                    "RECORD_ID", "RECORDID", "PERMIT_NUMBER", "APP_NUMBER"],
    "name": ["CASE_NAME", "CASENAME", "PROJECT_NAME", "PROJECTNAME", "PROJECT", "NAME", "TITLE"],
    "case_type": ["CASE_TYPE", "CASETYPE", "TYPE", "APPLICATION_TYPE", "APP_TYPE", "RECORD_TYPE", "PROCESS"],
    "status": ["STATUS", "CASE_STATUS", "CASESTATUS", "STAGE", "PHASE"],
    "address": ["ADDRESS", "SITE_ADDRESS", "SITEADDRESS", "LOCATION", "PROPERTY_ADDRESS", "SITUS", "FULL_ADDRESS"],
    "description": ["DESCRIPTION", "DESC", "REQUEST", "PROPOSAL", "SUMMARY", "COMMENTS", "PURPOSE"],
    "planner": ["PLANNER", "CASE_PLANNER", "CASEPLANNER", "STAFF", "CONTACT", "ASSIGNED_TO"],
    "applicant": ["APPLICANT", "APPLICANT_NAME", "OWNER", "OWNER_NAME"],
    "acres": ["ACRES", "ACREAGE", "GIS_ACRES", "AREA_ACRES"],
    "url": ["URL", "LINK", "HYPERLINK", "WEBLINK", "CASE_URL", "DOCUMENTS", "DOC_LINK"],
    "pin": ["PIN", "PARCEL", "PARCEL_ID", "PARCELID", "SCHEDULE", "APN"],
    "filed": ["DATE_FILED", "FILED", "SUBMITTED", "APPLIED", "OPEN_DATE", "RECEIVED", "DATE_RECEIVED"],
    "pc_hearing": ["PC_HEARING", "PC_DATE", "PLANNING_COMMISSION", "PC_HEARING_DATE"],
    "bcc_hearing": ["BCC_HEARING", "BCC_DATE", "BOCC_HEARING", "BCC_HEARING_DATE"],
    "boa_hearing": ["BOA_HEARING", "BOA_DATE"],
}

# Case number suffixes used by Jeffco P&Z (e.g. 26-118402RZ) and keywords in the type text.
TYPE_CODES = {
    "RZ": "RZ", "ZC": "RZ", "SU": "SU", "SP": "SDP", "SDP": "SDP", "PF": "PF", "PP": "PF", "FP": "PF", "MS": "PF",
    "SB": "PF", "EX": "PF", "VA": "BOA", "BOA": "BOA", "SE": "BOA", "PA": "PA", "LE": "SU",
}
TYPE_WORDS = [
    ("rezon", "RZ"), ("official development plan", "RZ"), ("planned development", "RZ"),
    ("special use", "SU"), ("location and extent", "SU"), ("site development", "SDP"), ("site plan", "SDP"),
    ("plat", "PF"), ("subdivision", "PF"), ("exemption", "PF"), ("lot line", "PF"),
    ("variance", "BOA"), ("adjustment", "BOA"), ("special exception", "BOA"), ("pre-app", "PA"), ("pre app", "PA"),
]
TYPE_LABELS = {"RZ": "Rezoning", "SU": "Special use", "SDP": "Site plan", "PF": "Plat",
               "BOA": "Variance", "PA": "Pre-application", "OTHER": "Other"}

CLOSED_WORDS = ("approved", "denied", "withdrawn", "closed", "complete", "recorded", "expired", "void")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


class Case(BaseModel):
    id: str
    case_number: str
    name: str = ""
    type_code: str = "OTHER"
    type_label: str = "Other"
    case_type_raw: str = ""
    status: str = ""
    status_kind: str = "review"  # review | hearing | closed
    address: str = ""
    description: str = ""
    planner: str = ""
    applicant: str = ""
    acres: Optional[float] = None
    pin: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    parcel: Optional[list] = None  # list of rings [[lon, lat], ...]
    dates: dict[str, str] = Field(default_factory=dict)
    links: list[dict] = Field(default_factory=list)  # {title, url}
    raw: dict[str, Any] = Field(default_factory=dict)


def resolve_fields(field_names: list[str], overrides: dict[str, str]) -> dict[str, str]:
    """Map our field keys to the layer's real field names."""
    upper = {n.upper(): n for n in field_names}
    out: dict[str, str] = {}
    for key, cand in FIELD_GUESSES.items():
        if key in overrides and overrides[key]:
            out[key] = overrides[key]
            continue
        hit = next((upper[c] for c in cand if c in upper), None)
        if hit is None and key in ("case_number", "case_type", "status", "address", "planner"):
            # Fuzzy: e.g. "CASE_NUMBER_TXT", "SITE_ADDR".
            stem = {"case_number": "CASE", "case_type": "TYPE", "status": "STATUS",
                    "address": "ADDR", "planner": "PLANNER"}[key]
            hit = next((orig for up, orig in upper.items() if stem in up and up not in out.values()), None)
        if hit:
            out[key] = hit
    return out


def normalize_type(case_number: str, type_text: str) -> str:
    m = re.search(r"\d([A-Z]{2,3})\s*$", (case_number or "").upper())
    if m and m.group(1) in TYPE_CODES:
        return TYPE_CODES[m.group(1)]
    t = (type_text or "").lower()
    for word, code in TYPE_WORDS:
        if word in t:
            return code
    tt = (type_text or "").strip().upper()
    return TYPE_CODES.get(tt, "OTHER")


def to_date(v: Any) -> Optional[str]:
    """ArcGIS dates come as epoch milliseconds; strings are passed through."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000, tz=dt.timezone.utc).date().isoformat()
    return str(v)


def status_kind(status: str, dates: dict[str, str], today: Optional[dt.date] = None) -> str:
    s = (status or "").lower()
    if any(w in s for w in CLOSED_WORDS):
        return "closed"
    today = today or dt.date.today()
    for k in ("pc_hearing", "bcc_hearing", "boa_hearing"):
        d = dates.get(k)
        try:
            if d and dt.date.fromisoformat(d[:10]) >= today:
                return "hearing"
        except ValueError:
            pass
    if "hearing" in s or "scheduled" in s:
        return "hearing"
    return "review"


def from_feature(feature: dict, fmap: dict[str, str], point: Optional[tuple[float, float]],
                 rings: Optional[list]) -> Optional[Case]:
    a = feature.get("attributes") or {}

    def g(key: str) -> Any:
        f = fmap.get(key)
        return a.get(f) if f else None

    num = str(g("case_number") or "").strip()
    if not num:
        return None
    dates = {k: d for k in ("filed", "pc_hearing", "bcc_hearing", "boa_hearing") if (d := to_date(g(k)))}
    type_raw = str(g("case_type") or "")
    code = normalize_type(num, type_raw)
    links = []
    for k, v in a.items():
        if isinstance(v, str):
            for u in URL_RE.findall(v):
                links.append({"title": k.replace("_", " ").title(), "url": u})
    acres = g("acres")
    try:
        acres = round(float(acres), 2) if acres not in (None, "") else None
    except (TypeError, ValueError):
        acres = None
    c = Case(
        id=re.sub(r"[^A-Za-z0-9_-]", "_", num),
        case_number=num,
        name=str(g("name") or "").strip(),
        type_code=code,
        type_label=TYPE_LABELS[code],
        case_type_raw=type_raw,
        status=str(g("status") or "").strip(),
        address=str(g("address") or "").strip(),
        description=str(g("description") or "").strip(),
        planner=str(g("planner") or "").strip(),
        applicant=str(g("applicant") or "").strip(),
        acres=acres,
        pin=str(g("pin") or "").strip(),
        lat=point[1] if point else None,
        lon=point[0] if point else None,
        parcel=rings,
        dates=dates,
        links=links,
        raw={k: v for k, v in a.items() if v not in (None, "")},
    )
    c.status_kind = status_kind(c.status, dates)
    if not c.name:
        c.name = c.description[:60] or c.address or c.type_label
    return c
