# Jeffco Notice Map

A small self-hosted web app that maps Jefferson County, Colorado land use notices (rezonings, special uses,
site plans, plats, variances) near ZIP 80128. It scrapes only when you press **Scan county now**.

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

## The first scan

The county publishes active cases as a layer on its ArcGIS server (the same data behind the jMap
Planning & Zoning map). On the first scan, if `source.layer_url` is empty, the app searches the county
GIS server for the layer that looks most like active land use cases and remembers it.

To check what it picked, or choose it yourself:

```sh
python -m app.discover                    # ranked list of candidate layers
python -m app.discover --layer <url>      # fields, guessed field mapping, and one sample record
```

Then set `source.layer_url` (and `source.fields` if any guess is wrong) in `config.yaml`.
With Docker: `docker compose run --rm notice-map python -m app.discover`.

If cases show up without documents, find a case's page on jeffco.us and put its URL pattern in
`source.case_page_template`, with `{case}` where the case number goes. The app collects PDF links from that page.

## Plan summaries

Summaries need an Anthropic API key in `ANTHROPIC_API_KEY`. By default a summary is made only when you
open a case and press **Summarize plans**. It is saved and reused until the case's document list changes.
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
- The case layer's fields aren't documented publicly, so field mapping is guessed. Check it with `app.discover`.
- Map tiles come from OpenStreetMap; please keep usage light.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest
```

The tests use a fake county server (`tests/fake_county.py`) with made-up records, so they run offline.
