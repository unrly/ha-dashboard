"""Find and download the PDFs behind each case."""
from __future__ import annotations

import hashlib
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from .cases import Case

log = logging.getLogger(__name__)

# Jeffco's site serves files from CivicPlus "DocumentCenter/View/<id>" URLs, which often have no .pdf suffix.
DOC_URL_RE = re.compile(r"(\.pdf($|\?))|(/DocumentCenter/View/\d+)", re.I)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def doc_links_in_html(html: str, base_url: str) -> list[dict]:
    p = _Links()
    p.feed(html)
    out, seen = [], set()
    for href, text in p.links:
        url = urljoin(base_url, href)
        if DOC_URL_RE.search(url) and url not in seen:
            seen.add(url)
            title = text or Path(urlparse(url).path).name
            out.append({"title": title, "url": url})
    return out


def collect_links(client: httpx.Client, case: Case, page_template: Optional[str]) -> list[dict]:
    """PDF links from the case record, plus any linked from the case's web page."""
    pages: list[str] = []
    docs: list[dict] = []
    for link in case.links:
        u = link["url"]
        if DOC_URL_RE.search(u):
            docs.append({**link, "kind": "pdf"})
        elif "sharepoint.com" in u:
            # The county's public document library: a shared folder per case. Linked as-is.
            docs.append({**link, "kind": "folder"})
        elif link.get("kind") == "page":
            docs.append(link)
        else:
            pages.append(u)
    if page_template:
        pages.append(page_template.replace("{case}", case.case_number))
    for page in pages[:3]:
        try:
            r = client.get(page)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.info("case page %s: %s", page, e)
            continue
        if "html" in r.headers.get("content-type", ""):
            docs.extend({**d, "kind": "pdf"} for d in doc_links_in_html(r.text, str(r.url)))
    seen, out = set(), []
    for d in docs:
        if d["url"] not in seen:
            seen.add(d["url"])
            out.append(d)
    return out


def download(client: httpx.Client, url: str, folder: Path, max_mb: float) -> Optional[Path]:
    """Download a PDF once; later calls reuse the cached copy."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if path.exists() and path.stat().st_size > 0:
        return path
    limit = int(max_mb * 1024 * 1024)
    try:
        with client.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > limit:
                    log.info("skip %s: larger than %s MB", url, max_mb)
                    return None
    except httpx.HTTPError as e:
        log.warning("download %s: %s", url, e)
        return None
    if not bytes(buf[:5]).startswith(b"%PDF"):
        log.info("skip %s: not a PDF", url)
        return None
    path.write_bytes(bytes(buf))
    return path
