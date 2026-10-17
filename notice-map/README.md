# Jeffco Notice Map

A small self-hosted web app that maps Jefferson County, Colorado land use cases (rezonings, special uses,
site plans, plats, pre-applications) in and around ZIP 80128. It scrapes only when you press **Scan county now**.

For each case you get:

- a marker and parcel outline on a street map
- status, hearing dates, planner, and the county's own description
- links back to the case documents on jeffco.us
- optional: a plain-English summary written by Claude from the PDFs, key numbers (units, height, square feet),
  and a 3D massing sketch, also drawn as building footprints on the map

## Quick start (Docker)

```sh
cd notice-map
cp config.example.yaml config.yaml
export ANTHROPIC_API_KEY=sk-ant-...   # optional, for plan summaries
docker compose up -d --build
```

Open http://localhost:8765 and press **Scan county now**.

## Quick start (Python 3.11+)

```sh
cd notice-map
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

## Where the data comes from

- **Cases:** the county's public `PZ_Amanda7_Development_Review` map service
  (`gisportal.jeffco.us/server/rest/services/PZ_Amanda7_Development_Review/MapServer`). It is fed by AMANDA,
  the county's case system, and has one layer per case type. Each record is one parcel; the app rebuilds the
  case number (`26-103768RZ`) and merges multi-parcel cases. Cases filed more than `max_age_years` ago are
  skipped, because the service still lists old cases that were never closed out.
- **Details for high-interest cases:** the county's Planning and Zoning news posts
  (`jeffco.us/CivicAlerts.aspx?CID=64`). These add a plain-language summary, the case manager, hearing dates,
  and a link to the case's public documents folder.
- **Boundary:** the Census ZIP Code Tabulation Area for 80128, plus `area.buffer_km` (1 km by default) so
  cases just outside the line still show.

The county service gives a status code with no public labels, so the app shows "Open · filed <date>"
instead. It also truncates descriptions to 50 characters; the full text and documents are in the
[Jeffco Citizen Portal](https://citizenportal.jeffco.us/citizenportal/app/landing) (Planning & Zoning Search →
Advanced → case number → View Public Documents), which each case links to.

If the county changes its services, `python -m app.discover` searches its GIS server for case layers and
`python -m app.discover --layer <url>` shows a layer's fields. Point `source.service_url` or
`source.layer_url` at the result.

## Plan summaries

Summaries need an Anthropic API key in `ANTHROPIC_API_KEY`. By default a summary is made only when you
open a case and press **Summarize plans**. It is saved and reused until the case's document list changes.
Claude reads any PDFs the app can download for the case; the county's SharePoint document folders are
linked but not yet downloaded automatically, so for most cases the summary works from the case record and
the county's post.
Set `ai.interpret_on_scan: true` to summarize every new case during a scan.

Cost depends on how many pages the PDFs have; expect roughly $0.10 to $1 per case.
Summaries, numbers, and sketches are estimates read from the documents. The linked county PDFs are the
source of truth.

## Home Assistant

Add it as a dashboard: **Settings → Dashboards → Add dashboard → Webpage**, URL `http://<host>:8765`.
Or add a **Webpage** card to an existing dashboard.

## How it works

1. Load the ZIP boundary from the Census TIGERweb service (cached in `data/`).
2. Query the county case layer for features inside the boundary's bounding box, in WGS84.
3. Use the parcel geometry for location; geocode the address with the Census geocoder if there is none.
4. Keep cases inside the ZIP polygon and merge multi-parcel cases.
5. Save to SQLite (`data/notices.db`); cases missing from a scan are marked inactive and dropped from the
   map after `keep_closed_days`.
6. Find PDF links in the case record and on the case page.
7. On request, download the PDFs and ask Claude for a structured summary.

## Limits

- Only cases in **unincorporated** Jefferson County. Parts of 80128 inside Littleton city limits go through the city.
- The county's status codes have no public labels, and its descriptions are cut at 50 characters.
- Map tiles come from OpenStreetMap; please keep usage light.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a fake county server (`tests/fake_county.py`) with made-up records, so they run offline.
