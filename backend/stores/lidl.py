from __future__ import annotations
import logging
import re
from urllib.parse import quote

from .base import StoreClient
from ..models import StoreProduct

logger = logging.getLogger(__name__)

LIDL_SEARCH_URL = "https://www.lidl.nl/q/search?q={query}"
LIDL_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

try:
    from playwright.async_api import async_playwright as _async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_playwright_instance = None
_browser = None


async def _ensure_browser():
    global _playwright_instance, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    if _playwright_instance is None:
        _playwright_instance = await _async_playwright().start()
    _browser = await _playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    return _browser


async def close_browser():
    global _playwright_instance, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            await _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None


def _parse_price(text: str) -> float:
    m = re.search(r"(\d+)[.,](\d+)", text)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m2 = re.search(r"\d+", text)
    return float(m2.group()) if m2 else 0.0


def _parse_quantity(packaging: str) -> tuple[float, str]:
    if not packaging:
        return 1.0, "stuks"
    text = packaging.lower().replace(",", ".")
    m = re.search(r"([\d.]+)\s*(kg|gram|g|liter|l|ml|cl|stuks?|pak|fles|blik|rol)", text)
    if not m:
        return 1.0, "stuks"
    try:
        qty = float(m.group(1))
        unit = m.group(2).rstrip("s")
        if unit in ("ml",):
            return qty / 1000, "liter"
        if unit in ("cl",):
            return qty / 100, "liter"
        if unit in ("gram", "g"):
            return qty / 1000, "kg"
        if unit == "l":
            return qty, "liter"
        return qty, unit
    except ValueError:
        return 1.0, "stuks"


_EXTRACT_JS = """
() => {
    const boxes = document.querySelectorAll('.product-grid-box');
    return [...boxes].map(box => {
        const title = box.querySelector('.product-grid-box__title')?.innerText?.trim() || '';
        const brand = box.querySelector('.product-grid-box__brand')?.innerText?.trim() || null;
        const link = box.querySelector('a[href]')?.href || '';
        const img = box.querySelector('img[src]')?.src || null;
        const priceEffective = box.querySelector('.ods-price__value')?.innerText?.trim() || '';
        const priceBox = box.querySelector('.product-grid-box__price')?.innerText?.trim() || '';
        const strokePrice = box.querySelector('.ods-price__stroke-price')?.innerText?.trim() || null;
        return {title, brand, link, img, priceEffective, priceBox, strokePrice};
    });
}
"""


def _normalize_query(query: str) -> str:
    """Strip size/quantity specs so Lidl's search finds the product, not appliances."""
    # Remove patterns like "1l", "500g", "1.5l", "2x1l", "250 ml"
    result = re.sub(r'\b\d+\s*[xX×]\s*[\d.,]+\s*(liter?|ltr|l|ml|cl|kg|g|gram|stuks?|pak)\b', '', query, flags=re.I)
    result = re.sub(r'\b[\d.,]+\s*(liter?|ltr|l|ml|cl|kg|g|gram)\b', '', result, flags=re.I)
    result = re.sub(r'\b\d+\s*[xX×]\s*\d+\b', '', result)
    return re.sub(r'\s{2,}', ' ', result).strip() or query


class LidlClient(StoreClient):
    store_name = "lidl"

    async def search(self, query: str, token: str | None = None) -> list[StoreProduct]:
        if not _PLAYWRIGHT_AVAILABLE:
            logger.info("Playwright not available — skipping Lidl")
            return []
        search_query = _normalize_query(query)
        if search_query != query:
            logger.debug("Lidl query normalized: %r → %r", query, search_query)
        try:
            browser = await _ensure_browser()
            page = await browser.new_page(user_agent=LIDL_UA)
            try:
                url = LIDL_SEARCH_URL.format(query=quote(search_query))
                await page.goto(url, timeout=30_000)
                await page.wait_for_selector(".product-grid-box", timeout=12_000)
                raw_items = await page.evaluate(_EXTRACT_JS)
            finally:
                await page.close()
        except Exception as exc:
            logger.warning("Lidl search failed for %r: %s", query, exc)
            return []

        results: list[StoreProduct] = []
        for item in raw_items:
            if not item["title"]:
                continue
            effective = _parse_price(item["priceEffective"])
            if effective <= 0:
                continue

            original: float | None = None
            if item["strokePrice"]:
                original = _parse_price(item["strokePrice"])

            # Last line of priceBox that looks like a size/weight
            packaging = ""
            for line in reversed((item["priceBox"] or "").split("\n")):
                line = line.strip()
                if re.search(r"\d+\s*(g|kg|ml|l|cl|stuks?|pak)\b", line, re.I):
                    packaging = line
                    break

            qty, unit = _parse_quantity(packaging)

            # Build a deduplicated name: skip brand prefix if already in title
            brand = item["brand"] or None
            name = item["title"]
            if brand and brand.lower() not in name.lower():
                name = f"{brand} {name}"

            product_id = re.search(r"/p(\d+)", item["link"])
            pid = product_id.group(1) if product_id else item["link"].split("/")[-1]

            results.append(StoreProduct(
                store="lidl",
                product_id=pid,
                name=name,
                brand=brand,
                price=original if original else effective,
                discount_price=effective if original else None,
                unit=unit,
                quantity=qty,
                quantity_label=packaging,
                url=item["link"] or None,
                image_url=item["img"] or None,
            ))

        return results

    async def is_reachable(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE
