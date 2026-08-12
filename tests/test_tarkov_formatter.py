"""Tests for Discord item presentation."""

import unittest

from duckies_bot.features.tarkov.formatter import (
    build_item_embed,
    build_flip_embeds,
    build_profit_embeds,
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
    PricedIngredient,
    ProfitResult,
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
    TraderFlipResult,
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


def field_value(embed, label: str) -> str:
    return next(field.value for field in embed.fields if label.casefold() in field.name.casefold())


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
        self.assertIn(item.name, embed.title)
        self.assertEqual(embed.thumbnail.url, item.icon_url)
        self.assertIn("1,000,000 ₽", field_value(embed, "24h average"))
        self.assertIn("Therapist", field_value(embed, "Best trader"))
        self.assertIn("and 2 more", field_value(embed, "Task uses"))
        self.assertLessEqual(len(field_value(embed, "Task uses")), 1024)

    def test_embed_handles_all_missing_values(self) -> None:
        item = TarkovItem("id", "Unknown", None, None, None, None, None, None, (), ())
        embed = build_item_embed(item)
        self.assertIn("Unavailable", field_value(embed, "24h average"))
        self.assertEqual(field_value(embed, "Best trader"), "No trader offer available")
        self.assertEqual(field_value(embed, "Task uses"), "Not currently required for a task")

    def test_quick_task_embed_prioritizes_raid_prep(self) -> None:
        embeds = build_task_embeds(sample_task())
        self.assertEqual(len(embeds), 1)
        self.assertIn("LEDX", field_value(embeds[0], "Bring / collect"))
        self.assertIn("Found in Raid", field_value(embeds[0], "Bring / collect"))
        self.assertIn("West 301 key", field_value(embeds[0], "Required keys"))
        self.assertIn("tarkov.dev/map/shoreline", field_value(embeds[0], "Maps"))
        self.assertIn("30,600 XP", field_value(embeds[0], "Completion rewards"))

    def test_detailed_task_embeds_expand_objectives_and_rewards(self) -> None:
        embeds = build_task_embeds(sample_task(), detailed=True)
        self.assertEqual(len(embeds), 3)
        self.assertIn("Find Item", embeds[1].fields[0].name)
        self.assertIn("Optional", embeds[1].fields[1].value)
        self.assertIn("130000× Roubles", field_value(embeds[2], "On completion"))
        self.assertIn("Therapist +0.05", field_value(embeds[2], "On completion"))

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
        self.assertIn("UNSTABLE", embed.description)
        self.assertIn("Authentication", field_value(embed, "Services"))
        self.assertIn("Long queues", field_value(embed, "Services"))
        self.assertIn("Investigating delays", field_value(embed, "Active incidents"))
        self.assertIn("does not expose load", field_value(embed, "Coverage"))

    def test_profit_embed_labels_gross_values_and_reusable_tools(self) -> None:
        result = ProfitResult(
            "recipe", "craft", "Ammo", 60, 1000, 60000, "Recent flea floor",
            (
                PricedIngredient("Gunpowder", 2, 10000, 20000, False),
                PricedIngredient("Multitool", 1, 30000, 30000, True),
            ),
            20000, 30000, 40000, 200.0, 20000, "Workbench", 3, None,
            7200, None, (),
        )
        embeds = build_profit_embeds((result,), "craft", "hourly")
        rendered = str([embed.to_dict() for embed in embeds])
        self.assertIn("40,000 ₽", rendered)
        self.assertIn("reusable capital", rendered)
        self.assertIn("Multitool (tool)", rendered)
        self.assertIn("exclude flea fees", embeds[0].description)

    def test_flip_embed_shows_spread_limit_and_task_requirement(self) -> None:
        result = TraderFlipResult(
            "Flip item", "Mechanic", 50_000, 100_000, "Recent flea floor",
            50_000, 100.0, 3, 30, "Required Task", 2, 100, False,
        )
        embeds = build_flip_embeds((result,))
        rendered = str([embed.to_dict() for embed in embeds])
        self.assertIn("50,000 ₽ gross each", rendered)
        self.assertIn("Mechanic LL3", rendered)
        self.assertIn("MAX GROSS", rendered)
        self.assertIn("Required Task", rendered)
        self.assertNotIn("author", embeds[0].to_dict())

    def test_all_embed_families_share_the_minimal_visual_style(self) -> None:
        item_embed = build_item_embed(
            TarkovItem("id", "LEDX", None, None, None, None, None, None, (), ())
        )
        task_embed = build_task_embeds(sample_task())[0]
        for embed in (item_embed, task_embed):
            self.assertIsNone(embed.author.name)
            self.assertIn("json.tarkov.dev", embed.footer.text)
