"""Cart HTTP endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List
from .deps import get_db, get_current_user
from .models import Product, User
from .cart import CartStore, Cart
from .deps import get_redis

router = APIRouter(prefix="/cart", tags=["cart"])


class AddItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=100)


class CartItemView(BaseModel):
    product_id: str
    quantity: int
    unit_price_cents: int
    subtotal_cents: int


class CartView(BaseModel):
    user_id: str
    items: List[CartItemView]
    total_cents: int


def _to_view(cart: Cart) -> CartView:
    return CartView(
        user_id=cart.user_id,
        items=[CartItemView(product_id=i.product_id, quantity=i.quantity,
                            unit_price_cents=i.unit_price_cents, subtotal_cents=i.subtotal_cents)
               for i in cart.items.values()],
        total_cents=cart.total_cents,
    )


@router.get("", response_model=CartView)
def get_cart(current_user: User = Depends(get_current_user),
             redis_client = Depends(get_redis)) -> CartView:
    store = CartStore(redis_client)
    return _to_view(store.get(current_user.id))


@router.post("/items", response_model=CartView, status_code=201)
def add_item(req: AddItemRequest, current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db), redis_client = Depends(get_redis)) -> CartView:
    product = db.query(Product).filter(Product.id == req.product_id).one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    store = CartStore(redis_client)
    cart = store.get(current_user.id)
    cart.add(req.product_id, req.quantity, product.price_cents)
    store.save(cart)
    return _to_view(cart)


@router.delete("/items/{product_id}", response_model=CartView)
def remove_item(product_id: str, current_user: User = Depends(get_current_user),
                redis_client = Depends(get_redis)) -> CartView:
    store = CartStore(redis_client)
    cart = store.get(current_user.id)
    cart.remove(product_id)
    store.save(cart)
    return _to_view(cart)


@router.delete("", status_code=204)
def clear_cart(current_user: User = Depends(get_current_user),
               redis_client = Depends(get_redis)) -> None:
    CartStore(redis_client).clear(current_user.id)
