"""Read Quanah jewel markers from an expanded MapleStory inventory PNG."""

from dataclasses import dataclass
from io import BytesIO
from itertools import groupby

from PIL import Image, ImageDraw


STAT_COLORS = {
    "INT": ("Blue",),
    "STR": ("Red", "Green", "Gray"),
    "DEX": ("Red", "Green", "Purple", "Gray"),
    "LUK": ("Purple", "Gray"),
}

_COLUMN_OFFSETS = tuple(group * 186 + column * 46 for group in range(4) for column in range(4))
_ROW_OFFSETS = tuple(range(0, 8 * 46, 46))
_CELL_SIZE = 42
_GRID_WIDTH = _COLUMN_OFFSETS[-1] + _CELL_SIZE
_GRID_HEIGHT = _ROW_OFFSETS[-1] + _CELL_SIZE

# A socket overlay darkens the top border and insets its visible run by one pixel.
_BORDER_INSETS = {
    (255, 204, 0): 0,
    (194, 97, 255): 0,
    (0, 255, 0): 0,
    (92, 161, 255): 0,
    (153, 122, 0): 1,
    (116, 58, 153): 1,
    (0, 153, 0): 1,
}

# Colors of the marker bodies, including shading where the item timer overlaps.
_MARKER_COLORS = {
    "Blue": {
        (51, 68, 102), (51, 85, 119), (34, 119, 238), (68, 170, 255),
        (17, 85, 170), (34, 136, 255), (17, 51, 119),
    },
    "Gray": {(51, 68, 85), (68, 85, 102)},
    "Green": {(34, 85, 85), (51, 85, 102)},
    "Purple": {(85, 34, 102), (102, 51, 119)},
    "Red": {(85, 51, 85), (102, 68, 85), (85, 51, 68)},
    "Prismatic": {(85, 102, 119), (102, 102, 119), (102, 119, 136)},
}


class InvalidInventoryScreenshot(ValueError):
    """The screenshot cannot be read at the supported inventory scale."""


@dataclass(frozen=True, slots=True)
class SocketMatch:
    row: int
    column: int
    colors: tuple[str | None, ...]


def highlight_sockets(
    screenshot: bytes, stat: str | None, *, allow_prismatic: bool = True
) -> tuple[BytesIO, list[SocketMatch]]:
    allowed = set(STAT_COLORS[stat]) if stat is not None else None
    if allowed is not None and allow_prismatic:
        allowed.add("Prismatic")

    try:
        with Image.open(BytesIO(screenshot), formats=("PNG",)) as source:
            if (
                not _GRID_WIDTH <= source.width <= 1536
                or not _GRID_HEIGHT <= source.height <= 1024
                or getattr(source, "is_animated", False)
            ):
                raise InvalidInventoryScreenshot()
            image = source.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise InvalidInventoryScreenshot() from exc

    origin_x, origin_y = _find_grid(image)
    pixels = image.load()
    overlay = ImageDraw.Draw(image)
    matches = []
    for row, offset_y in enumerate(_ROW_OFFSETS, start=1):
        for column, offset_x in enumerate(_COLUMN_OFFSETS, start=1):
            x, y = origin_x + offset_x, origin_y + offset_y
            # A diagonal in the item artwork can resemble one marker. Require
            # the outlines at both unobstructed socket positions.
            if not all(_has_marker_outline(pixels, x + dx, y) for dx in (21, 33)):
                continue
            colors = tuple(_read_marker(pixels, x + dx, y) for dx in (10, 21, 33))
            if allowed is None or (
                colors.count("Prismatic") <= 1
                and all(color is None or color in allowed for color in colors)
            ):
                matches.append(SocketMatch(row, column, colors))

    # Draw after scanning so the overlay cannot affect nearby reads.
    for match in matches:
        x = origin_x + _COLUMN_OFFSETS[match.column - 1]
        y = origin_y + _ROW_OFFSETS[match.row - 1]
        overlay.rectangle((x, y, x + _CELL_SIZE - 1, y + _CELL_SIZE - 1), outline="red", width=3)

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output, matches


def _has_marker_outline(pixels, x: int, y: int) -> bool:
    return (
        pixels[x + 1, y + 4] == pixels[x + 2, y + 5] == (0, 0, 0)
        or pixels[x + 1, y + 4] == (17, 17, 153)
        and pixels[x + 2, y + 4] == (157, 127, 134)
    )


def _read_marker(pixels, x: int, y: int) -> str | None:
    # The bright Prismatic marker keeps this gold edge beside the item timer.
    if pixels[x + 2, y + 6] == (214, 151, 78) and pixels[x + 2, y + 7] == (228, 181, 73):
        return "Prismatic"
    body = [pixels[x + dx, y + dy] for dx in (0, 1) for dy in (6, 7, 8)]
    matches = [
        color for color, shades in _MARKER_COLORS.items()
        if sum(pixel in shades for pixel in body) >= 5
    ]
    return matches[0] if len(matches) == 1 else None


def _find_grid(image: Image.Image) -> tuple[int, int]:
    pixels = image.load()
    best_origin = None
    best_score = 0
    for y in range(image.height - _GRID_HEIGHT + 1):
        starts = set()
        x = 0
        for color, run in groupby(pixels[column, y] for column in range(image.width)):
            length = sum(1 for _ in run)
            inset = _BORDER_INSETS.get(color)
            if inset is not None and length == _CELL_SIZE - 2 * inset:
                starts.add(x - inset)
            x += length

        origins = {start - offset for start in starts for offset in _COLUMN_OFFSETS}
        for origin in origins:
            if not 0 <= origin <= image.width - _GRID_WIDTH:
                continue
            if sum(origin + offset in starts for offset in _COLUMN_OFFSETS) < 4:
                continue
            score = sum(
                pixels[origin + dx + 2, y + dy] in _BORDER_INSETS
                and pixels[origin + dx + 2, y + dy] == pixels[origin + dx + 39, y + dy]
                for dy in _ROW_OFFSETS
                for dx in _COLUMN_OFFSETS
            )
            if score >= 24 and score > best_score:
                best_origin, best_score = (origin, y), score

    if best_origin is None:
        raise InvalidInventoryScreenshot()
    return best_origin
