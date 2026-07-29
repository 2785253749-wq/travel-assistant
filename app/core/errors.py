class AppError(Exception):
    """Base exception for expected application failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


ERROR_STATUS = {
    "AI_DAILY_LIMIT_REACHED": 429,
    "AI_GLOBAL_DAILY_LIMIT_REACHED": 429,
    "AI_DISABLED": 503,
}


def safe_error_detail(error: AppError) -> dict[str, str]:
    """Expose only stable public codes; never an upstream message or body."""
    messages = {
        "AI_DAILY_LIMIT_REACHED": "Daily AI request limit reached",
        "AI_GLOBAL_DAILY_LIMIT_REACHED": "AI service is temporarily at capacity",
        "AI_DISABLED": "AI service is temporarily disabled",
    }
    return {"code": error.code, "message": messages.get(error.code, "Service unavailable")}
