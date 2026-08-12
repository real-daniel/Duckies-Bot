"""User-facing Deadlock API failures."""


class DeadlockAPIError(RuntimeError):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


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
