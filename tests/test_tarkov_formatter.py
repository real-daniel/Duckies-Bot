"""Tests for Discord item presentation."""

import unittest

from duckies_bot.features.tarkov.formatter import (
    build_item_embed,
    build_quest_log_embeds,
    build_server_status_embed,
    build_task_embeds,
    format_roubles,
)
from duckies_bot.features.tarkov.models import (
    AggregatedTaskItem,
    AggregatedTaskKey,
    QuestLogMapGroup,
    QuestLogMatch,
    QuestLogSummary,
    ServerComponentStatus,
    ServerStatusMessage,
    TarkovItem,
    TarkovServerStatus,
    TarkovTask,
    TaskItemRequirement,
    TaskMap,
    TaskObjective,
    TaskRewardItem,
    TaskRewards,
    VendorPrice,
)


def sample_task() -> TarkovTask:
    empty = TaskRewards((), (), (), ())
    requirement = TaskItemRequirement(("LEDX",), 1, True, "Find a LEDX")
    return TarkovTask(
        id="task-id",
        name="Private Clinic",
        trader_name="Therapist",
        min_player_level=35,
        maps=(TaskMap("Shoreline", "shoreline"),),
        objectives=(
            TaskObjective("findItem", "Find a LEDX", False, (), (requirement,), ()),
            TaskObjective("visit", "Visit the health resort", True, (), (), ("West 301 key",)),
        ),
        prerequisite_names=("Health Care Privacy - Part 4",),
        required_items=(requirement,),
        required_keys=("West 301 key",),
        experience=30600,
        wiki_url="https://example.test/wiki",
        image_url="https://example.test/task.webp",
        kappa_required=True,
        lightkeeper_required=False,
        faction_name="Any",
        available_delay_min_seconds=0,
        available_delay_max_seconds=0,
        start_rewards=empty,
        finish_rewards=TaskRewards(
            (TaskRewardItem("Roubles", 130000),),
            ("Therapist +0.05",),
            (),
            (),
        ),
        failure_rewards=empty,
    )


class TarkovFormatterTests(unittest.TestCase):
    def test_format_roubles(self) -> None:
        self.assertEqual(format_roubles(1_250_000), "1,250,000 ₽")
        self.assertEqual(format_roubles(None), "Unavailable")

    def test_embed_contains_prices_trader_and_limited_tasks(self) -> None:
        item = TarkovItem(
            "id",
            "LEDX Skin Transilluminator",
            "LEDX",
            "https://example.test/icon.png",
            1_000_000,
            900_000,
            1_100_000,
            950_000,
            (VendorPrice("Therapist", 650_000),),
            tuple(f"Task {number}" for number in range(7)),
        )
        embed = build_item_embed(item)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(embed.title, item.name)
        self.assertEqual(embed.thumbnail.url, item.icon_url)
        self.assertIn("1,000,000 ₽", fields["24h average"])
        self.assertIn("Therapist", fields["Best trader"])
        self.assertIn("and 2 more", fields["Needed for tasks"])
        self.assertLessEqual(len(fields["Needed for tasks"]), 1024)

    def test_embed_handles_all_missing_values(self) -> None:
        item = TarkovItem("id", "Unknown", None, None, None, None, None, None, (), ())
        embed = build_item_embed(item)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["24h average"], "Unavailable")
        self.assertEqual(fields["Best trader"], "No trader offer available")
        self.assertEqual(fields["Needed for tasks"], "Not currently required for a task")

    def test_quick_task_embed_prioritizes_raid_prep(self) -> None:
        embeds = build_task_embeds(sample_task())
        self.assertEqual(len(embeds), 1)
        fields = {field.name: field.value for field in embeds[0].fields}
        self.assertIn("LEDX", fields["Bring / collect"])
        self.assertIn("Found in Raid", fields["Bring / collect"])
        self.assertIn("West 301 key", fields["Required keys"])
        self.assertIn("tarkov.dev/map/shoreline", fields["Maps"])
        self.assertIn("30,600 XP", fields["Completion rewards"])

    def test_detailed_task_embeds_expand_objectives_and_rewards(self) -> None:
        embeds = build_task_embeds(sample_task(), detailed=True)
        self.assertEqual(len(embeds), 3)
        objective_fields = {field.name: field.value for field in embeds[1].fields}
        self.assertIn("1. findItem", objective_fields)
        self.assertIn("Optional", objective_fields["2. visit"])
        reward_fields = {field.name: field.value for field in embeds[2].fields}
        self.assertIn("130000× Roubles", reward_fields["On completion"])
        self.assertIn("Therapist +0.05", reward_fields["On completion"])

    def test_quest_log_embeds_show_combined_prep_maps_and_uncertain_text(self) -> None:
        task = sample_task()
        summary = QuestLogSummary(
            matches=(QuestLogMatch(task, "Private Clink", 0.94),),
            item_requirements=(
                AggregatedTaskItem(("LEDX",), 1, True, (task.name,)),
            ),
            required_keys=(AggregatedTaskKey("West 301 key", (task.name,)),),
            map_groups=(QuestLogMapGroup(task.maps[0], (task.name,)),),
            tasks_without_map=(),
            unmatched_lines=("New 1.1 task",),
        )
        embeds = build_quest_log_embeds(summary, detailed=True)
        serialized = [embed.to_dict() for embed in embeds]
        rendered = str(serialized)
        self.assertIn("Private Clinic", rendered)
        self.assertIn("LEDX", rendered)
        self.assertIn("West 301 key", rendered)
        self.assertIn("tarkov.dev/map/shoreline", rendered)
        self.assertIn("New 1.1 task", rendered)
        self.assertIn("Quest Objectives", rendered)
        for embed in serialized:
            self.assertLessEqual(len(embed.get("fields", [])), 25)
            self.assertTrue(all(len(field["value"]) <= 1024 for field in embed.get("fields", [])))

    def test_server_status_embed_shows_components_and_active_incident(self) -> None:
        status = TarkovServerStatus(
            ServerComponentStatus("Global", None, 2, "Unstable"),
            (
                ServerComponentStatus("Authentication", None, 0, "OK"),
                ServerComponentStatus("Matchmaking", "Long queues", 2, "Unstable"),
            ),
            (
                ServerStatusMessage(
                    "Investigating delays",
                    "2026-08-09T12:00:00+00:00",
                    2,
                    None,
                    "Unstable",
                ),
            ),
        )
        embed = build_server_status_embed(status)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Unstable", embed.description)
        self.assertIn("Authentication", fields["Components"])
        self.assertIn("Long queues", fields["Components"])
        self.assertIn("Investigating delays", fields["Active incidents"])
        self.assertIn("No load", embed.footer.text)
