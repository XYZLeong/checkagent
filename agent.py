"""
agent.py
Drawing Checker Agent — HTTP server mode.

n8n polls Google Drive every 5 minutes and POSTs every PDF here.
The agent:
  1. Saves the file into a project subfolder (grouped by Google Drive folder ID)
     — always overwrites so updated drawings are always current
  2. Waits SETTLE_SECONDS after the last file for that project (debounce)
  3. Runs the full analysis pipeline on the project folder
  4. Compares the result with the previous run — only POSTs to the n8n alert
     webhook when the missing-drawings list actually changes

This means:
  • Files that existed in Google Drive before the workflow started are picked
    up automatically on the next poll — no manual backfill needed.
  • Re-uploading a corrected drawing triggers a fresh check and, if the missing
    list shrinks (or clears), a new notification is sent.
  • Repeated polls with no new/changed files produce no duplicate alerts.
"""

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Dict

from flask import Flask, request, jsonify

import config
from analyzer import check_drawings
from extractor import extract_part_list, find_all_weldment_files
from notifier import send_alert, send_complete_package

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("agent")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Results cache — remember last known missing list per project
# Notifications are only sent when the list actually changes
# ---------------------------------------------------------------------------
_results_lock = threading.Lock()


def _load_results() -> dict:
    if config.RESULTS_FILE.exists():
        try:
            return json.loads(config.RESULTS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _result_changed(project_name: str, missing: list) -> bool:
    """Return True if the missing-drawings list differs from the last stored result.
    Always returns True the first time a project is seen (even if missing list is empty),
    so weldments with no fabrication parts still trigger a notification on first run.
    """
    with _results_lock:
        cache = _load_results()
    if project_name not in cache:
        return True  # first time seeing this project — always notify
    prev = set(cache[project_name].get("missing", []))
    curr = {m["part_no"] for m in missing}
    return prev != curr


def _update_result(project_name: str, missing: list) -> None:
    with _results_lock:
        cache = _load_results()
        cache[project_name] = {"missing": sorted(m["part_no"] for m in missing)}
        config.RESULTS_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Duplicate / update tracking
# Detects when an existing file is re-uploaded with different content.
# n8n re-sends all files every poll with the same bytes → those are silent.
# Only files whose MD5 changes are flagged as genuine duplicates.
# ---------------------------------------------------------------------------
_updates_lock = threading.Lock()
_pending_updates: Dict[str, list] = {}  # folder_path → [filenames with changed content]


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_update(folder_key: str, filename: str) -> None:
    with _updates_lock:
        lst = _pending_updates.setdefault(folder_key, [])
        if filename not in lst:
            lst.append(filename)


def _pop_updates(folder_key: str) -> list:
    with _updates_lock:
        return _pending_updates.pop(folder_key, [])


# ---------------------------------------------------------------------------
# Debounce — run pipeline once per project after all files settle
# ---------------------------------------------------------------------------
_timer_lock = threading.Lock()
_timers: Dict[str, threading.Timer] = {}  # folder_path → pending Timer


def _schedule_pipeline(project_folder: Path) -> None:
    key = str(project_folder)
    with _timer_lock:
        existing = _timers.get(key)
        if existing:
            existing.cancel()
        timer = threading.Timer(config.SETTLE_SECONDS, _fire_pipeline, args=(project_folder,))
        timer.daemon = True
        timer.start()
        _timers[key] = timer


def _fire_pipeline(folder: Path) -> None:
    with _timer_lock:
        _timers.pop(str(folder), None)
    log.info("Files settled for '%s' — starting pipeline", folder.name)
    try:
        run_pipeline(folder)
    except Exception:
        log.exception("Unexpected error in pipeline for %s", folder)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(folder: Path) -> None:
    log.info("=== Analysis: %s ===", folder.name)

    weldments = find_all_weldment_files(folder)
    if not weldments:
        log.warning("No 210-xxxxx-xx.pdf yet in %s — will retry when more files arrive", folder)
        return

    # Process every weldment found (supports multiple projects in the same folder)
    for weldment in weldments:
        _run_single_project(weldment, folder)

    log.info("=== Done: %s ===", folder.name)


def _run_single_project(weldment: Path, folder: Path) -> None:
    match = config.WELDMENT_PATTERN.match(weldment.stem)
    project_name = match.group(0) if match else weldment.stem
    log.info("--- Project: %s ---", project_name)

    part_list = extract_part_list(weldment)
    if not part_list:
        log.warning("Part list is empty for %s — skipping.", project_name)
        return

    result = check_drawings(part_list, folder)
    missing = result["missing"]
    total_fabrication = len(result["present"]) + len(missing)

    if not _result_changed(project_name, missing):
        # Missing list unchanged — but check for genuine re-uploads (different content)
        duplicates = _pop_updates(str(folder))
        if duplicates and config.N8N_WEBHOOK_URL:
            log.info("%d duplicate upload(s) detected for %s — sending notification.", len(duplicates), project_name)
            send_alert(
                project_name=project_name,
                folder=folder,
                total_fabrication=total_fabrication,
                missing_drawings=missing,
                webhook_url=config.N8N_WEBHOOK_URL,
                duplicated_drawings=duplicates,
            )
        else:
            log.info("No change in results for %s — skipping notification.", project_name)
        return

    _update_result(project_name, missing)
    duplicates = _pop_updates(str(folder))  # clear any pending updates

    if not config.N8N_WEBHOOK_URL:
        log.warning("N8N_WEBHOOK_URL not set — skipping notification.")
        log.info("Missing: %s", [m["part_no"] for m in missing])
        return

    if missing:
        # Still incomplete — send missing-drawing alert
        send_alert(
            project_name=project_name,
            folder=folder,
            total_fabrication=total_fabrication,
            missing_drawings=missing,
            webhook_url=config.N8N_WEBHOOK_URL,
            duplicated_drawings=duplicates,
        )
        log.info("Alert sent — %d of %d fabrication drawing(s) MISSING.", len(missing), total_fabrication)
    else:
        # All drawings present — ZIP every PDF in the folder and email it
        all_pdfs = sorted(folder.glob("*.pdf"))
        send_complete_package(
            project_name=project_name,
            pdf_files=all_pdfs,
            webhook_url=config.N8N_WEBHOOK_URL,
            folder_id=folder.name,  # folder.name == Google Drive folder ID
        )
        log.info("Complete package sent — all %d fabrication drawing(s) present.", total_fabrication)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@app.route("/file", methods=["POST"])
def receive_file():
    """
    Receive a PDF file posted by n8n.

    Expected multipart/form-data fields:
      file_id   — Google Drive file ID (used for deduplication)
      filename  — original filename
      folder_id — Google Drive parent folder ID (used to group project files)
      file      — binary PDF content
    """
    file_id   = request.form.get("file_id", "").strip()
    filename  = request.form.get("filename", "").strip()
    folder_id = request.form.get("folder_id", "").strip()
    file_obj  = request.files.get("file")

    if not file_id or not filename or file_obj is None:
        return jsonify({"error": "Missing file_id, filename, or file"}), 400

    if not filename.lower().endswith(".pdf"):
        return jsonify({"status": "ignored", "reason": "not a PDF"}), 200

    # Group files by Google Drive folder ID so all project files land together
    group = folder_id if folder_id else "default"
    project_folder = config.INCOMING_DIR / group
    project_folder.mkdir(parents=True, exist_ok=True)

    dest = project_folder / filename
    existing_hash = _file_hash(dest) if dest.exists() else None
    file_obj.save(str(dest))
    if existing_hash is not None and _file_hash(dest) != existing_hash:
        _record_update(str(project_folder), filename)
        log.info("Content changed (duplicate upload): %s", filename)
    else:
        log.info("Saved: %s → %s", filename, dest)

    _schedule_pipeline(project_folder)

    return jsonify({"status": "accepted", "saved_to": str(dest)}), 200



@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Manually trigger the analysis pipeline for files already in the incoming folder.

    Optional JSON body:
      {"folder_id": "1E40nZbJxEUbb76BFR23uJi_bjvuzuMTn"}

    If folder_id is provided → run pipeline on that one project folder.
    If omitted → run pipeline on every subfolder inside INCOMING_DIR.
    """
    data = request.get_json(silent=True) or {}
    folder_id = data.get("folder_id", "").strip()

    if folder_id:
        folders = [config.INCOMING_DIR / folder_id]
    else:
        folders = [p for p in config.INCOMING_DIR.iterdir() if p.is_dir()]

    triggered = []
    for folder in folders:
        if folder.is_dir():
            log.info("Manual analyze triggered for: %s", folder.name)
            threading.Thread(target=_fire_pipeline, args=(folder,), daemon=True).start()
            triggered.append(folder.name)

    return jsonify({"triggered": triggered}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    config.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Drawing Checker Agent starting (HTTP mode).")
    log.info("Port         : %d", config.AGENT_PORT)
    log.info("Incoming dir : %s", config.INCOMING_DIR)
    log.info("Results cache: %s", config.RESULTS_FILE)
    log.info("Settle delay : %ds", config.SETTLE_SECONDS)
    log.info("n8n webhook  : %s", config.N8N_WEBHOOK_URL or "(not set)")

    app.run(host="0.0.0.0", port=config.AGENT_PORT, threaded=True)


if __name__ == "__main__":
    main()
