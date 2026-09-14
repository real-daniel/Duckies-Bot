"""Deterministic graphics for Deadlock scouting reports."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import MatchScout, ScoutedPlayer


WIDTH = 1200
HEIGHT = 760
_AMBER = (245, 170, 58)
_SAPPHIRE = (105, 160, 255)
_TEXT = (255, 253, 244)
_MUTED = (190, 196, 199)


def render_scout_graphic(
    scout: MatchScout,
    player_index: int | None = None,
    highlighted_account_id: int | None = None,
    *,
    hero_icons: Mapping[int, bytes] | None = None,
) -> bytes:
    """Render the lobby overview or one player card as a Discord-sized PNG."""
    if player_index is not None and not 0 <= player_index < len(scout.players):
        raise IndexError("player_index is outside the scouting report")

    image = Image.new("RGB", (WIDTH, HEIGHT), (7, 9, 11))
    draw = ImageDraw.Draw(image, "RGBA")
    _background(draw)
    _header(draw, scout, player_index)
    icons = _decode_icons(hero_icons or {})
    if player_index is None:
        _overview(image, draw, scout, highlighted_account_id, icons)
    else:
        _player_card(image, draw, scout, player_index, highlighted_account_id, icons)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=7)
    return output.getvalue()


def _background(draw: ImageDraw.ImageDraw) -> None:
    for y in range(HEIGHT):
        shade = int(7 + 10 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(shade, shade + 2, shade + 4, 255))
    for x in range(-180, WIDTH, 175):
        draw.polygon(
            ((x, 0), (x + 230, 0), (x + 35, HEIGHT), (x - 195, HEIGHT)),
            fill=(255, 255, 255, 3),
        )


def _header(draw: ImageDraw.ImageDraw, scout: MatchScout, player_index: int | None) -> None:
    draw.rectangle((0, 0, WIDTH, 105), fill=(4, 5, 6, 232))
    draw.rectangle((0, 103, WIDTH, 106), fill=(181, 145, 72, 170))
    draw.text((35, 14), "DEADLOCK", font=_font(22, bold=True), fill=(244, 203, 112))
    title = "LOBBY SCOUT" if player_index is None else "PLAYER SCOUT"
    draw.text((35, 46), title, font=_font(40, bold=True), fill=_TEXT)
    draw.text(
        (1165, 20),
        f"MATCH {scout.match_id}",
        font=_font(23, bold=True),
        fill=_TEXT,
        anchor="ra",
    )
    captured = _duration(scout.game_time_seconds)
    draw.text((1165, 57), f"ROSTER AT {captured}", font=_font(19), fill=_MUTED, anchor="ra")


def _overview(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    scout: MatchScout,
    highlighted_account_id: int | None,
    icons: Mapping[int, Image.Image],
) -> None:
    grouped: dict[int | None, list[ScoutedPlayer]] = {}
    for player in scout.players:
        grouped.setdefault(player.player.team, []).append(player)
    teams = sorted(grouped, key=lambda team: (team not in (2, 3), team or 99))[:2]
    while len(teams) < 2:
        teams.append(None)
    for column, team in enumerate(teams):
        left = 25 + column * 590
        players = grouped.get(team, [])
        color = _team_color(team)
        wins = sum(item.recent_outcomes.count("W") for item in players)
        losses = sum(item.recent_outcomes.count("L") for item in players)
        draw.text((left + 10, 121), _team_name(team).upper(), font=_font(30, bold=True), fill=color)
        draw.text(
            (left + 555, 128),
            f"RECENT FORM  {wins}-{losses}",
            font=_font(17, bold=True),
            fill=(*color, 225),
            anchor="ra",
        )
        for index in range(6):
            top = 164 + index * 94
            player = players[index] if index < len(players) else None
            _overview_row(image, draw, player, left, top, color, highlighted_account_id, icons)


def _overview_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    player: ScoutedPlayer | None,
    left: int,
    top: int,
    color: tuple[int, int, int],
    highlighted_account_id: int | None,
    icons: Mapping[int, Image.Image],
) -> None:
    right = left + 565
    draw.rounded_rectangle((left, top, right, top + 80), radius=10, fill=(20, 24, 27, 225))
    draw.rectangle((left, top, left + 5, top + 80), fill=(*color, 230))
    if player is None:
        draw.text((left + 24, top + 26), "EMPTY SLOT", font=_font(18), fill=(110, 116, 119))
        return
    live = player.player
    hero_id = live.hero_id
    _hero_icon(image, draw, icons.get(hero_id), hero_id, left + 17, top + 12, (56, 56))
    name = _fit(draw, live.steam_name, _font(20, bold=True), 225)
    if live.account_id == highlighted_account_id:
        name += "  • YOU"
    hero = player.hero.name if player.hero else f"Hero {hero_id}" if hero_id is not None else "Unknown hero"
    draw.text((left + 88, top + 12), name, font=_font(20, bold=True), fill=_TEXT)
    draw.text((left + 88, top + 43), _fit(draw, hero, _font(17), 225), font=_font(17), fill=_MUTED)
    draw.text((left + 332, top + 13), _rank(player), font=_font(18, bold=True), fill=color)
    draw.text((left + 332, top + 44), _games(player), font=_font(16), fill=_MUTED)
    form = "".join(player.recent_outcomes) or "—"
    draw.text((right - 16, top + 28), form, font=_font(19, bold=True), fill=_form_color(player), anchor="ra")


def _player_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    scout: MatchScout,
    player_index: int,
    highlighted_account_id: int | None,
    icons: Mapping[int, Image.Image],
) -> None:
    player = scout.players[player_index]
    live = player.player
    color = _team_color(live.team)
    draw.rounded_rectangle((35, 132, 1165, 710), radius=18, fill=(18, 22, 25, 235), outline=(*color, 190), width=2)
    _hero_icon(image, draw, icons.get(live.hero_id), live.hero_id, 75, 174, (150, 150))
    hero = player.hero.name if player.hero else f"Hero {live.hero_id}" if live.hero_id is not None else "Unknown hero"
    name = _fit(draw, live.steam_name, _font(38, bold=True), 660)
    draw.text((255, 169), name, font=_font(38, bold=True), fill=_TEXT)
    subtitle = f"{_team_name(live.team)}  •  {hero}"
    if live.account_id == highlighted_account_id:
        subtitle += "  •  YOUR LINKED ACCOUNT"
    draw.text((255, 220), subtitle, font=_font(21, bold=True), fill=color)
    draw.text((1115, 188), f"PLAYER {player_index + 1} / {len(scout.players)}", font=_font(18), fill=_MUTED, anchor="ra")

    stats = (
        ("RANK", _rank(player)),
        ("RECORDED GAMES", _games(player)),
        ("CURRENT HERO", _hero_history(player)),
        ("RECENT FORM", " ".join(player.recent_outcomes) or "Unknown"),
    )
    for index, (label, value) in enumerate(stats):
        left = 75 + index * 265
        draw.text((left, 365), label, font=_font(15, bold=True), fill=_MUTED)
        draw.text((left, 395), _fit(draw, value, _font(23, bold=True), 235), font=_font(23, bold=True), fill=_TEXT)

    draw.text((75, 485), "MOST PLAYED HEROES", font=_font(18, bold=True), fill=color)
    top_heroes = player.top_heroes[:3]
    if not top_heroes:
        draw.text((75, 530), "No recorded hero statistics", font=_font(21), fill=_MUTED)
    for index, record in enumerate(top_heroes):
        left = 75 + index * 350
        name = record.hero.name if record.hero else f"Hero {record.experience.hero_id}"
        wr = record.experience.win_rate
        detail = f"{record.experience.matches_played} games"
        if wr is not None:
            detail += f"  •  {wr:.0%} WR"
        draw.rounded_rectangle((left, 525, left + 315, 625), radius=10, fill=(29, 34, 38, 230))
        draw.text((left + 20, 545), _fit(draw, name, _font(20, bold=True), 275), font=_font(20, bold=True), fill=_TEXT)
        draw.text((left + 20, 581), detail, font=_font(17), fill=_MUTED)
    draw.text((75, 670), f"ACCOUNT {live.account_id}", font=_font(16), fill=(130, 136, 140))


def _hero_icon(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    icon: Image.Image | None,
    hero_id: int | None,
    left: int,
    top: int,
    size: tuple[int, int],
) -> None:
    if icon is not None:
        fitted = ImageOps.fit(icon, size, method=Image.Resampling.LANCZOS)
        image.paste(fitted, (left, top), fitted)
        return
    color = _avatar_color(hero_id)
    draw.rounded_rectangle((left, top, left + size[0], top + size[1]), radius=8, fill=(*color, 255))
    draw.text((left + size[0] // 2, top + size[1] // 2), "?", font=_font(size[1] // 2, bold=True), fill=_TEXT, anchor="mm")


def _decode_icons(payloads: Mapping[int, bytes]) -> dict[int, Image.Image]:
    decoded: dict[int, Image.Image] = {}
    for hero_id, payload in payloads.items():
        try:
            with Image.open(BytesIO(payload)) as source:
                decoded[hero_id] = source.convert("RGBA")
        except (OSError, ValueError):
            continue
    return decoded


def _rank(player: ScoutedPlayer) -> str:
    if player.rank is None:
        return "Unknown rank"
    if player.rank.tier == 0:
        return "Unranked"
    suffixes = ("I", "II", "III", "IV", "V", "VI")
    suffix = suffixes[player.rank.subrank - 1] if 1 <= player.rank.subrank <= 6 else str(player.rank.subrank)
    return f"{player.rank_name or f'Tier {player.rank.tier}'} {suffix}"


def _games(player: ScoutedPlayer) -> str:
    return f"{player.total_matches:,} total" if player.total_matches is not None else "Unknown total"


def _hero_history(player: ScoutedPlayer) -> str:
    if player.experience is None or player.experience.matches_played <= 0:
        return "No history"
    value = f"{player.experience.matches_played:,} games"
    if player.hero_match_share is not None:
        value += f" ({player.hero_match_share:.0%})"
    if player.experience.win_rate is not None:
        value += f" • {player.experience.win_rate:.0%} WR"
    return value


def _form_color(player: ScoutedPlayer) -> tuple[int, int, int]:
    wins = player.recent_outcomes.count("W")
    losses = player.recent_outcomes.count("L")
    return (85, 205, 135) if wins > losses else (221, 104, 91) if losses > wins else _MUTED


def _team_color(team: int | None) -> tuple[int, int, int]:
    return _AMBER if team == 2 else _SAPPHIRE if team == 3 else (130, 135, 140)


def _team_name(team: int | None) -> str:
    return "Amber" if team == 2 else "Sapphire" if team == 3 else "Unknown team"


def _avatar_color(hero_id: int | None) -> tuple[int, int, int]:
    value = hero_id or 0
    return (65 + value * 37 % 115, 70 + value * 61 % 105, 78 + value * 83 % 112)


def _duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/bahnschrift.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(value, font=font) <= width:
        return value
    clipped = value
    while clipped and draw.textlength(clipped + "…", font=font) > width:
        clipped = clipped[:-1]
    return clipped + "…"
