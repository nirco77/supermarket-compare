from __future__ import annotations
import re
import logging
from urllib.parse import quote
import httpx
from .base import StoreClient
from ..models import StoreProduct

logger = logging.getLogger(__name__)

DIRK_GRAPHQL_URL = "https://web-gateway.dirk.nl/graphql"
DIRK_IMAGE_BASE = "https://web-fileserver.dirk.nl/artikelen/"
DIRK_STORE_ID = 66

# API key is embedded in the public HTML — not a secret
DIRK_API_KEY = "6d3a42a3-6d93-4f98-838d-bcc0ab2307fd"

DIRK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-api-key": DIRK_API_KEY,
    "Referer": "https://www.dirk.nl/",
}

SEARCH_QUERY = """
{
  searchProducts(search: "%s", limit: 30) {
    products {
      ranking
      product {
        productId
        brand
        headerText
        packaging
        image
      }
    }
  }
}
"""

ASSORTMENT_QUERY = """
{
  productsAssortment(productIds: [%s], storeId: %d) {
    productId
    normalPrice
    offerPrice
    productInformation {
      headerText
      brand
      packaging
      image
    }
  }
}
"""


def _parse_quantity(text: str) -> tuple[float, str]:
    text = text.lower().strip().replace(",", ".")
    m = re.search(r"([\d.]+)\s*(kg|gram|g|liter|l|ml|cl|stuks?|pak|fles|blik|rol)", text)
    if m:
        try:
            qty = float(m.group(1))
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


def _normalize_query(query: str) -> str:
    """Normalize size units to full forms Dirk search understands; strip multi-pack notation."""
    # Remove multi-pack notation (e.g. '6 x 100ml', '4x250g') — confuses the search
    result = re.sub(
        r'\b\d+\s*[xX×]\s*\d+[\s,.]?\d*\s*(liter|ltr|stuks?|pak|fles|blik|rol|cl|ml|kg|gram|gr|g)\b',
        '', query, flags=re.IGNORECASE,
    )
    # Expand compact notations to full words so Dirk's search ranks correctly
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*liter\b', r'\1 liter', result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*ltr\b',   r'\1 liter', result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*[lL]\b',  r'\1 liter', result)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*gram\b',  r'\1 gram',  result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*gr\b',    r'\1 gram',  result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*g\b',     r'\1 gram',  result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*ml\b',    r'\1 ml',    result, flags=re.IGNORECASE)
    result = re.sub(r'\b(\d+[\s,.]?\d*)\s*cl\b',    r'\1 cl',    result, flags=re.IGNORECASE)
    return re.sub(r'\s{2,}', ' ', result).strip() or query


class DirkClient(StoreClient):
    store_name = "dirk"

    async def search(self, query: str, token: str | None = None) -> list[StoreProduct]:
        search_query = _normalize_query(query)
        if search_query != query:
            logger.debug("Dirk query normalized: %r → %r", query, search_query)
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                # Step 1: search for product IDs
                search_resp = await client.post(
                    DIRK_GRAPHQL_URL,
                    json={"query": SEARCH_QUERY % search_query.replace('"', "")},
                    headers=DIRK_HEADERS,
                )
                search_resp.raise_for_status()
                search_data = search_resp.json()

                if search_data.get("errors"):
                    logger.warning("Dirk search GraphQL errors for '%s': %s", query, search_data["errors"][0].get("message", ""))
                    return []

                products_raw = (
                    search_data.get("data", {}).get("searchProducts", {}).get("products", []) or []
                )
                if not products_raw:
                    return []

                # Collect product IDs in ranking order
                product_map: dict[int, dict] = {}
                for item in products_raw:
                    p = item.get("product", {})
                    pid = p.get("productId")
                    if pid:
                        product_map[pid] = p

                if not product_map:
                    return []

                # Step 2: fetch assortment (prices) for found products
                ids_str = ", ".join(str(i) for i in product_map)
                assort_resp = await client.post(
                    DIRK_GRAPHQL_URL,
                    json={"query": ASSORTMENT_QUERY % (ids_str, DIRK_STORE_ID)},
                    headers=DIRK_HEADERS,
                )
                assort_resp.raise_for_status()
                assort_data = assort_resp.json()

                if assort_data.get("errors"):
                    logger.warning("Dirk assortment GraphQL errors: %s", assort_data["errors"][0].get("message", ""))
                    return []

                assortment = assort_data.get("data", {}).get("productsAssortment", []) or []

        except Exception as e:
            logger.warning("Dirk GraphQL search failed for '%s': %s", query, e)
            return []

        results = []
        for item in assortment:
            try:
                product = self._parse_product(item)
                if product:
                    results.append(product)
            except Exception as e:
                logger.debug("Dirk product parse error: %s", e)

        logger.info("Dirk: found %d products for '%s'", len(results), query)
        return results

    def _parse_product(self, item: dict) -> StoreProduct | None:
        normal_price = item.get("normalPrice", 0) or 0
        offer_price = item.get("offerPrice", 0) or 0

        if normal_price == 0:
            return None

        regular = float(normal_price)
        promo = float(offer_price) if offer_price and offer_price > 0 else None

        info = item.get("productInformation") or {}
        title = info.get("headerText") or ""
        brand = info.get("brand") or None
        packaging = info.get("packaging") or ""
        image_file = info.get("image") or ""
        product_id = str(item.get("productId", ""))

        quantity, unit = _parse_quantity(packaging) if packaging else (1.0, "stuks")
        effective = promo if promo is not None else regular
        ppu = round(effective / quantity, 4) if quantity > 0 else effective

        image_url = f"{DIRK_IMAGE_BASE}{image_file}" if image_file else None

        return StoreProduct(
            store="dirk",
            product_id=product_id,
            name=title,
            brand=brand,
            price=round(regular, 2),
            discount_price=round(promo, 2) if promo is not None else None,
            unit=unit,
            quantity=quantity,
            quantity_label=packaging,
            price_per_unit=ppu,
            is_bulk=promo is not None,
            image_url=image_url,
            url=f"https://www.dirk.nl/zoeken?q={quote(title)}" if title else None,
        )

    async def is_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get("https://www.dirk.nl", headers={"User-Agent": "Mozilla/5.0"})
                return resp.status_code < 500
        except Exception:
            return False


async def close_browser():
    pass  # No browser needed for GraphQL API
