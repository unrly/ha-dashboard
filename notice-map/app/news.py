"""Read the county's "high interest case" posts for plain-language details and document links.

Each post (jeffco.us News Flash, Planning and Zoning category) has sections like Location, Case Summary,
Case Number, Documents, Case Manager, and Hearings.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)

CASE_RE = re.compile(r"\b\d{2}-\d{6}[A-Z]{2,3}\b")
SECTIONS = ["Location", "Case Summary", "Case Number", "Documents", "Case Manager", "Vicinity Map", "Hearings",
            "Case Documents", "Related News"]
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s*(\d{{4}})")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", fragment, flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"[ \t\xa0]+", " ", t)


def list_posts(client: httpx.Client, list_url: str) -> list[dict]:
    r = client.get(list_url)
    r.raise_for_status()
    out, seen = [], set()
    for href, title in re.findall(r'<a[^>]+href="([^"]*/Detail/\d+[^"]*)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        url = urljoin(str(r.url), htmllib.unescape(href))
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": " ".join(_text(title).split())})
    return out


def parse_post(page: str, url: str, title: str = "") -> Optional[dict]:
    """Pull the structured sections out of one case post. Returns None if it has no case number."""
    body = page
    m = re.search(r"Posted on .*?(?=Related News|Return to full list|</main>)", page, re.S | re.I)
    if m:
        body = m.group(0)
    text = _text(body)
    flat = " ".join(text.split())
    # Split on section headings that appear as standalone words in the post.
    pat = re.compile(r"\b(" + "|".join(re.escape(s) for s in SECTIONS) + r")\b")
    parts: dict[str, str] = {}
    hits = list(pat.finditer(flat))
    for i, h in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(flat)
        key = h.group(1)
        val = flat[h.end():end].strip()
        if val and key not in parts:
            parts[key] = val
    nums = CASE_RE.findall(parts.get("Case Number", "")) or CASE_RE.findall(flat)
    if not nums:
        return None
    docs = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        h = htmllib.unescape(href)
        lab = " ".join(_text(label).split())
        if "sharepoint.com" in h or "DocumentCenter" in h or re.search(r"document", lab, re.I):
            if not any(d["url"] == h for d in docs):
                docs.append({"title": lab or "Case documents", "url": urljoin(url, h)})
    manager = parts.get("Case Manager", "")
    manager = re.split(r";|\bemail\b", manager, maxsplit=1)[0].strip()
    hearings = parts.get("Hearings", "")
    hearing_dates = {}
    for sent in re.split(r"(?<=[.!?])\s+", hearings):
        dm = DATE_RE.search(sent)
        if not dm:
            continue
        iso = f"{dm.group(3)}-{_month(dm.group(1)):02d}-{int(dm.group(2)):02d}"
        if re.search(r"planning commission", sent, re.I):
            hearing_dates.setdefault("pc_hearing", iso)
        elif re.search(r"county commissioners|BCC", sent, re.I):
            hearing_dates.setdefault("bcc_hearing", iso)
        elif re.search(r"board of adjustment", sent, re.I):
            hearing_dates.setdefault("boa_hearing", iso)
    return {
        "case_numbers": sorted(set(nums)),
        "title": title,
        "url": url,
        "location": parts.get("Location", ""),
        "summary": parts.get("Case Summary", ""),
        "manager": manager,
        "hearings": hearings,
        "hearing_dates": hearing_dates,
        "docs": docs,
    }


def _month(name: str) -> int:
    return MONTHS.split("|").index(name) + 1


def fetch_posts(client: httpx.Client, list_url: str, limit: int = 40) -> dict[str, dict]:
    """Return {case_number: post} for recent high-interest posts."""
    by_case: dict[str, dict] = {}
    try:
        posts = list_posts(client, list_url)[:limit]
    except httpx.HTTPError as e:
        log.warning("high-interest list: %s", e)
        return by_case
    for p in posts:
        try:
            r = client.get(p["url"])
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.info("post %s: %s", p["url"], e)
            continue
        info = parse_post(r.text, p["url"], p["title"])
        if info:
            for n in info["case_numbers"]:
                by_case.setdefault(n, info)
    return by_case
