from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    ConflictError,
    DomainValidationError,
    ExternalServiceError,
    NotFoundError,
)


def register_error_handlers(app) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DomainValidationError)
    async def validation_handler(_: Request, exc: DomainValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ExternalServiceError)
    async def external_handler(_: Request, exc: ExternalServiceError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})
