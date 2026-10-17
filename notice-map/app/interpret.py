"""Ask Claude to read a case's PDFs and explain them in plain English, with numbers for the massing sketch."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

import anthropic

from .cases import Case
from .config import AIConfig

log = logging.getLogger(__name__)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "request", "key_numbers", "lot", "buildings", "watch", "confidence", "notes"],
    "properties": {
        "summary": {"type": "string", "description": "2-4 plain sentences: what would be built or changed, and where."},
        "request": {"type": "string", "description": "Short form of the request, e.g. 'A-1 to PD' or '75 ft monopole'."},
        "key_numbers": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["value", "label", "source"],
                "properties": {
                    "value": {"type": "string", "description": "Short value with units, e.g. '24' or '42 ft'."},
                    "label": {"type": "string", "description": "What it measures, e.g. 'dwelling units'."},
                    "source": {"type": "string", "description": "Document title and page, e.g. 'Site plan p.2'."},
                },
            },
        },
        "lot": {
            "type": "object", "additionalProperties": False, "required": ["width_m", "depth_m"],
            "properties": {"width_m": {"type": "number"}, "depth_m": {"type": "number"}},
        },
        "buildings": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["label", "x_m", "y_m", "width_m", "depth_m", "floors"],
                "properties": {
                    "label": {"type": "string"},
                    "x_m": {"type": "number", "description": "Offset east from the lot's west edge, meters."},
                    "y_m": {"type": "number", "description": "Offset south from the lot's north edge, meters."},
                    "width_m": {"type": "number"}, "depth_m": {"type": "number"},
                    "floors": {"type": "number", "description": "Stories; use height_ft / 11 for towers or sheds."},
                },
            },
        },
        "watch": {"type": "array", "items": {"type": "string"},
                  "description": "Things a neighbor would want to know: hearing dates, comment deadlines, traffic, height."},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "notes": {"type": "string", "description": "What was estimated or missing from the documents."},
    },
}

SYSTEM = """You read land use case files from Jefferson County, Colorado, for a neighbor who is not a planner.
Explain what is proposed in plain, specific language. Use numbers from the documents, and say where each came from.
For the massing sketch, estimate building footprints and stories from the site plan and elevations. Keep the lot
and buildings in meters with the lot's northwest corner at (0, 0). If a value is not in the documents, estimate
it only when the documents support a reasonable guess, and say so in notes. If there are no buildings (for
example a plat or a variance without plans), return an empty buildings list. Do not invent hearing dates."""


def case_context(case: Case) -> str:
    fields = {
        "Case number": case.case_number, "Name": case.name, "Type": f"{case.type_label} ({case.case_type_raw})",
        "Status": case.status, "Address": case.address, "Acres": case.acres, "Description": case.description,
        "Dates": case.dates,
    }
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v)


def interpret(client: anthropic.Anthropic, cfg: AIConfig, case: Case, pdfs: list[tuple[str, Path]]) -> dict:
    """pdfs is a list of (title, path). Returns the parsed JSON plus metadata."""
    content: list[dict] = []
    for title, path in pdfs:
        content.append({
            "type": "document",
            "title": title[:200],
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(path.read_bytes()).decode()},
        })
    content.append({"type": "text", "text": "Case record:\n" + case_context(case) +
                    ("" if pdfs else "\n\nNo documents were available; work from the case record only and say so.")})

    with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=32000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to summarize this case")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("The summary was cut off; try again")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("Claude returned no summary")
    data = json.loads(text)
    data["model"] = msg.model
    data["documents"] = [t for t, _ in pdfs]
    return data


def make_client() -> Optional[anthropic.Anthropic]:
    try:
        return anthropic.Anthropic()
    except anthropic.AnthropicError as e:  # no credentials configured
        log.info("Claude disabled: %s", e)
        return None
