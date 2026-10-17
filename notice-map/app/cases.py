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
    "name": ["CASE_NAME", "CASENAME", "PROJECT_NAME", "PROJECTNAME", "PROJECT", "PROPERTYNAME", "NAME", "TITLE"],
    "case_type": ["CASE_TYPE", "CASETYPE", "TYPE", "APPLICATION_TYPE", "APP_TYPE", "RECORD_TYPE", "PROCESS", "WORKDESC"],
    "status": ["STATUSDESC", "STATUS", "CASE_STATUS", "CASESTATUS", "STAGE", "PHASE"],
    "address": ["ADDRESS", "SITE_ADDRESS", "SITEADDRESS", "LOCATION", "PROPERTY_ADDRESS", "SITUS", "FULL_ADDRESS"],
    "description": ["DESCRIPTION", "DESC", "REQUEST", "PROPOSAL", "SUMMARY", "COMMENTS", "PURPOSE", "FOLDERDESC"],
    "planner": ["PLANNER", "CASE_PLANNER", "CASEPLANNER", "STAFF", "CONTACT", "ASSIGNED_TO"],
    "applicant": ["APPLICANT", "APPLICANT_NAME", "OWNER", "OWNER_NAME"],
    "acres": ["ACRES", "ACREAGE", "GIS_ACRES", "AREA_ACRES"],
    "url": ["URL", "LINK", "HYPERLINK", "WEBLINK", "CASE_URL", "DOCUMENTS", "DOC_LINK"],
    "pin": ["PIN", "PARCEL", "PARCEL_ID", "PARCELID", "SCHEDULE", "APN"],
    "filed": ["DATE_FILED", "FILED", "SUBMITTED", "APPLIED", "OPEN_DATE", "RECEIVED", "DATE_RECEIVED", "INDATE"],
    "final": ["FINALDATE", "FINAL_DATE", "CLOSED_DATE", "DATE_CLOSED"],
    "pc_hearing": ["PC_HEARING", "PC_DATE", "PLANNING_COMMISSION", "PC_HEARING_DATE"],
    "bcc_hearing": ["BCC_HEARING", "BCC_DATE", "BOCC_HEARING", "BCC_HEARING_DATE"],
    "boa_hearing": ["BOA_HEARING", "BOA_DATE"],
}

# Case number suffixes used by Jeffco P&Z (e.g. 26-103768RZ) and keywords in the type text.
TYPE_CODES = {
    "RZ": "RZ", "ZC": "RZ", "SU": "SU", "SD": "SDP", "SP": "SDP", "SDP": "SDP", "SA": "SDP", "PF": "PF", "PP": "PF",
    "FP": "PF", "MS": "PF", "SB": "PF", "EX": "PF", "RC": "PF", "VC": "PF", "VA": "BOA", "BOA": "BOA", "SE": "BOA",
    "PA": "PA", "LE": "SU", "MA": "MA", "MV": "MA", "WR": "MA", "WV": "MA",
}
# Layer names in the county's AMANDA "Development Review" map service.
LAYER_TYPES = [
    ("rezon", "RZ"), ("special use", "SU"), ("site development", "SDP"), ("site approval", "SDP"),
    ("plat", "PF"), ("rural cluster", "PF"), ("vacation", "PF"), ("minor amendment", "MA"), ("minor variation", "MA"),
    ("waiver", "MA"), ("preliminary application", "PA"), ("variance", "BOA"),
]
TYPE_WORDS = [
    ("rezon", "RZ"), ("official development plan", "RZ"), ("planned development", "RZ"),
    ("special use", "SU"), ("location and extent", "SU"), ("site development", "SDP"), ("site plan", "SDP"),
    ("plat", "PF"), ("subdivision", "PF"), ("exemption", "PF"), ("lot line", "PF"),
    ("variance", "BOA"), ("adjustment", "BOA"), ("special exception", "BOA"), ("pre-app", "PA"), ("pre app", "PA"),
]
TYPE_LABELS = {"RZ": "Rezoning", "SU": "Special use", "SDP": "Site plan", "PF": "Plat",
               "MA": "Minor change", "BOA": "Variance", "PA": "Pre-application", "OTHER": "Other"}

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
    layer: str = ""  # county layer the record came from, e.g. "Site Development"
    hearings_note: str = ""
    news_url: str = ""
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
            # Skip numeric code fields like STATUSCODE: they have no public labels.
            hit = next((orig for up, orig in upper.items()
                        if stem in up and not up.endswith("CODE") and orig not in out.values()), None)
        if hit:
            out[key] = hit
    return out


def normalize_type(case_number: str, type_text: str, layer_name: str = "") -> str:
    ln = layer_name.lower()
    for word, code in LAYER_TYPES:
        if word in ln:
            return code
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
    if any(w in s for w in CLOSED_WORDS) or dates.get("final"):
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


def amanda_case_number(a: dict) -> str:
    """AMANDA records carry the case number in parts: FOLDERYEAR-FOLDERSEQUENCE + FOLDERTYPE (26-103768RZ)."""
    y, seq, t = a.get("FOLDERYEAR"), a.get("FOLDERSEQUENCE"), a.get("FOLDERTYPE")
    return f"{y}-{seq}{t}" if y and seq and t else ""


def from_feature(feature: dict, fmap: dict[str, str], point: Optional[tuple[float, float]],
                 rings: Optional[list], layer_name: str = "") -> Optional[Case]:
    a = feature.get("attributes") or {}

    def g(key: str) -> Any:
        f = fmap.get(key)
        return a.get(f) if f else None

    num = str(g("case_number") or "").strip() or amanda_case_number(a)
    if not num:
        return None
    dates = {k: d for k in ("filed", "final", "pc_hearing", "bcc_hearing", "boa_hearing") if (d := to_date(g(k)))}
    type_raw = str(g("case_type") or "")
    code = normalize_type(num, type_raw, layer_name)
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
        layer=layer_name,
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
    # AMANDA puts either a street address or a project name in PROPERTYNAME.
    if not c.address and re.match(r"\d", c.name):
        c.address = c.name
    if c.name and c.name == c.address:
        c.name = f"{_title_address(c.address)} {layer_name or c.type_label}".strip()
    if not c.name:
        c.name = c.description[:60] or c.address or c.type_label
    return c


def _title_address(addr: str) -> str:
    """'10700 W Bowles AVE' -> '10700 W Bowles Ave'."""
    keep = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "&"}
    return " ".join(w if w.upper() in keep or any(ch.isdigit() for ch in w) else w.capitalize() for w in addr.split())
