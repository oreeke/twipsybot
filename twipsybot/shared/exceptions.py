__all__ = (
    "APIBadRequestError",
    "APIConnectionError",
    "APIFileTooLargeError",
    "APINotFoundError",
    "APIPermissionError",
    "APIRateLimitError",
    "APIResponseError",
    "AuthenticationError",
    "ConfigurationError",
    "TwipsyBotError",
    "WebSocketConnectionError",
    "WebSocketReconnectError",
)


class TwipsyBotError(Exception):
    pass


class ConfigurationError(TwipsyBotError):
    pass


class APIResponseError(TwipsyBotError):
    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        code: str | None = None,
        error_id: str | None = None,
        kind: str | None = None,
        info: object = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.error_id = error_id
        self.kind = kind
        self.info = info
        self.retry_after = retry_after


class AuthenticationError(APIResponseError):
    pass


class APIConnectionError(APIResponseError):
    pass


class APIRateLimitError(APIResponseError):
    pass


class APIBadRequestError(APIResponseError):
    pass


class APIPermissionError(APIResponseError):
    pass


class APINotFoundError(APIResponseError):
    pass


class APIFileTooLargeError(APIResponseError):
    pass


class WebSocketConnectionError(TwipsyBotError):
    pass


class WebSocketReconnectError(WebSocketConnectionError):
    pass
