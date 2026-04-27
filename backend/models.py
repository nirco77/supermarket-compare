from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, field_validator, computed_field


class StoreProduct(BaseModel):
    store: Literal["ah", "jumbo", "dirk"]
    product_id: str
    name: str
    brand: str | None = None
    price: float
    discount_price: float | None = None
    unit: str = "stuks"
    quantity: float = 1.0
    quantity_label: str = ""
    price_per_unit: float = 0.0
    is_bulk: bool = False
    bulk_size: int | None = None
    url: str | None = None
    image_url: str | None = None
    promotion_label: str | None = None

    @computed_field
    @property
    def effective_price(self) -> float:
        return self.discount_price if self.discount_price is not None else self.price

    def model_post_init(self, __context):
        if self.price_per_unit == 0.0 and self.quantity > 0:
            self.price_per_unit = round(self.effective_price / self.quantity, 4)


class ShoppingListItem(BaseModel):
    raw_text: str
    quantity: int = 1


class SearchRequest(BaseModel):
    items: list[ShoppingListItem]
    stores: list[str] = ["ah", "jumbo", "dirk"]


class BulkSuggestion(BaseModel):
    product: StoreProduct
    regular_product: StoreProduct
    saving_percent: float
    saving_euros: float
    message: str


class ItemResult(BaseModel):
    query: str
    user_quantity: int = 1
    best_per_store: dict[str, StoreProduct | None]
    bulk_suggestion: BulkSuggestion | None = None


class Basket(BaseModel):
    store: str
    items: list[StoreProduct]
    total: float
    regular_total: float
    savings: float


class SplitBasket(BaseModel):
    primary_store: str
    secondary_store: str
    primary_items: list[StoreProduct]
    secondary_items: list[StoreProduct]
    total: float
    savings_vs_single_cheapest: float


class ComparisonResult(BaseModel):
    items: list[ItemResult]
    cheapest_single_store: Basket | None
    optimal_split: SplitBasket | None
    all_store_totals: dict[str, float]
    timestamp: str


class ConfirmPurchaseRequest(BaseModel):
    store: str
    items: list[StoreProduct]
    quantities: dict[str, int] = {}


class PurchaseRecord(BaseModel):
    id: int | None = None
    timestamp: str
    store: str
    product_id: str
    product_name: str
    brand: str | None = None
    price_paid: float
    regular_price: float | None = None
    quantity_bought: int = 1
    unit: str | None = None
    quantity_label: str | None = None


class HistoryStats(BaseModel):
    total_purchases: int
    total_spent: float
    total_saved: float
    store_breakdown: dict[str, float]
    top_products: list[dict]


class CredentialRequest(BaseModel):
    store: Literal["ah", "jumbo"]
    username: str
    password: str


class CredentialStatus(BaseModel):
    ah: bool
    jumbo: bool


class LoginRequest(BaseModel):
    store: Literal["ah", "jumbo"]


class LoginResult(BaseModel):
    success: bool
    method: str
    message: str = ""


class HealthResponse(BaseModel):
    status: str
    stores_reachable: dict[str, bool]
    authenticated: dict[str, bool]
