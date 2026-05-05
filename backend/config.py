from __future__ import annotations
from pathlib import Path
import os
import time

# Use /tmp when running on Vercel or in a container (SUPERMARKET_STORAGE_DIR env var)
_custom_dir = os.environ.get("SUPERMARKET_STORAGE_DIR")
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _custom_dir:
    _STORAGE_BASE = Path(_custom_dir)
elif _IS_VERCEL:
    _STORAGE_BASE = Path("/tmp/supermarket-compare")
else:
    _STORAGE_BASE = Path.home() / ".config" / "supermarket-compare"

AH_API_BASE = "https://api.ah.nl/mobile/v1"
AH_AUTH_URL = "https://api.ah.nl/mobile/auth/v1/token"
AH_CLIENT_ID = "appie-android"
AH_CLIENT_SECRET = "vMEu_rSKQj2DXw8d"

JUMBO_API_BASE = "https://mobileapi.jumbo.com/v17"
DIRK_BASE_URL = "https://www.dirk.nl"

STORAGE_DIR = _STORAGE_BASE
DB_PATH = STORAGE_DIR / "history.db"
AH_SESSION_PATH = STORAGE_DIR / "ah_session.json"
JUMBO_SESSION_PATH = STORAGE_DIR / "jumbo_session.json"

SESSION_MAX_AGE_SECONDS = 86400  # 24 hours

BULK_SAVING_THRESHOLD = 0.10  # 10% per-unit saving triggers bulk suggestion

# In-memory token store: {"ah": {"token": str, "expires_at": float}, "jumbo": {...}}
_token_store: dict[str, dict] = {}


def store_token(store: str, token: str, expires_in: int = 3600):
    _token_store[store] = {
        "token": token,
        "expires_at": time.time() + expires_in - 60,  # 60s buffer
    }


def get_token(store: str) -> str | None:
    entry = _token_store.get(store)
    if entry and time.time() < entry["expires_at"]:
        return entry["token"]
    return None


def clear_token(store: str):
    _token_store.pop(store, None)


def is_session_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < SESSION_MAX_AGE_SECONDS


def ensure_storage_dir():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
