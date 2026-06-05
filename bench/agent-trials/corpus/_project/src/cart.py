"""Shopping cart: in-memory representation + Redis-backed persistence."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import redis


@dataclass
class CartItem:
    product_id: str
    quantity: int
    unit_price_cents: int

    @property
    def subtotal_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Cart:
    user_id: str
    items: Dict[str, CartItem] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    @property
    def total_cents(self) -> int:
        return sum(item.subtotal_cents for item in self.items.values())

    def add(self, product_id: str, quantity: int, unit_price_cents: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if product_id in self.items:
            self.items[product_id].quantity += quantity
        else:
            self.items[product_id] = CartItem(product_id, quantity, unit_price_cents)
        self.updated_at = time.time()

    def remove(self, product_id: str) -> None:
        self.items.pop(product_id, None)
        self.updated_at = time.time()


class CartStore:
    """Redis-backed cart persistence. Key: cart:{user_id}, TTL 7 days."""

    TTL_SECONDS = 7 * 24 * 3600

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _key(self, user_id: str) -> str:
        return f"cart:{user_id}"

    def get(self, user_id: str) -> Cart:
        raw = self.redis.get(self._key(user_id))
        if raw is None:
            return Cart(user_id=user_id)
        data = json.loads(raw)
        items = {pid: CartItem(**i) for pid, i in data["items"].items()}
        return Cart(user_id=user_id, items=items, updated_at=data["updated_at"])

    def save(self, cart: Cart) -> None:
        payload = {
            "items": {pid: {"product_id": i.product_id, "quantity": i.quantity,
                            "unit_price_cents": i.unit_price_cents}
                      for pid, i in cart.items.items()},
            "updated_at": cart.updated_at,
        }
        self.redis.setex(self._key(cart.user_id), self.TTL_SECONDS, json.dumps(payload))

    def clear(self, user_id: str) -> None:
        self.redis.delete(self._key(user_id))
