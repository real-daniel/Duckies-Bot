from io import BytesIO
from unittest import TestCase

from PIL import Image, ImageDraw

from duckies_bot.features.maplestory import InvalidInventoryScreenshot, SocketMatch, highlight_sockets


def inventory_png() -> bytes:
    image = Image.new("RGB", (770, 390), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    for y in range(9, 9 + 8 * 46, 46):
        for x in (11, 57, 103, 149, 197, 243, 289, 335, 383, 429, 475, 521, 569, 615, 661, 707):
            draw.rectangle((x, y, x + 41, y + 41), outline=(255, 204, 0))

    blue = (51, 68, 102)
    red = (85, 51, 85)
    green = (34, 85, 85)
    gray = (51, 68, 85)
    purple = (85, 34, 102)
    bright_prismatic = (238, 187, 238)
    prismatic = (85, 102, 119)
    bright_blue = (34, 119, 238)
    unclear = (120, 120, 120)
    pieces = (
        (11, (blue, blue, blue)),
        (57, (bright_prismatic, bright_blue, bright_blue)),
        (103, (red, green, red)),
        (149, (blue, blue, unclear)),
        (197, (blue, blue)),
        (243, (purple, gray, purple)),
        (383, (blue, prismatic, green)),
        (429, (prismatic, prismatic, blue)),
    )
    for x, colors in pieces:
        draw.line((x + 1, 13, x + 40, 13), fill=(133, 133, 133))
        for dx, color in zip((10, 21, 33), colors):
            draw.rectangle((x + dx, 15, x + dx + 1, 17), fill=color)
        for dx in (21, 33)[:len(colors) - 1]:
            draw.point((x + dx + 1, 13), fill="black")
            draw.point((x + dx + 2, 14), fill="black")

    # Weapon artwork can mimic the third outline without a middle socket.
    draw.line((290, 13, 329, 13), fill=(133, 133, 133))
    draw.point((289 + 34, 13), fill="black")
    draw.point((289 + 35, 14), fill="black")

    # Another weapon mimics both outlines but lacks the shaded socket strip.
    for dx in (21, 33):
        draw.point((335 + dx + 1, 13), fill="black")
        draw.point((335 + dx + 2, 14), fill="black")

    # One pixel in the red third socket is shaded differently on the real sample.
    draw.point((103 + 33, 17), fill=(85, 51, 68))
    # The bright Prismatic marker has a gold edge next to the timer.
    draw.point((57 + 12, 15), fill=(214, 151, 78))
    draw.point((57 + 12, 16), fill=(228, 181, 73))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class MapleStorySocketsTest(TestCase):
    def test_int_filter_boxes_candidates_and_marks_unclear_sockets(self):
        image, matches = highlight_sockets(inventory_png(), "INT")
        self.assertEqual(matches, [
            SocketMatch(1, 1, ("Blue", "Blue", "Blue")),
            SocketMatch(1, 2, ("Prismatic", "Blue", "Blue")),
            SocketMatch(1, 4, ("Blue", "Blue", None)),
        ])
        with Image.open(image) as overlay:
            self.assertEqual(overlay.getpixel((11, 9)), (255, 0, 0))
            self.assertEqual(overlay.getpixel((103, 9)), (255, 204, 0))
            self.assertEqual(overlay.getpixel((197, 9)), (255, 204, 0))

        _, without_prismatic = highlight_sockets(inventory_png(), "INT", allow_prismatic=False)
        self.assertEqual([match.column for match in without_prismatic], [1, 4])

    def test_physical_stat_filters_follow_the_supplied_guide(self):
        screenshot = inventory_png()
        _, strength = highlight_sockets(screenshot, "STR")
        _, dexterity = highlight_sockets(screenshot, "DEX")
        _, luck = highlight_sockets(screenshot, "LUK")
        self.assertEqual(strength, [SocketMatch(1, 3, ("Red", "Green", "Red"))])
        self.assertEqual([match.column for match in dexterity], [3, 6])
        self.assertEqual(luck, [SocketMatch(1, 6, ("Purple", "Gray", "Purple"))])

    def test_invalid_attachment(self):
        with self.assertRaises(InvalidInventoryScreenshot):
            highlight_sockets(b"invalid PNG", "INT")

    def test_all_items_ignores_color_filters_and_excludes_two_socket_gear(self):
        _, matches = highlight_sockets(inventory_png(), None, allow_prismatic=False)
        self.assertEqual([match.column for match in matches], [1, 2, 3, 4, 6, 9, 10])
        self.assertEqual(matches[1].colors, ("Prismatic", "Blue", "Blue"))
        self.assertEqual(matches[3].colors, ("Blue", "Blue", None))

    def test_force_requires_one_prismatic_and_keeps_the_selected_stat_filter(self):
        png = inventory_png()
        _, intelligence = highlight_sockets(png, "INT", allow_prismatic="force")
        self.assertEqual(intelligence, [SocketMatch(1, 2, ("Prismatic", "Blue", "Blue"))])

        _, unfiltered = highlight_sockets(png, None, allow_prismatic="force")
        self.assertEqual([match.column for match in unfiltered], [2, 9])
