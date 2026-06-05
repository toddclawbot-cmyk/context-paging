"""Seed script for development data."""
from .models import Product
from .deps import get_session_local


SEED_PRODUCTS = [
    {"sku": "WIDGET-001", "name": "Standard Widget", "price_cents": 999, "inventory_count": 100},
    {"sku": "WIDGET-002", "name": "Premium Widget", "price_cents": 1999, "inventory_count": 50},
    {"sku": "GADGET-001", "name": "Pocket Gadget", "price_cents": 499, "inventory_count": 200},
    {"sku": "GADGET-002", "name": "Pro Gadget", "price_cents": 1499, "inventory_count": 75},
    {"sku": "GIZMO-001", "name": "Mini Gizmo", "price_cents": 299, "inventory_count": 300},
]


def seed():
    db = get_session_local()()
    try:
        existing = {p.sku for p in db.query(Product).all()}
        added = 0
        for spec in SEED_PRODUCTS:
            if spec["sku"] in existing:
                continue
            db.add(Product(**spec))
            added += 1
        db.commit()
        print(f"seeded {added} new products")
    finally:
        db.close()
