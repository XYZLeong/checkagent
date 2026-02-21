import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# n8n webhook URL for the missing-drawing alert
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")

# Seconds to wait after last file for a project before running the pipeline
SETTLE_SECONDS = int(os.getenv("SETTLE_SECONDS", "5"))

# HTTP port the agent listens on (n8n POSTs files here)
AGENT_PORT = int(os.getenv("AGENT_PORT", "8765"))

# Directory where incoming project files are stored (grouped by GDrive folder ID)
INCOMING_DIR = Path(os.getenv("INCOMING_DIR", Path(__file__).parent / "incoming"))

# JSON file tracking which Google Drive file IDs have been processed (kept for reference)
STATE_FILE = Path(os.getenv("STATE_FILE", Path(__file__).parent / "processed_files.json"))

# JSON file storing the last known analysis result per project (for change detection)
RESULTS_FILE = Path(os.getenv("RESULTS_FILE", Path(__file__).parent / "results_cache.json"))

# JSON file storing the last known Google Drive modifiedTime per file (for duplicate detection)
FILE_STATE = Path(os.getenv("FILE_STATE", Path(__file__).parent / "file_state.json"))

# Directory where generated ZIP packages are stored for download by n8n
ZIPS_DIR = Path(os.getenv("ZIPS_DIR", Path(__file__).parent / "zips"))

# Base URL n8n uses to reach this agent (from inside Docker)
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://host.docker.internal:8765")


# Drawing number regex patterns
WELDMENT_PATTERN = re.compile(r"^210-\d{5}-\d{2}", re.IGNORECASE)
SHEET_METAL_PATTERN = re.compile(r"^290-\d{5}-\d{2}", re.IGNORECASE)
MACHINING_PATTERN = re.compile(r"^300-\d{5}-\d{2}", re.IGNORECASE)

# Keywords used to locate the header row in a PDF part list table
HEADER_KEYWORDS = {"part", "no", "number", "description", "qty", "quantity", "item"}
