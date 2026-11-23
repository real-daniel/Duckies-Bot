"""Read and label the three options in Quanah's Obtain Skills panel."""

from collections import Counter
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


_CARD_WIDTH = 442
_CARD_HEIGHT = 88
_ROW_OFFSETS = (0, 100, 200)
_CARD_STACK_HEIGHT = _ROW_OFFSETS[-1] + _CARD_HEIGHT
_CARD_EDGE = (168, 210, 255)
_CARD_EDGE_RUN = bytes(_CARD_EDGE) * 12
_BORDER_SWATCHES = {
    (102, 255, 255): "Rare",
    (255, 190, 254): "Epic",
    (255, 217, 114): "Unique",
    (60, 255, 129): "Legendary",
}
_BORDER_SAMPLE_POINTS = (
    (32, 15), (55, 15), (32, 16), (55, 16),
    (15, 35), (16, 35), (15, 52), (16, 52),
    (71, 35), (72, 35), (71, 52), (72, 52),
)


class InvalidSkillScreenshot(ValueError):
    """The three Obtain Skills cards could not be located."""


def label_skill_tiers(screenshot: bytes) -> tuple[BytesIO, tuple[str, ...]]:
    try:
        with Image.open(BytesIO(screenshot), formats=("PNG",)) as source:
            if source.width * source.height > 4096 * 2160 or source.is_animated:
                raise InvalidSkillScreenshot()
            image = source.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise InvalidSkillScreenshot() from exc

    x, y = _find_skill_cards(image)
    pixels = image.load()
    tiers = tuple(_read_tier(pixels, x, y + offset) for offset in _ROW_OFFSETS)

    preview = Image.new("RGB", (_CARD_WIDTH + 144, _CARD_STACK_HEIGHT), (15, 23, 42))
    preview.paste(image.crop((x, y, x + _CARD_WIDTH, y + _CARD_STACK_HEIGHT)))
    draw = ImageDraw.Draw(preview)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    for offset, tier in zip(_ROW_OFFSETS, tiers):
        draw.text((_CARD_WIDTH + 12, offset + _CARD_HEIGHT // 2), tier, font=font, fill="white", anchor="lm")

    output = BytesIO()
    preview.save(output, format="PNG")
    output.seek(0)
    return output, tiers


def _find_skill_cards(image: Image.Image) -> tuple[int, int]:
    pixels = image.load()
    stride = image.width * 3
    raster = image.tobytes()
    origins = []
    for y in range(image.height - _CARD_STACK_HEIGHT + 1):
        row = raster[y * stride : (y + 1) * stride]
        index = row.find(_CARD_EDGE_RUN)
        while index >= 0:
            x = index // 3
            if (
                index % 3 == 0
                and x + _CARD_WIDTH <= image.width
                and (x == 0 or pixels[x - 1, y] != _CARD_EDGE)
                and all(
                    pixels[x, y + offset + edge_y] == _CARD_EDGE
                    for offset in _ROW_OFFSETS
                    for edge_y in (0, 40, _CARD_HEIGHT - 1)
                )
            ):
                origins.append((x, y))
            index = row.find(_CARD_EDGE_RUN, index + len(_CARD_EDGE_RUN))

    if len(origins) != 1:
        raise InvalidSkillScreenshot()
    return origins[0]


def _read_tier(pixels, x: int, y: int) -> str:
    votes: Counter[str] = Counter()
    for dx, dy in _BORDER_SAMPLE_POINTS:
        rgb = pixels[x + dx, y + dy]
        if min(rgb) >= 180 and max(rgb) - min(rgb) <= 18:
            votes["Normal"] += 1
        for swatch, tier in _BORDER_SWATCHES.items():
            if max(abs(channel - reference) for channel, reference in zip(rgb, swatch)) <= 18:
                votes[tier] += 1

    for tier, count in votes.items():
        if count >= 8:
            return tier
    return "Unclear"
