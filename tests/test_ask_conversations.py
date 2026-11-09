"""Tests for compact reply-linked ask sessions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from duckies_bot.features.ask_conversations import (
    ASK_SESSION_MAX_TURNS,
    ASK_SESSION_TURN_CHARS,
    AskConversationStore,
)


class AskConversationStoreTests(unittest.TestCase):
    def test_only_owner_in_same_channel_can_claim_reply_session(self) -> None:
        store = AskConversationStore()
        store.register(
            100,
            user_id=7,
            channel_id=8,
            turns=("is infernus OP in oracle",),
            metadata="NA1",
        )

        self.assertIsNone(store.claim(100, user_id=9, channel_id=8))
        self.assertIsNone(store.claim(100, user_id=7, channel_id=10))
        session = store.claim(100, user_id=7, channel_id=8)

        self.assertIsNotNone(session)
        self.assertEqual(session.metadata, "NA1")  # type: ignore[union-attr]
        self.assertIsNone(store.claim(100, user_id=7, channel_id=8))

    def test_follow_up_query_keeps_only_short_user_turns(self) -> None:
        store = AskConversationStore()
        store.register(
            100,
            user_id=7,
            channel_id=8,
            turns=("x" * 500,),
        )
        session = store.claim(100, user_id=7, channel_id=8)
        assert session is not None

        query = session.follow_up_query("what about phantom+ " + "y" * 500)

        self.assertIn("User turn 1:", query)
        self.assertIn("User turn 2: what about phantom+", query)
        self.assertEqual(len(session.turns[0]), ASK_SESSION_TURN_CHARS)
        self.assertLessEqual(query.count("y"), ASK_SESSION_TURN_CHARS)
        self.assertLessEqual(len(query), 800)

    def test_conversation_stops_after_four_user_turns(self) -> None:
        store = AskConversationStore()
        store.register(
            100,
            user_id=7,
            channel_id=8,
            turns=("one", "two", "three"),
        )
        session = store.claim(100, user_id=7, channel_id=8)
        assert session is not None

        continued = store.continue_from(101, session, "four")

        self.assertFalse(continued)
        self.assertEqual(ASK_SESSION_MAX_TURNS, 4)
        self.assertIsNone(store.claim(101, user_id=7, channel_id=8))

    def test_expired_session_is_discarded(self) -> None:
        store = AskConversationStore(ttl_seconds=10)
        with patch(
            "duckies_bot.features.ask_conversations.time.monotonic",
            return_value=100.0,
        ):
            store.register(100, user_id=7, channel_id=8, turns=("question",))
        with patch(
            "duckies_bot.features.ask_conversations.time.monotonic",
            return_value=111.0,
        ):
            self.assertIsNone(store.claim(100, user_id=7, channel_id=8))
