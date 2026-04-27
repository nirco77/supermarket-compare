from __future__ import annotations
import difflib
import logging
from ..models import StoreProduct, BulkSuggestion, ItemResult
from .. import config

logger = logging.getLogger(__name__)

NAME_SIMILARITY_THRESHOLD = 0.70
BULK_QUANTITY_RATIO = 1.5  # A product is "bulk" if its quantity is 1.5x a comparable product


def _find_bulk_suggestions(products: list[StoreProduct]) -> BulkSuggestion | None:
    """
    Given a list of product variants from a single query, find the best bulk suggestion.
    Returns a BulkSuggestion if a bulk pack saves >= BULK_SAVING_THRESHOLD vs the cheapest single pack.
    """
    if len(products) < 2:
        return None

    # Separate bulk-flagged products and regular ones
    regulars = [p for p in products if not p.is_bulk]
    bulks = [p for p in products if p.is_bulk]

    # Also detect bulk by quantity: if one product has >1.5x the quantity of another similar-named one
    if not bulks:
        for i, p in enumerate(products):
            for j, q in enumerate(products):
                if i == j:
                    continue
                name_sim = difflib.SequenceMatcher(None, p.name.lower(), q.name.lower()).ratio()
                if name_sim > NAME_SIMILARITY_THRESHOLD and q.quantity > p.quantity * BULK_QUANTITY_RATIO:
                    # q is the bulk version of p
                    if q not in bulks:
                        bulks.append(q)
                    if p not in regulars:
                        regulars.append(p)

    if not bulks or not regulars:
        return None

    # Find cheapest regular and cheapest bulk
    best_regular = min(regulars, key=lambda p: p.price_per_unit if p.price_per_unit > 0 else p.effective_price)
    best_bulk = min(bulks, key=lambda p: p.price_per_unit if p.price_per_unit > 0 else p.effective_price / p.quantity)

    if best_regular.price_per_unit == 0 or best_bulk.price_per_unit == 0:
        return None
    if best_regular.unit != best_bulk.unit:
        return None

    saving_pct = (best_regular.price_per_unit - best_bulk.price_per_unit) / best_regular.price_per_unit
    if saving_pct < config.BULK_SAVING_THRESHOLD:
        return None

    saving_euros = round(
        (best_regular.price_per_unit - best_bulk.price_per_unit) * best_bulk.quantity, 2
    )
    saving_pct_display = round(saving_pct * 100, 1)

    message = (
        f"Buy {best_bulk.quantity_label or 'bulk pack'} for €{best_bulk.effective_price:.2f} "
        f"instead of €{best_regular.price_per_unit:.2f}/{best_regular.unit} — "
        f"saves {saving_pct_display}% per {best_regular.unit}"
    )

    return BulkSuggestion(
        product=best_bulk,
        regular_product=best_regular,
        saving_percent=saving_pct_display,
        saving_euros=saving_euros,
        message=message,
    )


def attach_bulk_suggestions(item_results: list[ItemResult], store_raw_results: dict[str, dict[str, list[StoreProduct]]]):
    """
    Mutates item_results in-place, attaching bulk suggestions.
    store_raw_results: { item_query: { store: [StoreProduct, ...] } }
    """
    for ir in item_results:
        per_store = store_raw_results.get(ir.query, {})
        best_suggestion = None
        # Only compare products within the same store — cross-store comparisons
        # mix incompatible units (e.g. stuks vs kg) and produce nonsense savings %.
        for store_products in per_store.values():
            suggestion = _find_bulk_suggestions(store_products)
            if suggestion and (best_suggestion is None or suggestion.saving_percent > best_suggestion.saving_percent):
                best_suggestion = suggestion
        if best_suggestion:
            ir.bulk_suggestion = best_suggestion
            logger.debug("Bulk suggestion for '%s': %s", ir.query, best_suggestion.message)
