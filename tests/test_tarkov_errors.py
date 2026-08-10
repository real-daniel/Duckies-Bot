"""Tests for safe user messages and retained diagnostics."""

import unittest

from duckies_bot.providers.tarkov.errors import (
    InvalidTarkovResponseError,
    TarkovAPIError,
    TarkovRateLimitError,
    TarkovUnavailableError,
)


class TarkovErrorTests(unittest.TestCase):
    def test_expected_errors_share_a_base(self) -> None:
        self.assertIsInstance(TarkovRateLimitError(), TarkovAPIError)
        self.assertIsInstance(TarkovUnavailableError(), TarkovAPIError)

    def test_diagnostic_fields_are_retained(self) -> None:
        self.assertEqual(TarkovRateLimitError(2.5).retry_after, 2.5)
        self.assertEqual(TarkovUnavailableError(503).status_code, 503)

    def test_response_detail_is_not_exposed_to_users(self) -> None:
        error = InvalidTarkovResponseError("private diagnostic")
        self.assertNotIn("private diagnostic", error.user_message)
        self.assertIn("private diagnostic", str(error))
