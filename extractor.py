"""
extractor.py
Finds the main weldment PDF (210-xxxxx-xx) inside a project folder and
extracts the part list table from it.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber

from config import ASSEMBLY_PATTERN, WELDMENT_PATTERN, HEADER_KEYWORDS

# Engineering part number: 3 digits – 5 digits – 2 digits (optional revision suffix)
_PART_NO_RE = re.compile(r"(\d{3}-\d{5}-\d{2}(?:-[A-Za-z]\d+)?)", re.IGNORECASE)

log = logging.getLogger(__name__)


def find_all_assembly_files(folder: Path) -> list:
    """Return all PDFs in *folder* whose stem matches the assembly pattern (215-*), newest first."""
    matches = [p for p in folder.glob("*.pdf") if ASSEMBLY_PATTERN.match(p.stem)]
    if not matches:
        return []
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for m in matches:
        log.info("Found assembly file: %s", m.name)
    return matches


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

    Handles two layouts:
      • Normal:  multiple separate cells each containing a keyword
                 e.g. 'ITEM' | 'PART NUMBER' | 'DESCRIPTION' | 'QTY'
      • Merged:  all column headers collapsed into one cell
                 e.g. 'PARTS LIST\nITEM PART NUMBER DESCRIPTION PART QTY'
    """
    for i, row in enumerate(rows):
        cells = [str(c).lower().strip() for c in row if c]
        # Normal case: at least 2 cells individually match a header keyword
        matches = sum(1 for cell in cells if any(kw in cell for kw in HEADER_KEYWORDS))
        if matches >= 2:
            return i
        # Merged header: one cell contains 3+ distinct header keywords
        distinct_kw = sum(1 for kw in HEADER_KEYWORDS if any(kw in cell for cell in cells))
        if distinct_kw >= 3:
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


def _description_for_row(row: list) -> str:
    """
    Find the best description text in a row by scanning all cells.

    Used as a fallback when the column-detected description is just a number
    (e.g. item numbers in merged-header tables).  Returns the first cell that:
      • is not empty
      • is not a pure number / item counter
      • does not look like a part number
      • contains letters and is at least 5 characters long
    """
    cells = [str(c).strip() if c is not None else "" for c in row]
    for cell in cells:
        if not cell:
            continue
        if re.fullmatch(r"[\d\s.]+", cell):   # skip pure numbers / item counters
            continue
        if _PART_NO_RE.search(cell):           # skip part numbers
            continue
        if len(cell) >= 5 and re.search(r"[A-Za-z]", cell):
            return cell.replace("\n", ", ")
    return ""


def _scan_row_for_part_nos(row: list) -> list[str]:
    """
    Scan all cells in *row* for engineering part numbers.

    Returns a deduplicated list of all part numbers found. Handles:
      • Normal:   one cell contains the full part number
      • Merged:   multiple part numbers in one cell separated by newlines
                  e.g. '210-35052-00\\n548-32-003' → ['210-35052-00']
      • Split:    part number broken across two adjacent cells (first ends with '-')
                  e.g. '1 290-' + '38199-00' → ['290-38199-00']
    """
    cells = [str(c).strip() if c is not None else "" for c in row]
    found: list[str] = []

    # Pass 1 — findall in each cell (handles merged/multiline cells too)
    for cell in cells:
        found.extend(_PART_NO_RE.findall(cell))

    if found:
        return list(dict.fromkeys(found))  # deduplicate, preserve order

    # Pass 2 — part number split across adjacent cells (first cell ends with '-')
    for i in range(len(cells) - 1):
        if cells[i].endswith("-") and cells[i + 1]:
            found.extend(_PART_NO_RE.findall(cells[i] + cells[i + 1]))

    return list(dict.fromkeys(found))


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
                    # Primary: scan every cell for part number patterns.
                    # Returns a list — handles merged cells with multiple parts per row.
                    part_nos = _scan_row_for_part_nos(row)

                    # Fallback: use the column-detected value if no pattern match found.
                    if not part_nos and part_col is not None and row[part_col]:
                        raw = str(row[part_col]).strip()
                        if raw:
                            part_nos = [raw]

                    description = str(row[desc_col]).strip() if desc_col is not None and row[desc_col] else ""
                    # Fallback: if desc_col gives a pure number (item counter from
                    # merged-header tables), scan the row for the real description.
                    if not description or re.fullmatch(r"[\d\s]+", description):
                        description = _description_for_row(row)
                    qty_raw = str(row[qty_col]).strip() if qty_col is not None and row[qty_col] else "1"

                    # Try to parse qty as a number, fallback to 1
                    try:
                        qty = int(float(qty_raw.split()[0]))  # handle "1\n2" merged qty
                    except (ValueError, IndexError):
                        qty = 1

                    for part_no in part_nos:
                        if not part_no or part_no.lower() in {"part no", "part number", "none"}:
                            continue
                        parts.append({"part_no": part_no, "description": description, "qty": qty})

    log.info("Extracted %d parts from %s", len(parts), pdf_path.name)
    return parts
