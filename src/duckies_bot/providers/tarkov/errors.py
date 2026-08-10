"""Expected failures raised by the Tarkov integration."""


class TarkovAPIError(Exception):
    def __init__(self, user_message: str, technical_message: str | None = None) -> None:
        self.user_message = user_message
        self.technical_message = technical_message
        super().__init__(user_message)

    def __str__(self) -> str:
        if self.technical_message:
            return f"{self.user_message} | Details: {self.technical_message}"
        return self.user_message

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"user_message={self.user_message!r}, "
            f"technical_message={self.technical_message!r})"
        )


class InvalidItemQueryError(TarkovAPIError):
    def __init__(self) -> None:
        super().__init__(user_message="Enter an item name to search for.")


class InvalidTaskQueryError(TarkovAPIError):
    def __init__(self) -> None:
        super().__init__(user_message="Enter a task name to search for.")


class InvalidQuestLogImageError(TarkovAPIError):
    def __init__(self, detail: str = "Upload a PNG, JPEG, or WebP quest-log screenshot.") -> None:
        super().__init__(user_message=detail)


class OCRUnavailableError(TarkovAPIError):
    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            user_message="Screenshot reading is temporarily unavailable.",
            technical_message=detail,
        )


class QuestLogNotRecognizedError(TarkovAPIError):
    def __init__(self) -> None:
        super().__init__(
            user_message=(
                "I couldn't confidently recognize any task names. "
                "Try a sharper screenshot with the task titles visible."
            )
        )


class ItemNotFoundError(TarkovAPIError):
    def __init__(self, item_query: str) -> None:
        self.query = item_query
        super().__init__(user_message=f"No Tarkov item was found for {item_query}.")


class TaskNotFoundError(TarkovAPIError):
    def __init__(self, task_query: str) -> None:
        self.query = task_query
        super().__init__(user_message=f"No PvE Tarkov task was found for {task_query}.")


class TarkovTimeoutError(TarkovAPIError):
    def __init__(self) -> None:
        super().__init__(user_message="The Tarkov API took too long to respond. Try again shortly.")


class TarkovRateLimitError(TarkovAPIError):
    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        if retry_after is not None:
            user_message = f"The Tarkov API is busy. Try again in {retry_after:g} seconds."
        else:
            user_message = "The Tarkov API is busy. Try again shortly."
        super().__init__(user_message=user_message)


class TarkovUnavailableError(TarkovAPIError):
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(user_message="Tarkov data is temporarily unavailable. Try again shortly.")


class InvalidTarkovResponseError(TarkovAPIError):
    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(
            user_message="The Tarkov API returned an unexpected response.",
            technical_message=detail,
        )
