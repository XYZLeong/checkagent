"""
analyzer.py
Classifies parts from the extracted part list and checks whether each
fabrication part has a corresponding PDF drawing in the project folder.
"""

import logging
import re
from pathlib import Path

from config import SHEET_METAL_PATTERN, MACHINING_PATTERN

# Matches a trailing revision suffix like -P01, -P02, -P10
_REVISION_RE = re.compile(r"-P\d+$", re.IGNORECASE)

log = logging.getLogger(__name__)


def classify_part(part_no: str) -> str:
    """
    Return the part classification based on its drawing number prefix.

    Returns:
        "sheet_metal"  – prefix 290-xxxx-xx  (requires drawing)
        "machining"    – prefix 300-xxxx-xx  (requires drawing)
        "standard"     – anything else        (drawing not required)
    """
    if SHEET_METAL_PATTERN.match(part_no):
        return "sheet_metal"
    if MACHINING_PATTERN.match(part_no):
        return "machining"
    return "standard"


def _base_part_no(part_no: str) -> str:
    """Strip trailing revision suffix (e.g. -P01, -P02) from a part number.

    Examples:
        290-25396-00-P01  →  290-25396-00
        290-25396-00-P02  →  290-25396-00
        290-25396-00      →  290-25396-00  (unchanged)
    """
    return _REVISION_RE.sub("", part_no).strip()


def _pdf_stems_in_folder(folder: Path) -> set[str]:
    """Return a set of lowercase PDF stems (filenames without extension) in *folder*."""
    return {p.stem.lower() for p in folder.glob("*.pdf")}


def check_drawings(part_list: list[dict], folder: Path) -> dict:
    """
    Compare the fabrication parts in *part_list* against PDF files in *folder*.

    Returns a dict with:
        present   – list of fabrication parts that have a drawing
        missing   – list of fabrication parts with NO drawing found
        standard  – list of parts classified as standard (skipped)
    """
    available = _pdf_stems_in_folder(folder)
    log.debug("PDFs available in folder: %s", sorted(available))

    present: list[dict] = []
    missing: list[dict] = []
    standard: list[dict] = []

    for part in part_list:
        part_no: str = part["part_no"]
        kind = classify_part(part_no)

        if kind == "standard":
            standard.append(part)
            continue

        # Strip revision suffix (-P01, -P02 …) so that a BOM entry like
        # "290-25396-00-P01" matches a file named "290-25396-00-P02_...pdf".
        base_no = _base_part_no(part_no).lower()
        found = any(stem.startswith(base_no) for stem in available)

        enriched = {**part, "type": kind}
        if found:
            present.append(enriched)
            log.debug("Drawing FOUND for %s (%s)", part_no, kind)
        else:
            missing.append(enriched)
            log.warning("Drawing MISSING for %s (%s) – %s", part_no, kind, part.get("description", ""))

    log.info(
        "Analysis complete: %d fabrication parts (%d present, %d missing), %d standard parts",
        len(present) + len(missing),
        len(present),
        len(missing),
        len(standard),
    )

    return {"present": present, "missing": missing, "standard": standard}
