from __future__ import annotations
import httpx
import logging
from .. import config
from ..services.credential_service import get_credentials

logger = logging.getLogger(__name__)

JUMBO_LOGIN_URL = f"{config.JUMBO_API_BASE}/users/login"


async def login_api(username: str, password: str) -> bool:
    """Authenticate via Jumbo mobile API. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                JUMBO_LOGIN_URL,
                json={"username": username, "password": password},
                headers={
                    "User-Agent": "Jumbo/10.0.0 (Android)",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                token = (
                    data.get("data", {}).get("customer", {}).get("data", {}).get("token")
                    or data.get("token")
                    or resp.headers.get("x-jumbo-token")
                )
                if token:
                    config.store_token("jumbo", token, expires_in=86400)
                    logger.info("Jumbo: logged in via API")
                    return True
            logger.warning("Jumbo API login failed: %s", resp.status_code)
            return False
    except Exception as e:
        logger.warning("Jumbo API login exception: %s", e)
        return False


async def login_playwright(username: str, password: str) -> bool:
    """Fallback: log in via Playwright and persist session."""
    config.ensure_storage_dir()
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto("https://www.jumbo.com/account/login", timeout=20000)
            await page.wait_for_selector(
                "[data-testid='email-input'], input[name='email'], input[type='email']",
                timeout=8000,
            )

            await page.fill("[data-testid='email-input'], input[name='email'], input[type='email']", username)
            await page.fill("[data-testid='password-input'], input[name='password'], input[type='password']", password)
            await page.click("[data-testid='login-button'], button[type='submit']")
            await page.wait_for_url(lambda url: "login" not in url.lower(), timeout=10000)

            # Try to extract token from localStorage
            token = await page.evaluate("localStorage.getItem('jum_token') || localStorage.getItem('token')")
            if token:
                config.store_token("jumbo", token, expires_in=86400)

            await context.storage_state(path=str(config.JUMBO_SESSION_PATH))
            await browser.close()
            logger.info("Jumbo: logged in via Playwright, session saved")
            return True
    except Exception as e:
        logger.error("Jumbo Playwright login failed: %s", e)
        return False


async def login(username: str | None = None, password: str | None = None) -> tuple[bool, str]:
    """
    Try API login first, fall back to Playwright.
    Returns (success, method_used).
    """
    if username is None or password is None:
        creds = get_credentials("jumbo")
        if not creds:
            return False, "no_credentials"
        username, password = creds

    if await login_api(username, password):
        return True, "api"

    if await login_playwright(username, password):
        return True, "playwright"

    return False, "failed"
