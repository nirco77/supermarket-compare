from __future__ import annotations
import httpx
import logging
from .. import config
from ..services.credential_service import get_credentials

logger = logging.getLogger(__name__)


async def login_api(username: str, password: str) -> bool:
    """Authenticate via AH OAuth2 API. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                config.AH_AUTH_URL,
                data={
                    "client_id": config.AH_CLIENT_ID,
                    "client_secret": config.AH_CLIENT_SECRET,
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                },
                headers={"User-Agent": "Appie/8.22.3"},
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                if token:
                    config.store_token("ah", token, expires_in)
                    logger.info("AH: logged in via API, token expires in %ds", expires_in)
                    return True
            logger.warning("AH API login failed: %s %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("AH API login exception: %s", e)
        return False


async def login_playwright(username: str, password: str) -> bool:
    """Fallback: log in via Playwright and persist session cookies."""
    config.ensure_storage_dir()
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto("https://www.ah.nl/mijn/inloggen", timeout=20000)
            await page.wait_for_selector("input[name='username'], #username, input[type='email']", timeout=8000)

            await page.fill("input[name='username'], #username, input[type='email']", username)
            await page.fill("input[name='password'], #password, input[type='password']", password)
            await page.click("button[type='submit'], .login-button, [data-testid='login-submit']")

            await page.wait_for_url(lambda url: "inloggen" not in url, timeout=10000)

            await context.storage_state(path=str(config.AH_SESSION_PATH))
            await browser.close()
            logger.info("AH: logged in via Playwright, session saved")
            return True
    except Exception as e:
        logger.error("AH Playwright login failed: %s", e)
        return False


async def login(username: str | None = None, password: str | None = None) -> tuple[bool, str]:
    """
    Try API login first, fall back to Playwright.
    Returns (success, method_used).
    """
    if username is None or password is None:
        creds = get_credentials("ah")
        if not creds:
            return False, "no_credentials"
        username, password = creds

    if await login_api(username, password):
        return True, "api"

    if await login_playwright(username, password):
        return True, "playwright"

    return False, "failed"
