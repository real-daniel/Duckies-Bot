"""User-facing Deadlock API failures."""


class DeadlockAPIError(RuntimeError):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class LiveDemoUnavailableError(DeadlockAPIError):
    """Raised when Valve's Source TV endpoint cannot serve the broadcast."""

    def __init__(self) -> None:
        super().__init__(
            "Valve's live broadcast endpoint is unavailable. Its URL may be stale, "
            "or the live parser may be unable to reach Valve's CDN."
        )


class LivePlayerDataTimeoutError(DeadlockAPIError):
    """Raised when a parser stream opens but produces no player snapshot."""

    def __init__(self) -> None:
        super().__init__(
            "The live broadcast did not produce player data before timing out."
        )


class LiveBroadcastEndedBeforeDataError(DeadlockAPIError):
    """Raised when a parser stream ends before producing a player snapshot."""

    def __init__(self) -> None:
        super().__init__("The live broadcast ended without returning player data.")


class LiveBroadcastStreamError(DeadlockAPIError):
    """Raised when the parser reports a Valve relay or demo parsing failure."""

    def __init__(self) -> None:
        super().__init__(
            "The live broadcast stream failed before returning player data."
        )


class InvalidDeadlockResponseError(DeadlockAPIError):
    def __init__(self) -> None:
        super().__init__("The Deadlock API returned an unexpected response.")


class InvalidDeadlockScreenshotError(DeadlockAPIError):
    def __init__(
        self,
        detail: str = "Upload a PNG, JPEG, or WebP Deadlock screenshot.",
    ) -> None:
        super().__init__(detail)


class MatchIDNotRecognizedError(DeadlockAPIError):
    def __init__(self) -> None:
        super().__init__(
            "I couldn't read a match ID from the bottom-right corner. "
            "Try a full-resolution screenshot or enter the match ID manually."
        )
