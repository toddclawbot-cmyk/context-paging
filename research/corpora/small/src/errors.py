"""Custom exception types and FastAPI exception handlers."""
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

log = logging.getLogger(__name__)


class DomainError(Exception):
    """Base for application errors that should produce a 4xx response."""
    status_code = 400

    def __init__(self, message: str, code: str | None = None):
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(message)


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class AuthError(DomainError):
    status_code = 401


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle_domain(req: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "message": exc.message})
