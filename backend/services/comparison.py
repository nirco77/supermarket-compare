from __future__ import annotations
import asyncio
import difflib
import logging
import re
from datetime import datetime, timezone
from itertools import combinations

from ..models import (
    ShoppingListItem, StoreProduct, ItemResult,
    Basket, SplitBasket, ComparisonResult,
)
from ..stores.albert_heijn import AHClient
from ..stores.jumbo import JumboClient
from ..stores.dirk import DirkClient
from .. import config

logger = logging.getLogger(__name__)

STORES = ["ah", "jumbo", "dirk"]
_clients = {
    "ah": AHClient(),
    "jumbo": JumboClient(),
    "dirk": DirkClient(),
}


# Qualifiers that indicate a product specialisation the user didn't ask for.
# Each entry is (search_pattern_in_name, pattern_to_check_in_query).
_IMPLICIT_QUALIFIERS = [
    (re.compile(r'houdba(ar|re)', re.I), re.compile(r'houdba(ar|re)', re.I)),
    (re.compile(r'biologisch[e]?|(?<!\w)bio(?!\w)', re.I), re.compile(r'biologisch|(?<!\w)bio(?!\w)', re.I)),
    (re.compile(r'lactose[-\s]?vrij[e]?', re.I), re.compile(r'lactose[-\s]?vrij', re.I)),
]
_QUALIFIER_PENALTY = 0.2


def _best_match(query: str, products: list[StoreProduct]) -> StoreProduct | None:
    """Pick the best matching product using keyword coverage + size match, cheapest breaks ties.

    Products that contain a specialisation qualifier (houdbaar, biologisch, lactosevrij)
    not present in the query are penalised so plain variants are preferred.
    """
    if not products:
        return None
    q = query.lower()
    # Meaningful keywords: len >= 3, non-numeric
    q_words = [w for w in re.split(r'\W+', q) if len(w) >= 3 and not w.isdigit()]

    scored = []
    for p in products:
        name_lower = p.name.lower()
        if q_words:
            kw_score = sum(1 for w in q_words if w in name_lower) / len(q_words)
        else:
            kw_score = difflib.SequenceMatcher(None, q, name_lower).ratio()

        size_bonus = 0.0
        if p.quantity_label:
            ql = p.quantity_label.lower().replace("gram", "g").replace("gr", "g").replace("liter", "l")
            q_norm = q.replace("gram", "g").replace("gr", "g").replace("liter", "l").replace(" ", "")
            ql_norm = ql.replace(" ", "")
            if ql_norm and ql_norm in q_norm:
                size_bonus = 0.1

        qualifier_penalty = sum(
            _QUALIFIER_PENALTY
            for name_pat, query_pat in _IMPLICIT_QUALIFIERS
            if name_pat.search(name_lower) and not query_pat.search(q)
        )

        scored.append((kw_score + size_bonus - qualifier_penalty, p))
    scored.sort(key=lambda x: (-x[0], x[1].effective_price))
    return scored[0][1]


async def _search_store(store: str, query: str) -> list[StoreProduct]:
    client = _clients[store]
    token = config.get_token(store)
    try:
        return await client.search(query, token=token)
    except Exception as e:
        logger.warning("Store %s search failed: %s", store, e)
        return []


async def _search_item(item: ShoppingListItem, stores: list[str]) -> dict[str, list[StoreProduct]]:
    """Search all stores in parallel for a single item."""
    tasks = {store: _search_store(store, item.raw_text) for store in stores}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        store: (res if isinstance(res, list) else [])
        for store, res in zip(tasks.keys(), results)
    }


def _build_item_result(item: ShoppingListItem, store_results: dict[str, list[StoreProduct]]) -> ItemResult:
    best_per_store: dict[str, StoreProduct | None] = {}
    for store, products in store_results.items():
        best_per_store[store] = _best_match(item.raw_text, products)
    return ItemResult(
        query=item.raw_text,
        user_quantity=item.quantity,
        best_per_store=best_per_store,
    )


def _basket_total(item_results: list[ItemResult], store: str) -> float | None:
    """Total cost at a single store. Returns None if any item is missing."""
    total = 0.0
    for ir in item_results:
        product = ir.best_per_store.get(store)
        if product is None:
            return None
        total += product.effective_price * ir.user_quantity
    return round(total, 2)


def _build_basket(item_results: list[ItemResult], store: str) -> Basket | None:
    items = []
    total = 0.0
    regular_total = 0.0
    for ir in item_results:
        product = ir.best_per_store.get(store)
        if product is None:
            return None
        total += product.effective_price * ir.user_quantity
        regular_total += product.price * ir.user_quantity
        items.append(product)
    return Basket(
        store=store,
        items=items,
        total=round(total, 2),
        regular_total=round(regular_total, 2),
        savings=round(regular_total - total, 2),
    )


def _find_cheapest_single(item_results: list[ItemResult], stores: list[str]) -> Basket | None:
    best: Basket | None = None
    for store in stores:
        basket = _build_basket(item_results, store)
        if basket is not None and (best is None or basket.total < best.total):
            best = basket
    return best


def _find_optimal_split(item_results: list[ItemResult], stores: list[str], single_cheapest_total: float) -> SplitBasket | None:
    """Try all 2-store combinations and return the cheapest split."""
    best_split: SplitBasket | None = None
    best_total = single_cheapest_total  # Only beat the single store

    for store_a, store_b in combinations(stores, 2):
        primary_items: list[StoreProduct] = []
        secondary_items: list[StoreProduct] = []
        total = 0.0

        for ir in item_results:
            p_a = ir.best_per_store.get(store_a)
            p_b = ir.best_per_store.get(store_b)

            if p_a is None and p_b is None:
                total = float("inf")
                break
            elif p_a is None:
                secondary_items.append(p_b)
                total += p_b.effective_price * ir.user_quantity
            elif p_b is None:
                primary_items.append(p_a)
                total += p_a.effective_price * ir.user_quantity
            else:
                if p_a.effective_price <= p_b.effective_price:
                    primary_items.append(p_a)
                    total += p_a.effective_price * ir.user_quantity
                else:
                    secondary_items.append(p_b)
                    total += p_b.effective_price * ir.user_quantity

        total = round(total, 2)
        if total < best_total:
            best_total = total
            best_split = SplitBasket(
                primary_store=store_a,
                secondary_store=store_b,
                primary_items=primary_items,
                secondary_items=secondary_items,
                total=total,
                savings_vs_single_cheapest=round(single_cheapest_total - total, 2),
            )

    return best_split


async def compare(items: list[ShoppingListItem], stores: list[str] | None = None) -> ComparisonResult:
    if stores is None:
        stores = STORES

    # Search all stores for all items in parallel
    all_results = await asyncio.gather(*[_search_item(item, stores) for item in items])

    item_results = [
        _build_item_result(item, store_results)
        for item, store_results in zip(items, all_results)
    ]

    # Compute totals per store
    all_store_totals = {}
    for store in stores:
        total = _basket_total(item_results, store)
        if total is not None:
            all_store_totals[store] = total

    cheapest_single = _find_cheapest_single(item_results, stores)
    single_total = cheapest_single.total if cheapest_single else float("inf")
    optimal_split = _find_optimal_split(item_results, stores, single_total)

    return ComparisonResult(
        items=item_results,
        cheapest_single_store=cheapest_single,
        optimal_split=optimal_split,
        all_store_totals=all_store_totals,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
