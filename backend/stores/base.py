from __future__ import annotations
from abc import ABC, abstractmethod
from ..models import StoreProduct


class StoreClient(ABC):
    store_name: str

    @abstractmethod
    async def search(self, query: str, token: str | None = None) -> list[StoreProduct]:
        ...

    @abstractmethod
    async def is_reachable(self) -> bool:
        ...
