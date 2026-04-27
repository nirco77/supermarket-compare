from __future__ import annotations
from datetime import datetime, timezone
from ..database import get_db
from ..models import StoreProduct, PurchaseRecord, HistoryStats


def record_purchase(store: str, items: list[StoreProduct], quantities: dict[str, int] | None = None):
    now = datetime.now(timezone.utc).isoformat()
    if quantities is None:
        quantities = {}

    with get_db() as conn:
        for product in items:
            qty = quantities.get(product.product_id, 1)
            conn.execute(
                """
                INSERT INTO purchase_history
                    (timestamp, store, product_id, product_name, brand,
                     price_paid, regular_price, quantity_bought, unit, quantity_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    store,
                    product.product_id,
                    product.name,
                    product.brand,
                    product.effective_price,
                    product.price,
                    qty,
                    product.unit,
                    product.quantity_label,
                ),
            )


def get_history(limit: int = 50, store: str | None = None, product_name: str | None = None) -> list[PurchaseRecord]:
    query = "SELECT * FROM purchase_history WHERE 1=1"
    params: list = []

    if store:
        query += " AND store = ?"
        params.append(store)
    if product_name:
        query += " AND product_name LIKE ?"
        params.append(f"%{product_name}%")

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        PurchaseRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            store=row["store"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            brand=row["brand"],
            price_paid=row["price_paid"],
            regular_price=row["regular_price"],
            quantity_bought=row["quantity_bought"],
            unit=row["unit"],
            quantity_label=row["quantity_label"],
        )
        for row in rows
    ]


def get_stats() -> HistoryStats:
    with get_db() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt, SUM(price_paid * quantity_bought) as spent, "
            "SUM((regular_price - price_paid) * quantity_bought) as saved "
            "FROM purchase_history WHERE regular_price IS NOT NULL"
        ).fetchone()

        store_rows = conn.execute(
            "SELECT store, SUM(price_paid * quantity_bought) as total "
            "FROM purchase_history GROUP BY store"
        ).fetchall()

        top_rows = conn.execute(
            "SELECT product_name, SUM(quantity_bought) as times_bought "
            "FROM purchase_history GROUP BY product_name "
            "ORDER BY times_bought DESC LIMIT 10"
        ).fetchall()

    return HistoryStats(
        total_purchases=total_row["cnt"] or 0,
        total_spent=round(total_row["spent"] or 0, 2),
        total_saved=round(total_row["saved"] or 0, 2),
        store_breakdown={row["store"]: round(row["total"], 2) for row in store_rows},
        top_products=[{"name": r["product_name"], "times_bought": r["times_bought"]} for r in top_rows],
    )
