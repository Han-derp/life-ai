class AppError(Exception):
    """Base class for domain-level errors."""


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class DomainValidationError(AppError):
    pass


class ExternalServiceError(AppError):
    pass
