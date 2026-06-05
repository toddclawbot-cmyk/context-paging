"""Order service: create, pay, ship, cancel."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from .models import Order, OrderItem, OrderStatus, Payment, Product, User
from .cart import CartStore, Cart


class OrderError(Exception):
    pass


class InventoryError(OrderError):
    pass


class PaymentError(OrderError):
    pass


def create_order_from_cart(db: Session, cart: Cart, shipping_address: str) -> Order:
    """Materialize a cart into a pending Order, decrementing inventory atomically."""
    if not cart.items:
        raise OrderError("cart is empty")
    if not shipping_address or len(shipping_address) < 5:
        raise OrderError("shipping address required")

    # Lock product rows and check inventory
    product_ids = list(cart.items.keys())
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).with_for_update().all()}

    for product_id, item in cart.items.items():
        product = products.get(product_id)
        if product is None:
            raise InventoryError(f"unknown product {product_id}")
        if product.inventory_count < item.quantity:
            raise InventoryError(f"insufficient inventory for {product.sku}: have {product.inventory_count}, need {item.quantity}")

    # Decrement inventory
    for product_id, item in cart.items.items():
        products[product_id].inventory_count -= item.quantity

    # Create the order
    order = Order(
        user_id=cart.user_id,
        status=OrderStatus.PENDING,
        total_cents=cart.total_cents,
        shipping_address=shipping_address,
    )
    db.add(order)
    db.flush()  # populate order.id

    for item in cart.items.values():
        db.add(OrderItem(
            order_id=order.id,
            product_id=item.product_id,
            quantity=item.quantity,
            unit_price_cents=item.unit_price_cents,
        ))

    return order


def mark_paid(db: Session, order: Order, provider: str, provider_charge_id: str) -> Payment:
    """Mark an order as paid and record the payment row. Idempotent by order_id."""
    existing = db.query(Payment).filter(Payment.order_id == order.id).one_or_none()
    if existing is not None:
        if existing.provider_charge_id == provider_charge_id and existing.succeeded:
            return existing
        raise PaymentError(f"payment already exists for order {order.id}")

    if order.status not in (OrderStatus.PENDING,):
        raise PaymentError(f"cannot pay for order in status {order.status.value}")

    payment = Payment(
        order_id=order.id,
        provider=provider,
        provider_charge_id=provider_charge_id,
        amount_cents=order.total_cents,
        succeeded=True,
    )
    db.add(payment)
    order.status = OrderStatus.PAID
    return payment


def cancel(db: Session, order: Order) -> None:
    """Cancel a pending or paid order, restocking inventory."""
    if order.status == OrderStatus.CANCELLED:
        return
    if order.status not in (OrderStatus.PENDING, OrderStatus.PAID):
        raise OrderError(f"cannot cancel order in status {order.status.value}")

    for item in order.items:
        product = db.query(Product).filter(Product.id == item.product_id).with_for_update().one()
        product.inventory_count += item.quantity

    order.status = OrderStatus.CANCELLED
