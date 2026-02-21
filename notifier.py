"""
notifier.py
Sends alerts and complete-package notifications to the n8n webhook,
which in turn emails the user.
"""

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def send_alert(
    project_name: str,
    folder: Path,
    total_fabrication: int,
    missing_drawings: list[dict],
    webhook_url: str,
    duplicated_drawings: list = None,
) -> bool:
    """
    POST the missing-drawing report to the n8n webhook.

    Returns True if the webhook responded with a 2xx status, False otherwise.
    *duplicated_drawings* is a list of filenames that were re-uploaded with
    different content (genuine updates, not n8n re-polls).
    """
    duplicated = duplicated_drawings or []
    payload = {
        "project": project_name,
        "folder": str(folder),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_fabrication_parts": total_fabrication,
        "missing_count": len(missing_drawings),
        "missing_drawings": missing_drawings,
        "all_ok": len(missing_drawings) == 0,
        "duplicated_drawings": duplicated,
        "duplicated_count": len(duplicated),
    }

    log.info(
        "Sending alert to n8n: project=%s, missing=%d",
        project_name,
        len(missing_drawings),
    )
    log.debug("Payload: %s", json.dumps(payload, indent=2))

    try:
        response = requests.post(webhook_url, json=payload, timeout=15)
        response.raise_for_status()
        log.info("n8n webhook acknowledged: HTTP %d", response.status_code)
        return True
    except requests.exceptions.RequestException as exc:
        log.error("Failed to reach n8n webhook: %s", exc)
        return False


def send_complete_package(
    project_name: str,
    pdf_files: list,
    webhook_url: str,
    folder_id: str = "",
) -> bool:
    """
    ZIP all PDFs for the completed project and POST to the n8n webhook.
    n8n attaches the ZIP to a Gmail and sends it to the user.

    *pdf_files* is a list of Path objects — every PDF in the project folder.
    *folder_id* is the Google Drive folder ID so n8n can delete files after sending.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf in pdf_files:
            zf.write(pdf, pdf.name)
    buf.seek(0)

    zip_name = f"{project_name}_drawings.zip"
    files_payload = {"drawings_zip": (zip_name, buf, "application/zip")}
    data = {
        "report_type":    "complete_package",
        "project":        project_name,
        "total_drawings": str(len(pdf_files)),
        "zip_filename":   zip_name,
        "folder_id":      folder_id,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "Sending complete package to n8n: project=%s, files=%d",
        project_name, len(pdf_files),
    )

    try:
        response = requests.post(webhook_url, data=data, files=files_payload, timeout=60)
        response.raise_for_status()
        log.info("Complete package acknowledged: HTTP %d", response.status_code)
        return True
    except requests.exceptions.RequestException as exc:
        log.error("Failed to send complete package: %s", exc)
        return False
