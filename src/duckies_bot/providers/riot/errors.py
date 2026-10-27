"""User-facing Riot API failures."""


class RiotAPIError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class InvalidRiotResponseError(RiotAPIError):
    def __init__(self) -> None:
        super().__init__("Riot returned an unexpected response. Please try again shortly.")

