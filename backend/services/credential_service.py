from __future__ import annotations
import logging
import keyring

logger = logging.getLogger(__name__)
SERVICE_NAME = "supermarket-compare"


def save_credentials(store: str, username: str, password: str):
    keyring.set_password(SERVICE_NAME, f"{store}_username", username)
    keyring.set_password(SERVICE_NAME, f"{store}_password", password)
    logger.info("Saved credentials for %s to Keychain", store)


def get_credentials(store: str) -> tuple[str, str] | None:
    username = keyring.get_password(SERVICE_NAME, f"{store}_username")
    password = keyring.get_password(SERVICE_NAME, f"{store}_password")
    if username and password:
        return username, password
    return None


def credentials_exist(store: str) -> bool:
    return get_credentials(store) is not None


def delete_credentials(store: str):
    try:
        keyring.delete_password(SERVICE_NAME, f"{store}_username")
        keyring.delete_password(SERVICE_NAME, f"{store}_password")
    except keyring.errors.PasswordDeleteError:
        pass
