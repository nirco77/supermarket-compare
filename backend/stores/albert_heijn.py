from __future__ import annotations
import re
import json
import logging
from urllib.parse import quote
import httpx
from .base import StoreClient
from ..models import StoreProduct

logger = logging.getLogger(__name__)

AH_SEARCH_URL = "https://www.ah.nl/zoeken"
AH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "nl-NL,nl;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Matches the product JSON objects embedded in Next.js RSC HTML
PRODUCT_PATTERN = re.compile(
    r'"product":\{"__typename":"Product","priceV2":\{(?P<price>[^}]+)\}[^}]*\}[^,]*(?:,"imagePack":\[(?P<images>[^\]]*)\])?[^,]*,"id":(?P<id>\d+),"hqId":\d+,"title":"(?P<title>[^"]+)","brand":(?P<brand>"[^"]*"|null)[^}]*"webPath":"(?P<path>[^"]*)"',
    re.DOTALL,
)

UNIT_PATTERN = re.compile(r'"unitSize":"(?P<unit>[^"]+)"')


def _parse_money(amount_str: str) -> float:
    m = re.search(r'"amount":([\d.]+)', amount_str)
    return float(m.group(1)) if m else 0.0


def _parse_unit(text: str) -> tuple[float, str]:
    text = text.lower().strip()
    m = re.search(r"([\d.,]+)\s*(kg|gram|g|liter|l|ml|cl|stuks?|pak|fles|blik|rol)", text)
    if m:
        try:
            qty = float(m.group(1).replace(",", "."))
            unit = m.group(2).rstrip("s")
            # Normalize to base units so price_per_unit is comparable across stores
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
            pass
    return 1.0, "stuks"


async def _fetch_ah_search(query: str, client: httpx.AsyncClient) -> str:
    resp = await client.get(
        AH_SEARCH_URL,
        params={"query": query, "page": 1, "sorting": "RELEVANCE"},
        headers=AH_HEADERS,
    )
    resp.raise_for_status()
    return resp.text


def _extract_products_from_html(html: str, query: str) -> list[StoreProduct]:
    """
    AH server-renders product data in Next.js RSC chunks as escaped JSON inside <script> tags.
    We unescape the relevant sections and extract product data.
    """
    products: list[StoreProduct] = []

    # The data appears in escaped form: \"priceV2\" inside script strings
    # Two cases: escaped (inside JS string) or raw (direct JSON)
    escaped_marker = r'\"__typename\":\"Product\",\"priceV2\"'
    raw_marker = '"__typename":"Product","priceV2"'

    # Work with the escaped version - unescape sections containing product data
    # Find all script blocks that contain product data
    script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    combined = " ".join(script_blocks)

    # Unescape backslash-escaped JSON
    combined = combined.replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')

    # Product objects: "product":{"__typename":"Product", ...may have taxonomyPath before priceV2
    search_marker = '"product":{"__typename":"Product"'
    pos = 0
    found = 0

    while found < 10:
        idx = combined.find(search_marker, pos)
        if idx == -1:
            break
        pos = idx + 1

        # The product object starts at the { after "product":
        obj_start = idx + len('"product":')

        # Extract a reasonable chunk of JSON starting from obj_start
        chunk = combined[obj_start:obj_start + 8000]

        try:
            # Extract price fields
            price_now_m = re.search(r'"now":\{"[^"]*":"Money","amount":([\d.]+)\}', chunk)
            price_was_m = re.search(r'"was":\{"[^"]*":"Money","amount":([\d.]+)\}', chunk)
            title_m = re.search(r'"title":"([^"]+)"', chunk)
            id_m = re.search(r'"id":(\d+)', chunk)
            brand_m = re.search(r'"brand":(?:"([^"]*)"|(null))', chunk)
            path_m = re.search(r'"webPath":"([^"]+)"', chunk)
            # salesUnitSize is more reliable than unitSize for most products
            unit_m = re.search(r'"salesUnitSize":"([^"]+)"', chunk) or \
                     re.search(r'"unitSize":"([^"]+)"', chunk)
            discount_m = re.search(r'"discount":\{"[^"]*":"[^"]*","amount":([\d.]+)\}', chunk)
            image_m = re.search(r'"url":"(https://static\.ah\.nl/dam/product/[^"]+)"', chunk)
            promo_label_m = re.search(r'"description":"([^"]+)"', chunk)

            if not (price_now_m and title_m and id_m):
                continue

            price_now = float(price_now_m.group(1))
            price_was = float(price_was_m.group(1)) if price_was_m else price_now
            discount_amount = float(discount_m.group(1)) if discount_m else 0.0

            regular = price_was if price_was > price_now else price_now
            discounted = price_now if price_was > price_now else None

            promo_label: str | None = None

            # Handle percentage/amount discounts (price_now < price_was)
            if discounted is not None and promo_label_m:
                promo_label = promo_label_m.group(1)

            # Handle multi-buy deals where price_now == price_was
            if discounted is None and promo_label_m:
                desc = promo_label_m.group(1)
                desc_clean = desc.lower().replace(" ", "")
                # "2 + 1 gratis" style
                m_deal = re.match(r'(\d+)\+(\d+)gratis', desc_clean)
                if m_deal:
                    n, free = int(m_deal.group(1)), int(m_deal.group(2))
                    discounted = round(regular * n / (n + free), 2)
                    promo_label = desc
                else:
                    # "2 voor 9.99" / "3 voor 5" style
                    m_voor = re.match(r'(\d+)voor([\d.,]+)', desc_clean)
                    if m_voor:
                        n = int(m_voor.group(1))
                        total = float(m_voor.group(2).replace(",", "."))
                        effective_each = round(total / n, 2)
                        if effective_each < regular:
                            discounted = effective_each
                            promo_label = desc

            unit_text = unit_m.group(1) if unit_m else ""
            quantity, unit = _parse_unit(unit_text) if unit_text else (1.0, "stuks")
            effective = discounted if discounted is not None else regular
            ppu = round(effective / quantity, 4) if quantity > 0 else effective

            brand = brand_m.group(1) if brand_m and brand_m.group(1) else None
            product_id = id_m.group(1)
            title = title_m.group(1)
            web_path = path_m.group(1) if path_m else ""
            img_url = image_m.group(1) if image_m else None

            products.append(StoreProduct(
                store="ah",
                product_id=product_id,
                name=title,
                brand=brand,
                price=round(regular, 2),
                discount_price=round(discounted, 2) if discounted is not None else None,
                unit=unit,
                quantity=quantity,
                quantity_label=unit_text,
                price_per_unit=ppu,
                is_bulk=discount_amount > 0 or price_was > price_now or discounted is not None,
                promotion_label=promo_label,
                image_url=img_url,
                url=f"https://www.ah.nl{web_path}" if web_path else None,
            ))
            found += 1

        except Exception as e:
            logger.debug("AH product parse error: %s", e)
            continue

    return products


# Dutch grocery adjectives that should precede their noun in search queries.
# AH's search is order-sensitive: "halfvolle melk" ranks better than "melk halfvolle".
_DUTCH_MODIFIERS = {
    'halfvolle', 'volle', 'magere', 'verse', 'houdbare', 'houdbaar',
    'gezouten', 'ongezouten', 'biologische', 'biologisch', 'bio',
    'lactosevrije', 'lactosevrij', 'gerookte', 'gerookt',
    'jong', 'jonge', 'oud', 'oude', 'belegen',
}


def _normalize_query(query: str) -> str:
    """Strip size/unit specs and ensure Dutch adjectives precede their noun."""
    cleaned = re.sub(
        r'\b\d+[\s,.]?\d*\s*(liter|ltr|stuks?|pak|fles|blik|rol|cl|ml|kg|gram|gr|g)\b',
        '', query, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'\b\d+\s*[lL]\b', '', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()

    # Reorder: [noun modifier1 modifier2 ...] → [modifier1 modifier2 ... noun]
    words = cleaned.split()
    if len(words) >= 2:
        modifiers = [w for w in words if w.lower() in _DUTCH_MODIFIERS]
        nouns = [w for w in words if w.lower() not in _DUTCH_MODIFIERS]
        if modifiers and nouns and words[0].lower() not in _DUTCH_MODIFIERS:
            cleaned = ' '.join(modifiers + nouns)

    return cleaned or query


class AHClient(StoreClient):
    store_name = "ah"

    async def search(self, query: str, token: str | None = None) -> list[StoreProduct]:
        search_query = _normalize_query(query)
        if search_query != query:
            logger.debug("AH query normalized: %r → %r", query, search_query)
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                html = await _fetch_ah_search(search_query, client)
            products = _extract_products_from_html(html, search_query)
            logger.info("AH: found %d products for '%s'", len(products), query)
            return products
        except Exception as e:
            logger.warning("AH search failed for '%s': %s", query, e)
            return []

    async def is_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get("https://www.ah.nl", headers=AH_HEADERS)
                return resp.status_code < 500
        except Exception:
            return False


async def close_ah_browser():
    pass  # No browser to close for HTTP-based scraping
