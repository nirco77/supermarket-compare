from __future__ import annotations
import re
import logging
from urllib.parse import quote
import httpx
from .base import StoreClient
from ..models import StoreProduct

logger = logging.getLogger(__name__)

JUMBO_GRAPHQL_URL = "https://www.jumbo.com/api/graphql"
JUMBO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": "https://www.jumbo.com/",
    "Accept-Language": "nl-NL,nl;q=0.9",
    "apollographql-client-name": "jumbo-web",
    "apollographql-client-version": "master-v31.6.1-web",
}

SEARCH_QUERY_TEMPLATE = """{{
  searchProducts(input: {{searchTerms: "{terms}", searchType: "keyword"}}) {{
    products {{
      sku title brand image
      subtitle: packSizeDisplay
      promotions {{ id title tags {{ text }} }}
      price {{
        price
        promoPrice
        pricePerUnit {{ price quantity unit }}
      }}
    }}
    count
  }}
}}"""

# Promotion tag texts that don't represent a price discount
_PROMO_SKIP = ("bezorging", "online", "bezorgkorting", "vanaf €", "vanaf euro")


def _parse_promotion_tag(text: str, regular_cents: int) -> float | None:
    """Return effective per-unit price in euros from a Jumbo promotion tag, or None."""
    # Strip zero-width and control characters, lowercase
    clean = "".join(c for c in text if c.isprintable() and ord(c) > 31).strip().lower()

    if any(skip in clean for skip in _PROMO_SKIP):
        return None

    # N+M gratis  →  pay N, get M free  →  effective = price × N/(N+M)
    m = re.match(r"(\d+)\+(\d+)\s*gratis", clean)
    if m:
        n, free = int(m.group(1)), int(m.group(2))
        return round(regular_cents * n / (n + free) / 100, 2)

    # N voor X,XX  →  effective per unit = total / N
    m = re.match(r"(\d+)\s*voor\s*([\d,.]+)", clean)
    if m:
        n = int(m.group(1))
        total = float(m.group(2).replace(",", "."))
        effective = round(total / n, 2)
        if effective < regular_cents / 100:
            return effective

    # voor X,XX  →  explicit promo price
    m = re.match(r"voor\s*([\d,.]+)", clean)
    if m:
        price = float(m.group(1).replace(",", "."))
        if price < regular_cents / 100:
            return price

    # X% korting
    m = re.match(r"(\d+)%\s*korting", clean)
    if m:
        pct = int(m.group(1))
        return round(regular_cents * (100 - pct) / 100 / 100, 2)

    return None


def _parse_quantity(subtitle: str, ppu_quantity: str, ppu_unit: str) -> tuple[float, str]:
    """Extract numeric quantity and unit from packSizeDisplay or pricePerUnit fields."""
    for text in [subtitle, ppu_quantity + " " + ppu_unit]:
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


class JumboClient(StoreClient):
    store_name = "jumbo"

    async def search(self, query: str, token: str | None = None) -> list[StoreProduct]:
        headers = dict(JUMBO_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                inline_q = SEARCH_QUERY_TEMPLATE.format(terms=query.replace('"', ""))
                resp = await client.post(
                    JUMBO_GRAPHQL_URL,
                    json={"query": inline_q},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

        except Exception as e:
            logger.warning("Jumbo GraphQL search failed for '%s': %s", query, e)
            return []

        if data.get("errors"):
            logger.warning("Jumbo GraphQL errors for '%s': %s", query, data["errors"][0].get("message", ""))
            return []

        products_data = (
            data.get("data", {}).get("searchProducts", {}).get("products", []) or []
        )
        results = []
        for item in products_data[:10]:
            try:
                product = self._parse_product(item)
                if product:
                    results.append(product)
            except Exception as e:
                logger.debug("Jumbo product parse error: %s", e)

        logger.info("Jumbo: found %d products for '%s'", len(results), query)
        return results

    def _parse_product(self, item: dict) -> StoreProduct | None:
        price_obj = item.get("price") or {}
        # Prices are in cents
        regular_cents = price_obj.get("price", 0)
        promo_cents = price_obj.get("promoPrice")
        ppu_obj = price_obj.get("pricePerUnit") or {}

        if regular_cents == 0:
            return None

        regular = regular_cents / 100
        promo = promo_cents / 100 if promo_cents else None
        promo_label: str | None = None

        # Parse promotion tags when promoPrice is not set
        if promo is None:
            for promotion in item.get("promotions") or []:
                for tag in promotion.get("tags") or []:
                    tag_text = tag.get("text") or ""
                    effective = _parse_promotion_tag(tag_text, regular_cents)
                    if effective is not None and effective < regular:
                        promo = effective
                        # Clean zero-width spaces from label
                        promo_label = "".join(
                            c for c in tag_text if c.isprintable() and ord(c) > 31
                        ).strip()
                        break
                if promo is not None:
                    break
        elif promo_cents:
            # promoPrice is set — find any matching promotion label
            for promotion in item.get("promotions") or []:
                for tag in promotion.get("tags") or []:
                    tag_text = (tag.get("text") or "").strip()
                    clean = "".join(c for c in tag_text if c.isprintable() and ord(c) > 31).strip()
                    if clean and not any(s in clean.lower() for s in _PROMO_SKIP):
                        promo_label = clean
                        break
                if promo_label:
                    break

        subtitle = item.get("subtitle") or ""
        ppu_qty = str(ppu_obj.get("quantity") or "1")
        ppu_unit = str(ppu_obj.get("unit") or "")
        quantity, unit = _parse_quantity(subtitle, ppu_qty, ppu_unit)

        effective = promo if promo is not None else regular
        ppu = round(effective / quantity, 4) if quantity > 0 else effective

        return StoreProduct(
            store="jumbo",
            product_id=item.get("sku", ""),
            name=item.get("title", ""),
            brand=item.get("brand"),
            price=round(regular, 2),
            discount_price=round(promo, 2) if promo is not None else None,
            unit=unit,
            quantity=quantity,
            quantity_label=subtitle,
            price_per_unit=ppu,
            is_bulk=promo is not None,
            promotion_label=promo_label,
            image_url=item.get("image"),
            url=f"https://www.jumbo.com/producten/?searchType=keyword&searchTerms={quote(item.get('title', ''))}" if item.get("title") else None,
        )

    async def is_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get("https://www.jumbo.com", headers={"User-Agent": "Mozilla/5.0"})
                return resp.status_code < 500
        except Exception:
            return False


async def close_jumbo_browser():
    pass  # No browser needed for GraphQL API
