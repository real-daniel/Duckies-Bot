"""Tests for companion pairing persistence and the private HTTP receiver."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from duckies_bot.companion_api import create_companion_api
from duckies_bot.storage import CompanionPairingRepository


class CompanionPairingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = CompanionPairingRepository(
            Path(self.temp_dir.name) / "duckies.sqlite3"
        )
        await self.repository.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_pair_authenticate_rotate_and_disable(self) -> None:
        first, first_token = await self.repository.create(42, 10, 100)
        self.assertNotEqual(first.token_hash, first_token)
        self.assertEqual(await self.repository.authenticate(first_token), first)

        second, second_token = await self.repository.create(42, 10, 200)
        self.assertIsNone(await self.repository.authenticate(first_token))
        self.assertEqual(await self.repository.authenticate(second_token), second)
        self.assertEqual(second.channel_id, 200)

        self.assertTrue(await self.repository.disable(42, 10))
        self.assertIsNone(await self.repository.authenticate(second_token))
        self.assertFalse(await self.repository.disable(42, 10))

    async def test_match_submission_is_idempotent(self) -> None:
        pairing, _token = await self.repository.create(42, 10, 100)
        self.assertTrue(await self.repository.record_match(pairing, 100141930))
        self.assertFalse(await self.repository.record_match(pairing, 100141930))


class CompanionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = CompanionPairingRepository(
            Path(self.temp_dir.name) / "duckies.sqlite3"
        )
        await self.repository.initialize()
        _pairing, self.token = await self.repository.create(42, 10, 100)
        self.received: list[tuple[int, int]] = []

        async def callback(pairing, match_id: int) -> None:
            self.received.append((pairing.discord_user_id, match_id))

        self.client = TestClient(TestServer(create_companion_api(self.repository, callback)))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temp_dir.cleanup()

    def payload(self, **updates: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "match_id": 100141930,
            "detected_at": datetime.now(UTC).isoformat(),
            "source": "deadlock-console",
        }
        payload.update(updates)
        return payload

    async def test_health_is_public(self) -> None:
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"status": "ok"})

    async def test_requires_valid_bearer_token(self) -> None:
        response = await self.client.post(
            "/v1/companion/matches",
            json=self.payload(),
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(self.received, [])

    async def test_accepts_once_and_returns_duplicate_without_callback(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        first = await self.client.post(
            "/v1/companion/matches", json=self.payload(), headers=headers
        )
        second = await self.client.post(
            "/v1/companion/matches", json=self.payload(), headers=headers
        )
        self.assertEqual(first.status, 202)
        self.assertEqual(second.status, 200)
        self.assertEqual(self.received, [(42, 100141930)])
        self.assertTrue((await second.json())["duplicate"])

    async def test_rejects_stale_or_malformed_events(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        response = await self.client.post(
            "/v1/companion/matches",
            json=self.payload(detected_at=stale),
            headers=headers,
        )
        self.assertEqual(response.status, 400)
        response = await self.client.post(
            "/v1/companion/matches",
            json=self.payload(match_id="100141930"),
            headers=headers,
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.received, [])
