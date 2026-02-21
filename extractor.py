"""
extractor.py
Finds the main weldment PDF (210-xxxxx-xx) inside a project folder and
extracts the part list table from it.
"""

import logging
from pathlib import Path
from typing import Optional

import pdfplumber

from config import WELDMENT_PATTERN, HEADER_KEYWORDS

log = logging.getLogger(__name__)


def find_weldment_file(folder: Path) -> Optional[Path]:
    """Return the first PDF in *folder* whose stem matches the weldment pattern."""
    weldments = find_all_weldment_files(folder)
    return weldments[0] if weldments else None


def find_all_weldment_files(folder: Path) -> list:
    """Return all PDFs in *folder* whose stem matches the weldment pattern, newest first."""
    matches = [p for p in folder.glob("*.pdf") if WELDMENT_PATTERN.match(p.stem)]
    if not matches:
        log.warning("No weldment file (210-xxxxx-xx.pdf) found in %s", folder)
        return []
    # Sort by modification time descending so the newest project is processed first
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for m in matches:
        log.info("Found weldment file: %s", m.name)
    return matches


def _find_header_row(rows: list[list]) -> Optional[int]:
    """
    Return the index of the row that looks like a table header.
    We look for a row where at least 2 cells contain header keywords.
    """
    for i, row in enumerate(rows):
        cells = [str(c).lower().strip() for c in row if c]
        # A header row typically has words like 'part', 'no', 'qty', 'description'
        matches = sum(1 for cell in cells if any(kw in cell for kw in HEADER_KEYWORDS))
        if matches >= 2:
            return i
    return None


def _normalise_header(headers: list) -> list[str]:
    """Convert raw header cells to clean lowercase strings."""
    return [str(h).lower().strip() if h else "" for h in headers]


def _col_index(headers: list[str], *candidates: str) -> Optional[int]:
    """Find the first header that contains any of the candidate substrings."""
    for i, h in enumerate(headers):
        if any(c in h for c in candidates):
            return i
    return None


def extract_part_list(pdf_path: Path) -> list[dict]:
    """
    Open *pdf_path* and extract the part list table.

    Returns a list of dicts with keys: part_no, description, qty.
    Rows where part_no is empty or looks like a sub-header are skipped.
    """
    parts: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue

                header_idx = _find_header_row(table)
                if header_idx is None:
                    continue

                headers = _normalise_header(table[header_idx])
                part_col = _col_index(headers, "part", "no", "number", "dwg")
                desc_col = _col_index(headers, "desc", "name")
                qty_col = _col_index(headers, "qty", "quantity", "quant")

                if part_col is None:
                    log.debug("Page %d: could not identify part-number column", page_num)
                    continue

                for row in table[header_idx + 1 :]:
                    part_no = str(row[part_col]).strip() if row[part_col] else ""
                    description = str(row[desc_col]).strip() if desc_col is not None and row[desc_col] else ""
                    qty_raw = str(row[qty_col]).strip() if qty_col is not None and row[qty_col] else "1"

                    # Skip blank or repeated-header rows
                    if not part_no or part_no.lower() in {"", "part no", "part number", "none"}:
                        continue

                    # Try to parse qty as a number, fallback to 1
                    try:
                        qty = int(float(qty_raw))
                    except ValueError:
                        qty = 1

                    parts.append({"part_no": part_no, "description": description, "qty": qty})

    log.info("Extracted %d parts from %s", len(parts), pdf_path.name)
    return parts
