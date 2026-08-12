"""Steam identifier parsing shared by account-aware commands."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse


STEAM_ID64_OFFSET = 76_561_197_960_265_728
MAX_ACCOUNT_ID = 2**32 - 1


class SteamIDError(ValueError):
    """Raised when user input cannot be converted to a Steam account ID."""


@dataclass(frozen=True, slots=True)
class SteamAccount:
    account_id: int
    steam_id64: int

    @property
    def profile_url(self) -> str:
        return f"https://steamcommunity.com/profiles/{self.steam_id64}"


def parse_steam_id(value: str) -> SteamAccount:
    """Parse SteamID64, SteamID3, SteamID2, or a numeric profile URL."""

    text = value.strip().strip("<>")
    if not text:
        raise SteamIDError("Enter a Steam ID or numeric Steam profile URL.")

    if "://" in text:
        parsed = urlparse(text)
        if parsed.netloc.casefold() not in {"steamcommunity.com", "www.steamcommunity.com"}:
            raise SteamIDError("That is not a Steam Community profile URL.")
        match = re.fullmatch(r"/profiles/(\d+)/?", parsed.path)
        if match is None:
            raise SteamIDError(
                "Vanity profile URLs are not supported yet; use the account's numeric SteamID64."
            )
        text = match.group(1)

    steam3 = re.fullmatch(r"\[?U:1:(\d+)\]?", text, flags=re.IGNORECASE)
    if steam3:
        return _from_account_id(int(steam3.group(1)))

    steam2 = re.fullmatch(r"STEAM_[0-5]:([01]):(\d+)", text, flags=re.IGNORECASE)
    if steam2:
        return _from_account_id(int(steam2.group(2)) * 2 + int(steam2.group(1)))

    if not text.isdecimal():
        raise SteamIDError(
            "Use a SteamID64, SteamID3, SteamID2, or numeric Steam profile URL."
        )

    numeric = int(text)
    if numeric >= STEAM_ID64_OFFSET:
        account_id = numeric - STEAM_ID64_OFFSET
        account = _from_account_id(account_id)
        if account.steam_id64 != numeric:
            raise SteamIDError("That SteamID64 is outside the supported account range.")
        return account
    return _from_account_id(numeric)


def _from_account_id(account_id: int) -> SteamAccount:
    if not 0 < account_id <= MAX_ACCOUNT_ID:
        raise SteamIDError("The Steam account ID must be between 1 and 4294967295.")
    return SteamAccount(account_id, STEAM_ID64_OFFSET + account_id)
