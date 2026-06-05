"""Orders HTTP endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from .deps import get_db, get_current_user
from .models import User, Order
from .cart import CartStore
from .orders import create_order_from_cart, cancel, OrderError, InventoryError
from .settings import Settings
from .deps import get_settings

router = APIRouter(prefix="/orders", tags=["orders"])


class CheckoutRequest(BaseModel):
    shipping_address: str = Field(min_length=5, max_length=500)


class OrderItemView(BaseModel):
    product_id: str
    quantity: int
    unit_price_cents: int


class OrderView(BaseModel):
    id: str
    status: str
    total_cents: int
    shipping_address: str
    items: list[OrderItemView]


def _to_view(order: Order) -> OrderView:
    return OrderView(
        id=order.id,
        status=order.status.value,
        total_cents=order.total_cents,
        shipping_address=order.shipping_address,
        items=[OrderItemView(product_id=i.product_id, quantity=i.quantity,
                             unit_price_cents=i.unit_price_cents) for i in order.items],
    )


@router.post("/checkout", response_model=OrderView, status_code=201)
def checkout(req: CheckoutRequest, current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db), redis_client = Depends(get_redis)) -> OrderView:
    cart = CartStore(redis_client).get(current_user.id)
    try:
        order = create_order_from_cart(db, cart, req.shipping_address)
    except InventoryError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except OrderError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(order)
    # Clear the cart after successful checkout
    CartStore(redis_client).clear(current_user.id)
    return _to_view(order)


@router.post("/{order_id}/cancel", response_model=OrderView)
def cancel_order(order_id: str, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> OrderView:
    order = db.query(Order).filter(Order.id == order_id, Order.user_id == current_user.id).one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    try:
        cancel(db, order)
    except OrderError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(order)
    return _to_view(order)


@router.get("", response_model=list[OrderView])
def list_orders(current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> list[OrderView]:
    orders = db.query(Order).filter(Order.user_id == current_user.id).order_by(Order.created_at.desc()).all()
    return [_to_view(o) for o in orders]
