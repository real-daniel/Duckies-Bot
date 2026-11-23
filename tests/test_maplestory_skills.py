from io import BytesIO
from unittest import TestCase

from PIL import Image, ImageDraw

from duckies_bot.features.maplestory_skills import InvalidSkillScreenshot, label_skill_tiers


def skill_panel_png(colors, *, x=36, y=25):
    image = Image.new("RGB", (x + 442, y + 288), (52, 85, 209))
    draw = ImageDraw.Draw(image)
    for row, color in enumerate(colors):
        top = y + row * 100
        draw.rectangle((x, top, x + 441, top + 87), outline=(168, 210, 255))
        draw.rectangle((x + 15, top + 15, x + 72, top + 72), outline=color, width=2)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class MapleStorySkillsTest(TestCase):
    def test_labels_the_three_skill_borders_in_order(self):
        preview, tiers = label_skill_tiers(
            skill_panel_png(((60, 255, 129), (255, 190, 254), (255, 217, 114)))
        )
        self.assertEqual(tiers, ("Legendary", "Epic", "Unique"))
        with Image.open(preview) as image:
            self.assertEqual(image.size, (586, 288))
            self.assertEqual(image.getpixel((15, 35)), (60, 255, 129))

    def test_supports_a_tight_crop_and_keeps_unknown_borders_unclear(self):
        _, tiers = label_skill_tiers(
            skill_panel_png(((102, 255, 255), (235, 235, 235), (52, 85, 209)), x=0, y=0)
        )
        self.assertEqual(tiers, ("Rare", "Normal", "Unclear"))

    def test_requires_all_three_cards(self):
        with self.assertRaises(InvalidSkillScreenshot):
            label_skill_tiers(skill_panel_png(((60, 255, 129),)))
