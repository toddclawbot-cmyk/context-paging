"""Order service tests."""
import pytest
from myshop.orders import create_order_from_cart, cancel, OrderError, InventoryError
from myshop.cart import Cart


def test_create_from_empty_cart_raises():
    with pytest.raises(OrderError):
        create_order_from_cart(db=None, cart=Cart(user_id="u1"), shipping_address="123 Main St")


def test_create_requires_shipping_address():
    cart = Cart(user_id="u1")
    cart.add("p1", 1, 100)
    with pytest.raises(OrderError):
        create_order_from_cart(db=None, cart=cart, shipping_address="")


def test_create_requires_long_enough_address():
    cart = Cart(user_id="u1")
    cart.add("p1", 1, 100)
    with pytest.raises(OrderError):
        create_order_from_cart(db=None, cart=cart, shipping_address="abc")
