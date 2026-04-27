from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
SERVICE_NAME = "supermarket-compare"

try:
    import keyring as _keyring
    _KEYRING_AVAILABLE = True
except Exception:
    _keyring = None  # type: ignore
    _KEYRING_AVAILABLE = False


def save_credentials(store: str, username: str, password: str):
    if not _KEYRING_AVAILABLE:
        logger.warning("Keyring unavailable — credentials not saved")
        return
    _keyring.set_password(SERVICE_NAME, f"{store}_username", username)
    _keyring.set_password(SERVICE_NAME, f"{store}_password", password)
    logger.info("Saved credentials for %s to Keychain", store)


def get_credentials(store: str) -> tuple[str, str] | None:
    if not _KEYRING_AVAILABLE:
        return None
    try:
        username = _keyring.get_password(SERVICE_NAME, f"{store}_username")
        password = _keyring.get_password(SERVICE_NAME, f"{store}_password")
        if username and password:
            return username, password
    except Exception:
        pass
    return None


def credentials_exist(store: str) -> bool:
    return get_credentials(store) is not None


def delete_credentials(store: str):
    if not _KEYRING_AVAILABLE:
        return
    try:
        _keyring.delete_password(SERVICE_NAME, f"{store}_username")
        _keyring.delete_password(SERVICE_NAME, f"{store}_password")
    except Exception:
        pass
