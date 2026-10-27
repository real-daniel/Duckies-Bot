"""Riot Games API provider."""

from .client import PLATFORM_REGIONS, RiotClient, regional_route
from .errors import InvalidRiotResponseError, RiotAPIError

__all__ = [
    "InvalidRiotResponseError",
    "PLATFORM_REGIONS",
    "RiotAPIError",
    "RiotClient",
    "regional_route",
]
