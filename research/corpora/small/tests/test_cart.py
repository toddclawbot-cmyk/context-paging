"""Cart tests using fakeredis."""
import pytest
from myshop.cart import Cart, CartItem, CartStore


def test_add_and_total():
    cart = Cart(user_id="u1")
    cart.add("p1", 2, 500)
    cart.add("p2", 1, 1000)
    assert cart.total_cents == 2 * 500 + 1 * 1000


def test_add_same_product_increments_quantity():
    cart = Cart(user_id="u1")
    cart.add("p1", 2, 500)
    cart.add("p1", 3, 500)
    assert cart.items["p1"].quantity == 5


def test_add_zero_or_negative_raises():
    cart = Cart(user_id="u1")
    with pytest.raises(ValueError):
        cart.add("p1", 0, 100)
    with pytest.raises(ValueError):
        cart.add("p1", -1, 100)
