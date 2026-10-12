"""Tests for retrying failed Discord lookups."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from duckies_bot.cogs.deadlock import DeadlockCog
from duckies_bot.cogs.tarkov import TarkovCog
from duckies_bot.features.tarkov.models import TarkovItem
from duckies_bot.providers.tarkov.errors import TarkovUnavailableError
from duckies_bot.storage import CompanionPairing
from duckies_bot.views import LookupRetryView


class LookupRetryViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_disables_while_retry_is_running(self) -> None:
        retry = AsyncMock()
        view = LookupRetryView(42, retry)
        button = view.children[0]
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

        await button.callback(interaction)  # type: ignore[attr-defined]

        self.assertTrue(button.disabled)
        interaction.response.edit_message.assert_awaited_once_with(
            content="Retrying lookup…",
            view=view,
        )
        retry.assert_awaited_once_with(interaction, view)

    async def test_failed_retry_reenables_button(self) -> None:
        view = LookupRetryView(42, AsyncMock())
        view.retry_button.disabled = True
        interaction = SimpleNamespace(edit_original_response=AsyncMock())

        await view.show_error(interaction, "Still unavailable")  # type: ignore[arg-type]

        self.assertFalse(view.retry_button.disabled)
        interaction.edit_original_response.assert_awaited_once_with(
            content="Still unavailable",
            view=view,
        )

    async def test_only_original_requester_can_retry(self) -> None:
        view = LookupRetryView(42, AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=99),
            guild=None,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        allowed = await view.interaction_check(interaction)  # type: ignore[arg-type]

        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once_with(
            "Only the person who ran this lookup or a server moderator can retry it.",
            ephemeral=True,
        )


class TarkovLookupRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_item_lookup_can_succeed_in_place(self) -> None:
        item = TarkovItem("id", "LEDX", "LEDX", None, 1, 1, 2, 1, (), ())
        service = SimpleNamespace(
            lookup_item=AsyncMock(
                side_effect=(TarkovUnavailableError(), item),
            )
        )
        cog = object.__new__(TarkovCog)
        cog.service = service
        error_message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=error_message)),
        )

        await TarkovCog.item.callback(cog, interaction, "LEDX")

        retry_view = interaction.followup.send.await_args.kwargs["view"]
        self.assertIsInstance(retry_view, LookupRetryView)
        self.assertIs(retry_view.message, error_message)
        retry_interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await retry_view.children[0].callback(retry_interaction)

        self.assertEqual(service.lookup_item.await_count, 2)
        edit = retry_interaction.edit_original_response.await_args.kwargs
        self.assertEqual(edit["embed"].title, "LEDX")
        self.assertIsNone(edit["view"])


class CompanionLookupRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_companion_lookup_gets_retry_button(self) -> None:
        cog = object.__new__(DeadlockCog)
        cog._live_watches = {}
        cog._wait_for_companion_demo = AsyncMock(side_effect=TimeoutError)
        notice = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace()
        pairing = CompanionPairing("token", 42, 10, 100)

        await cog._run_companion_watch(  # type: ignore[arg-type]
            pairing,
            123,
            channel,
            notice,
        )

        edit = notice.edit.await_args.kwargs
        self.assertIn("did not become ready", edit["content"])
        self.assertIsInstance(edit["view"], LookupRetryView)
        self.assertEqual(edit["view"].requester_id, 42)

    async def test_companion_retry_reuses_the_existing_notice(self) -> None:
        cog = object.__new__(DeadlockCog)
        cog._run_companion_watch = AsyncMock()
        notice = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace()
        pairing = CompanionPairing("token", 42, 10, 100)
        await cog._show_companion_lookup_error(  # type: ignore[arg-type]
            pairing,
            123,
            channel,
            notice,
            "Lookup failed",
            retry_view=None,
            retry_interaction=None,
        )
        retry_view = notice.edit.await_args.kwargs["view"]
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

        await retry_view.children[0].callback(interaction)

        cog._run_companion_watch.assert_awaited_once_with(
            pairing,
            123,
            channel,
            notice,
            retry_view=retry_view,
            retry_interaction=interaction,
        )


if __name__ == "__main__":
    unittest.main()
