"""FastAPI app entry point."""
from __future__ import annotations
from fastapi import FastAPI
from .api_users import router as users_router
from .api_cart import router as cart_router
from .api_orders import router as orders_router


def create_app() -> FastAPI:
    app = FastAPI(title="MyShop API", version="0.4.2")
    app.include_router(users_router)
    app.include_router(cart_router)
    app.include_router(orders_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.4.2"}

    return app


app = create_app()
