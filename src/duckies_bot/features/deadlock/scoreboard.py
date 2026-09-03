"""Deterministic PNG renderer for the live Deadlock scoreboard."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import ItemSummary, LiveMatchSnapshot, LivePlayer


_WIDTH = 1600
_HEIGHT = 900
_AMBER = (245, 170, 58)
_SAPPHIRE = (105, 160, 255)
_TEXT = (255, 253, 244)
_MUTED = (211, 214, 209)
_GOLD = (244, 193, 72)


def render_live_scoreboard(
    snapshot: LiveMatchSnapshot,
    highlighted_account_id: int | None = None,
    *,
    stream_status: str = "live",
    item_icons: Mapping[int, bytes] | None = None,
    hero_icons: Mapping[int, bytes] | None = None,
) -> bytes:
    """Render a fixed-layout 1600x900 scoreboard and return PNG bytes."""
    image = Image.new("RGB", (_WIDTH, _HEIGHT), (7, 9, 11))
    draw = ImageDraw.Draw(image, "RGBA")
    decoded_item_icons = _decode_item_icons(item_icons or {})
    decoded_hero_icons = _decode_item_icons(hero_icons or {}, size=(38, 35))
    _draw_background(draw)
    _draw_header(draw, snapshot, stream_status)

    grouped: dict[int | None, list[LivePlayer]] = {}
    for player in snapshot.players:
        grouped.setdefault(player.team, []).append(player)
    ordered_teams = sorted(grouped, key=lambda team: (team not in (2, 3), team or 99))
    teams = ordered_teams[:2]
    while len(teams) < 2:
        teams.append(None)

    _draw_team(
        image,
        draw,
        snapshot,
        teams[0],
        grouped.get(teams[0], []),
        top=114,
        highlighted_account_id=highlighted_account_id,
        item_icons=decoded_item_icons,
        hero_icons=decoded_hero_icons,
    )
    _draw_team(
        image,
        draw,
        snapshot,
        teams[1],
        grouped.get(teams[1], []),
        top=492,
        highlighted_account_id=highlighted_account_id,
        item_icons=decoded_item_icons,
        hero_icons=decoded_hero_icons,
    )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=7)
    return output.getvalue()


def render_discord_scoreboard(
    snapshot: LiveMatchSnapshot,
    highlighted_account_id: int | None = None,
    *,
    stream_status: str = "live",
    item_icons: Mapping[int, bytes] | None = None,
    hero_icons: Mapping[int, bytes] | None = None,
) -> bytes:
    """Render a taller scoreboard intended for Discord's narrow message column."""
    width, height = 1000, 1400
    image = Image.new("RGB", (width, height), (7, 9, 11))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        shade = int(7 + 11 * y / height)
        draw.line((0, y, width, y), fill=(shade, shade + 2, shade + 4, 255))
    for x in range(-180, width, 150):
        draw.polygon(
            ((x, 0), (x + 210, 0), (x + 20, height), (x - 190, height)),
            fill=(255, 255, 255, 3),
        )

    draw.rectangle((0, 0, width, 112), fill=(4, 5, 6, 230))
    draw.rectangle((0, 110, width, 113), fill=(181, 145, 72, 170))
    draw.text((42, 21), "DEADLOCK", font=_font(22, bold=True), fill=(244, 203, 112))
    draw.text((42, 49), "LIVE SCOREBOARD", font=_font(35, bold=True), fill=_TEXT)
    status_colors = {
        "live": ((72, 207, 139), "LIVE"),
        "reconnecting": ((232, 171, 68), "RECONNECTING"),
        "unreachable": ((216, 82, 79), "FEED UNAVAILABLE"),
        "ended": ((137, 141, 145), "ENDED"),
    }
    color, label = status_colors.get(stream_status, status_colors["live"])
    draw.text((958, 28), label, font=_font(20, bold=True), fill=color, anchor="ra")
    draw.text(
        (958, 64),
        f"MATCH {snapshot.match_id}  •  {_duration(snapshot.game_time_seconds)}",
        font=_font(18),
        fill=_MUTED,
        anchor="ra",
    )

    grouped: dict[int | None, list[LivePlayer]] = {}
    for player in snapshot.players:
        grouped.setdefault(player.team, []).append(player)
    teams = sorted(grouped, key=lambda team: (team not in (2, 3), team or 99))[:2]
    while len(teams) < 2:
        teams.append(None)
    decoded_icons = _decode_item_icons(item_icons or {}, size=(31, 31))
    decoded_hero_icons = _decode_item_icons(hero_icons or {}, size=(53, 54))
    _draw_discord_team(
        image,
        draw,
        snapshot,
        teams[0],
        grouped.get(teams[0], []),
        top=138,
        highlighted_account_id=highlighted_account_id,
        item_icons=decoded_icons,
        hero_icons=decoded_hero_icons,
    )
    _draw_discord_team(
        image,
        draw,
        snapshot,
        teams[1],
        grouped.get(teams[1], []),
        top=772,
        highlighted_account_id=highlighted_account_id,
        item_icons=decoded_icons,
        hero_icons=decoded_hero_icons,
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=7)
    return output.getvalue()


def render_discord_scoreboard_page(
    snapshot: LiveMatchSnapshot,
    page: str = "overview",
    highlighted_account_id: int | None = None,
    *,
    stream_status: str = "live",
    selected_account_id: int | None = None,
    timeline: tuple[str, ...] = (),
    item_icons: Mapping[int, bytes] | None = None,
    hero_icons: Mapping[int, bytes] | None = None,
) -> bytes:
    """Render one low-density 1200x760 scoreboard page for Discord previews."""
    width, height = 1200, 760
    image = Image.new("RGB", (width, height), (7, 9, 11))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        shade = int(7 + 10 * y / height)
        draw.line((0, y, width, y), fill=(shade, shade + 2, shade + 4, 255))
    for x in range(-180, width, 175):
        draw.polygon(
            ((x, 0), (x + 230, 0), (x + 35, height), (x - 195, height)),
            fill=(255, 255, 255, 3),
        )

    labels = {
        "overview": "OVERVIEW",
        "combat": "COMBAT",
        "economy": "ECONOMY",
        "builds": "BUILDS",
        "statues": "GOLDEN STATUES",
        "timeline": "TIMELINE",
        "player": "PLAYER DETAILS",
    }
    status_colors = {
        "live": ((72, 207, 139), "LIVE"),
        "reconnecting": ((232, 171, 68), "RECONNECTING"),
        "unreachable": ((216, 82, 79), "FEED UNAVAILABLE"),
        "ended": ((137, 141, 145), "ENDED"),
    }
    status_color, status_label = status_colors.get(stream_status, status_colors["live"])
    draw.rectangle((0, 0, width, 105), fill=(4, 5, 6, 232))
    draw.rectangle((0, 103, width, 106), fill=(181, 145, 72, 170))
    draw.text((35, 14), "DEADLOCK", font=_font(22, bold=True), fill=(244, 203, 112))
    draw.text((35, 46), labels.get(page, page.upper()), font=_font(40, bold=True), fill=_TEXT)
    draw.text((1165, 17), status_label, font=_font(23, bold=True), fill=status_color, anchor="ra")
    draw.text(
        (1165, 57),
        f"MATCH {snapshot.match_id}  •  {_duration(snapshot.game_time_seconds)}",
        font=_font(22),
        fill=_MUTED,
        anchor="ra",
    )

    icons = _decode_item_icons(item_icons or {}, size=(30, 30))
    heroes = _decode_item_icons(hero_icons or {}, size=(55, 56))
    if page == "player":
        selected = next(
            (player for player in snapshot.players if player.account_id == selected_account_id),
            snapshot.players[0] if snapshot.players else None,
        )
        _draw_discord_player_page(image, draw, snapshot, selected, icons, heroes)
    elif page == "timeline":
        _draw_discord_timeline(draw, timeline)
    else:
        grouped: dict[int | None, list[LivePlayer]] = {}
        for player in snapshot.players:
            grouped.setdefault(player.team, []).append(player)
        teams = sorted(grouped, key=lambda team: (team not in (2, 3), team or 99))[:2]
        while len(teams) < 2:
            teams.append(None)
        for column, team in enumerate(teams):
            left = 25 + column * 590
            _draw_discord_page_team(
                image,
                draw,
                snapshot,
                team,
                grouped.get(team, []),
                page=page,
                left=left,
                highlighted_account_id=highlighted_account_id,
                item_icons=icons,
                hero_icons=heroes,
            )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=7)
    return output.getvalue()


def _draw_discord_page_team(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    team: int | None,
    players: list[LivePlayer],
    *,
    page: str,
    left: int,
    highlighted_account_id: int | None,
    item_icons: Mapping[int, Image.Image],
    hero_icons: Mapping[int, Image.Image],
) -> None:
    color = _team_color(team)
    right = left + 565
    draw.text((left + 10, 120), _team_name(team).upper(), font=_font(32, bold=True), fill=color)
    draw.text(
        (right - 10, 127),
        (
            "COUNTS: TIER 1 / 2 / 3"
            if page == "statues"
            else _compact(sum(player.net_worth for player in players)) + " SOULS"
        ),
        font=_font(21, bold=True),
        fill=(*color, 235),
        anchor="ra",
    )
    for index in range(6):
        y = 165 + index * 96
        player = players[index] if index < len(players) else None
        highlighted = player is not None and player.account_id == highlighted_account_id
        opacity = 54 if index % 2 == 0 else 35
        if highlighted:
            opacity = 90
        draw.rounded_rectangle(
            (left, y, right, y + 87),
            7,
            fill=(*color, opacity) if player is not None else (255, 255, 255, 5),
            outline=(*((236, 220, 157) if highlighted else color), 210 if highlighted else 35),
            width=2 if highlighted else 1,
        )
        if player is None:
            continue
        hero = snapshot.hero(player.hero_id)
        hero_name = hero.name if hero else (
            f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
        )
        draw.rounded_rectangle(
            (left + 12, y + 13, left + 67, y + 69),
            9,
            fill=(*_avatar_color(player.hero_id), 255),
            outline=(255, 255, 255, 45),
        )
        initials = "".join(word[:1] for word in hero_name.split()[:2]).upper() or "?"
        hero_icon = hero_icons.get(player.hero_id) if player.hero_id is not None else None
        if hero_icon is not None:
            image.paste(hero_icon, (left + 12, y + 14), hero_icon)
        else:
            draw.text((left + 40, y + 41), initials, font=_font(23, bold=True), fill=_TEXT, anchor="mm")
        name = _fit_text(draw, player.steam_name, _font(26, bold=True), 205)
        draw.text((left + 82, y + 6), name, font=_font(26, bold=True), fill=_TEXT)
        if page != "builds":
            hero_label = _fit_text(draw, hero_name, _font(20), 205)
            draw.text((left + 82, y + 42), hero_label, font=_font(20), fill=(*color, 245))
        if highlighted:
            if page == "builds":
                badge_x, badge_y = left + 335, y + 17
            elif page == "statues":
                badge_x, badge_y = left + 306, y + 72
            else:
                badge_x, badge_y = left + 306, y + 72
            draw.text(
                (badge_x, badge_y),
                "YOU",
                font=_font(14, bold=True),
                fill=(239, 224, 169),
                anchor="ra",
            )

        if page == "overview":
            primary = f"{_compact(player.net_worth)} SOULS"
            secondary = f"{player.kills} / {player.deaths} / {player.assists}  KDA"
        elif page == "combat":
            primary = f"{_compact(player.hero_damage)} DMG   {_compact(player.objective_damage)} OBJ"
            secondary = f"{_compact(player.hero_healing)} HEAL   {player.kills}/{player.deaths}/{player.assists} KDA"
        elif page == "economy":
            minutes = max((snapshot.game_time_seconds or 0) / 60, 1 / 60)
            primary = f"{_compact(player.net_worth)} SOULS   {player.net_worth / minutes:,.0f}/MIN"
            secondary = f"{player.last_hits} LAST HITS   {player.denies} DENIES   LANE {player.assigned_lane or '—'}"
        elif page == "builds":
            primary = f"{len(_ordered_inventory(snapshot, player))} ITEMS"
            secondary = "CURRENT LIVE INVENTORY"
        else:
            primary = secondary = ""
        if page == "statues":
            _draw_statue_details(draw, player, left + 306, y + 8)
        else:
            primary_font = _font(20 if page == "combat" else 23, bold=True)
            secondary_font = _font(17 if page == "combat" else 19)
            draw.text((right - 12, y + 9), primary, font=primary_font, fill=_TEXT, anchor="ra")
            if page != "builds":
                draw.text((right - 12, y + 48), secondary, font=secondary_font, fill=_MUTED, anchor="ra")
            if page == "combat":
                _draw_statue_badge(draw, left + 235, y + 67, player.statue_buff_count, size=15)

        if page == "builds":
            inventory = _ordered_inventory(snapshot, player)
            mapped_inventory = bool(snapshot.items)
            for slot in range(12):
                slot_left = left + 82 + slot * 38
                item = inventory[slot] if slot < len(inventory) else None
                active = item is not None or (not mapped_inventory and slot < len(player.upgrades))
                fill_color = _item_color(item) if item is not None else _upgrade_color(slot)
                draw.rounded_rectangle(
                    (slot_left, y + 48, slot_left + 31, y + 81),
                    4,
                    fill=(*fill_color, 230) if active else (255, 255, 255, 16),
                    outline=(255, 255, 255, 24),
                )
                if item is not None and (icon := item_icons.get(item.item_id)) is not None:
                    image.paste(icon, (slot_left + 1, y + 50), icon)


def _draw_discord_timeline(draw: ImageDraw.ImageDraw, timeline: tuple[str, ...]) -> None:
    draw.text((45, 132), "RECENT WATCH EVENTS", font=_font(31, bold=True), fill=(244, 203, 112))
    entries = timeline[-9:] or ("No tracked changes have occurred since this watch started.",)
    for index, entry in enumerate(entries):
        y = 165 + index * 59
        clean = entry.replace("`", "").replace("**", "")
        draw.rounded_rectangle((42, y, 1158, y + 50), 7, fill=(255, 255, 255, 8))
        draw.text((60, y + 10), _fit_text(draw, clean, _font(23), 1070), font=_font(23), fill=_TEXT)


def _draw_discord_player_page(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    player: LivePlayer | None,
    item_icons: Mapping[int, Image.Image],
    hero_icons: Mapping[int, Image.Image],
) -> None:
    if player is None:
        draw.text((600, 390), "NO PLAYER DATA", font=_font(42, bold=True), fill=_MUTED, anchor="mm")
        return
    color = _team_color(player.team)
    hero = snapshot.hero(player.hero_id)
    hero_name = hero.name if hero else (
        f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
    )
    draw.rounded_rectangle((45, 120, 1155, 710), 12, fill=(*color, 38), outline=(*color, 125))
    draw.rounded_rectangle((80, 145, 220, 285), 18, fill=(*_avatar_color(player.hero_id), 255))
    initials = "".join(word[:1] for word in hero_name.split()[:2]).upper() or "?"
    hero_icon = hero_icons.get(player.hero_id) if player.hero_id is not None else None
    if hero_icon is not None:
        enlarged_hero = hero_icon.resize((132, 132), Image.Resampling.LANCZOS)
        image.paste(enlarged_hero, (84, 149), enlarged_hero)
    else:
        draw.text((150, 215), initials, font=_font(56, bold=True), fill=_TEXT, anchor="mm")
    player_name = _fit_text(draw, player.steam_name, _font(45, bold=True), 850)
    draw.text((255, 145), player_name, font=_font(45, bold=True), fill=_TEXT)
    draw.text((255, 207), hero_name, font=_font(31), fill=color)
    stats = (
        (255, 295, "SOULS", _compact(player.net_worth)),
        (485, 295, "K / D / A", f"{player.kills} / {player.deaths} / {player.assists}"),
        (745, 295, "PLAYER DAMAGE", _compact(player.hero_damage)),
        (970, 295, "OBJECTIVE", _compact(player.objective_damage)),
        (255, 400, "HEALING", _compact(player.hero_healing)),
        (485, 400, "LAST HITS", str(player.last_hits)),
        (745, 400, "DENIES", str(player.denies)),
        (970, 400, "LANE", str(player.assigned_lane or "—")),
    )
    for x, y, label, value in stats:
        draw.text((x, y), label, font=_font(20, bold=True), fill=_MUTED)
        draw.text((x, y + 35), value, font=_font(32, bold=True), fill=_TEXT)
    draw.text((80, 515), "CURRENT BUILD", font=_font(25, bold=True), fill=_MUTED)
    inventory = _ordered_inventory(snapshot, player)
    mapped_inventory = bool(snapshot.items)
    for slot in range(12):
        left = 80 + slot * 85
        item = inventory[slot] if slot < len(inventory) else None
        active = item is not None or (not mapped_inventory and slot < len(player.upgrades))
        fill_color = _item_color(item) if item is not None else _upgrade_color(slot)
        draw.rounded_rectangle(
            (left, 560, left + 68, 632),
            8,
            fill=(*fill_color, 230) if active else (255, 255, 255, 15),
            outline=(255, 255, 255, 28),
        )
        if item is not None and (icon := item_icons.get(item.item_id)) is not None:
            enlarged = icon.resize((60, 60), Image.Resampling.LANCZOS)
            image.paste(enlarged, (left + 4, 566), enlarged)


def _draw_discord_team(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    team: int | None,
    players: list[LivePlayer],
    *,
    top: int,
    highlighted_account_id: int | None,
    item_icons: Mapping[int, Image.Image],
    hero_icons: Mapping[int, Image.Image],
) -> None:
    color = _team_color(team)
    draw.text((38, top), _team_name(team).upper(), font=_font(29, bold=True), fill=color)
    draw.text(
        (962, top + 5),
        f"TEAM SOULS  {_compact(sum(player.net_worth for player in players))}",
        font=_font(19, bold=True),
        fill=(*color, 235),
        anchor="ra",
    )
    for index in range(6):
        y = top + 47 + index * 95
        player = players[index] if index < len(players) else None
        highlighted = player is not None and player.account_id == highlighted_account_id
        opacity = 52 if index % 2 == 0 else 34
        if highlighted:
            opacity = 88
        draw.rounded_rectangle(
            (34, y, 966, y + 87),
            7,
            fill=(*color, opacity) if player is not None else (255, 255, 255, 5),
            outline=(*((236, 220, 157) if highlighted else color), 210 if highlighted else 35),
            width=2 if highlighted else 1,
        )
        if player is None:
            continue
        hero = snapshot.hero(player.hero_id)
        hero_name = hero.name if hero else (
            f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
        )
        avatar = _avatar_color(player.hero_id)
        draw.rounded_rectangle(
            (48, y + 15, 101, y + 69), 10, fill=(*avatar, 255), outline=(255, 255, 255, 45)
        )
        initials = "".join(word[:1] for word in hero_name.split()[:2]).upper() or "?"
        hero_icon = hero_icons.get(player.hero_id) if player.hero_id is not None else None
        if hero_icon is not None:
            image.paste(hero_icon, (48, y + 15), hero_icon)
        else:
            draw.text((75, y + 42), initials, font=_font(20, bold=True), fill=_TEXT, anchor="mm")
        name = _fit_text(draw, player.steam_name, _font(22, bold=True), 285)
        draw.text((116, y + 13), name, font=_font(22, bold=True), fill=_TEXT)
        draw.text((116, y + 44), hero_name, font=_font(17), fill=(*color, 245))
        if highlighted:
            draw.text((397, y + 20), "YOU", font=_font(12, bold=True), fill=(239, 224, 169), anchor="ra")

        stat_line = (
            f"{_compact(player.net_worth)} SOULS   "
            f"{player.kills}/{player.deaths}/{player.assists} KDA   "
            f"{_compact(player.hero_damage)} DMG   "
            f"{_compact(player.objective_damage)} OBJ   "
            f"{_compact(player.hero_healing)} HEAL"
        )
        draw.text((430, y + 13), stat_line, font=_font(17, bold=True), fill=_TEXT)
        inventory = _ordered_inventory(snapshot, player)
        mapped_inventory = bool(snapshot.items)
        for slot in range(12):
            left = 430 + slot * 43
            item = inventory[slot] if slot < len(inventory) else None
            active = item is not None or (not mapped_inventory and slot < len(player.upgrades))
            fill_color = _item_color(item) if item is not None else _upgrade_color(slot)
            draw.rounded_rectangle(
                (left, y + 43, left + 35, y + 77),
                4,
                fill=(*fill_color, 230) if active else (255, 255, 255, 16),
                outline=(255, 255, 255, 25),
            )
            if item is not None and (icon := item_icons.get(item.item_id)) is not None:
                image.paste(icon, (left + 2, y + 45), icon)


def _draw_background(draw: ImageDraw.ImageDraw) -> None:
    for y in range(_HEIGHT):
        shade = int(7 + 10 * y / _HEIGHT)
        draw.line((0, y, _WIDTH, y), fill=(shade, shade + 2, shade + 4, 255))
    for x in range(-250, _WIDTH, 190):
        draw.polygon(
            ((x, 0), (x + 260, 0), (x + 40, _HEIGHT), (x - 220, _HEIGHT)),
            fill=(255, 255, 255, 3),
        )
    for x in range(30, _WIDTH, 48):
        for y in range(22, _HEIGHT, 48):
            draw.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, 12))


def _draw_header(
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    stream_status: str,
) -> None:
    draw.rectangle((0, 0, _WIDTH, 92), fill=(4, 5, 6, 225))
    draw.rectangle((0, 90, _WIDTH, 92), fill=(181, 145, 72, 160))
    draw.text((66, 25), "DEADLOCK", font=_font(24, bold=True), fill=(244, 203, 112))
    draw.text((66, 49), "LIVE SCOREBOARD", font=_font(35, bold=True), fill=_TEXT)

    status_colors = {
        "live": ((72, 207, 139), "LIVE"),
        "reconnecting": ((232, 171, 68), "RECONNECTING"),
        "unreachable": ((216, 82, 79), "FEED UNAVAILABLE"),
        "ended": ((137, 141, 145), "ENDED"),
    }
    color, label = status_colors.get(stream_status, status_colors["live"])
    label_width = _text_width(draw, label, _font(17, bold=True)) + 34
    left = _WIDTH - 66 - label_width
    draw.rounded_rectangle((left, 25, _WIDTH - 66, 62), 8, fill=(*color, 38), outline=(*color, 190))
    draw.ellipse((left + 13, 39, left + 21, 47), fill=(*color, 255))
    draw.text((left + 29, 34), label, font=_font(17, bold=True), fill=color)
    match_text = f"MATCH {snapshot.match_id}   •   {_duration(snapshot.game_time_seconds)}"
    draw.text((_WIDTH - 66, 72), match_text, font=_font(16), fill=_MUTED, anchor="ra")


def _draw_team(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    team: int | None,
    players: list[LivePlayer],
    *,
    top: int,
    highlighted_account_id: int | None,
    item_icons: Mapping[int, Image.Image],
    hero_icons: Mapping[int, Image.Image],
) -> None:
    color = _team_color(team)
    title = _team_name(team)
    total_souls = sum(player.net_worth for player in players)
    draw.text((70, top), title.upper(), font=_font(29, bold=True), fill=color)
    draw.text(
        (_WIDTH - 70, top + 4),
        f"TEAM SOULS  {_compact(total_souls)}",
        font=_font(18, bold=True),
        fill=(*color, 235),
        anchor="ra",
    )
    header_top = top + 42
    draw.rounded_rectangle((66, header_top, _WIDTH - 66, header_top + 35), 6, fill=(*color, 32))
    headings = (
        (142, "PLAYER / HERO", "la"),
        (570, "SOULS", "ra"),
        (650, "K", "ma"),
        (710, "D", "ma"),
        (770, "A", "ma"),
        (900, "PLYR DMG", "ra"),
        (1035, "OBJ DMG", "ra"),
        (1165, "HEALING", "ra"),
        (1220, "STATUES", "ma"),
        (1405, "ITEMS", "ma"),
    )
    for x, label, anchor in headings:
        draw.text((x, header_top + 10), label, font=_font(14, bold=True), fill=_MUTED, anchor=anchor)

    row_height = 47
    for index in range(6):
        y = header_top + 39 + index * row_height
        if index >= len(players):
            draw.rounded_rectangle((66, y, _WIDTH - 66, y + 43), 5, fill=(255, 255, 255, 5))
            continue
        player = players[index]
        highlighted = player.account_id == highlighted_account_id
        opacity = 48 if index % 2 == 0 else 31
        if highlighted:
            opacity = 82
        draw.rounded_rectangle(
            (66, y, _WIDTH - 66, y + 43),
            5,
            fill=(*color, opacity),
            outline=(*((236, 220, 157) if highlighted else color), 210 if highlighted else 35),
            width=2 if highlighted else 1,
        )
        _draw_player_row(
            image,
            draw,
            snapshot,
            player,
            y,
            color,
            highlighted,
            item_icons,
            hero_icons,
        )


def _draw_player_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
    y: int,
    team_color: tuple[int, int, int],
    highlighted: bool,
    item_icons: Mapping[int, Image.Image],
    hero_icons: Mapping[int, Image.Image],
) -> None:
    hero = snapshot.hero(player.hero_id)
    hero_name = hero.name if hero else (
        f"Hero {player.hero_id}" if player.hero_id is not None else "Unknown hero"
    )
    avatar = _avatar_color(player.hero_id)
    draw.rounded_rectangle((82, y + 4, 120, y + 39), 8, fill=(*avatar, 255), outline=(255, 255, 255, 45))
    initials = "".join(word[:1] for word in hero_name.split()[:2]).upper() or "?"
    hero_icon = hero_icons.get(player.hero_id) if player.hero_id is not None else None
    if hero_icon is not None:
        image.paste(hero_icon, (82, y + 4), hero_icon)
    else:
        draw.text((101, y + 21), initials, font=_font(15, bold=True), fill=(255, 255, 250), anchor="mm")
    name = _fit_text(draw, player.steam_name, _font(17, bold=True), 315)
    draw.text((136, y + 7), name, font=_font(17, bold=True), fill=_TEXT)
    if highlighted:
        name_width = _text_width(draw, name, _font(17, bold=True))
        badge_left = min(136 + name_width + 12, 455)
        draw.rounded_rectangle(
            (badge_left, y + 8, badge_left + 47, y + 27),
            5,
            fill=(225, 204, 130, 42),
            outline=(225, 204, 130, 170),
        )
        draw.text(
            (badge_left + 23, y + 18),
            "YOU",
            font=_font(10, bold=True),
            fill=(239, 224, 169),
            anchor="mm",
        )
    draw.text((136, y + 26), hero_name, font=_font(13), fill=(*team_color, 240))

    values = (
        (570, _compact(player.net_worth), "ra"),
        (650, str(player.kills), "ma"),
        (710, str(player.deaths), "ma"),
        (770, str(player.assists), "ma"),
        (900, _compact(player.hero_damage), "ra"),
        (1035, _compact(player.objective_damage), "ra"),
        (1165, _compact(player.hero_healing), "ra"),
    )
    for x, value, anchor in values:
        draw.text((x, y + 22), value, font=_font(18, bold=True), fill=_TEXT, anchor=anchor)

    inventory = _ordered_inventory(snapshot, player)
    mapped_inventory = bool(snapshot.items)
    for slot in range(12):
        left = 1270 + slot * 22
        item = inventory[slot] if slot < len(inventory) else None
        active = item is not None or (not mapped_inventory and slot < len(player.upgrades))
        fill_color = _item_color(item) if item is not None else _upgrade_color(slot)
        fill = (*fill_color, 230) if active else (255, 255, 255, 17)
        draw.rounded_rectangle(
            (left, y + 10, left + 18, y + 32),
            3,
            fill=fill,
            outline=(255, 255, 255, 22),
        )
        if item is not None and (icon := item_icons.get(item.item_id)) is not None:
            image.paste(icon, (left, y + 11), icon)


def _draw_statue_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    count: int,
    *,
    size: int = 18,
) -> None:
    """Draw a tiny gold statue/pedestal and its permanent-buff count."""
    center = x + size // 2
    draw.ellipse(
        (center - size // 5, y, center + size // 5, y + size * 2 // 5),
        fill=(*_GOLD, 255),
    )
    draw.polygon(
        (
            (center - size // 3, y + size * 2 // 5),
            (center + size // 3, y + size * 2 // 5),
            (center + size // 4, y + size * 3 // 4),
            (center - size // 4, y + size * 3 // 4),
        ),
        fill=(*_GOLD, 255),
    )
    draw.rounded_rectangle(
        (x, y + size * 3 // 4, x + size, y + size),
        max(1, size // 8),
        fill=(196, 137, 37, 255),
    )
    draw.text(
        (x + size + 4, y + size // 2),
        str(count),
        font=_font(max(12, size - 2), bold=True),
        fill=_GOLD,
        anchor="lm",
    )


def _draw_statue_details(
    draw: ImageDraw.ImageDraw,
    player: LivePlayer,
    x: int,
    y: int,
) -> None:
    draw.text(
        (x, y + 24),
        f"TOTAL {player.statue_buff_count}",
        font=_font(11, bold=True),
        fill=_GOLD,
        anchor="lm",
    )
    stats = (
        ("HP", "health"),
        ("WP", "weapon_power"),
        ("SPI", "spirit"),
        ("FIRE", "fire_rate"),
        ("AMMO", "ammo"),
        ("CD", "cooldown"),
    )
    for index, (label, stat) in enumerate(stats):
        center = x + 52 + index * 36
        tiers = player.statue_tiers(stat)
        draw.text((center, y + 4), label, font=_font(9, bold=True), fill=_MUTED, anchor="ma")
        draw.text(
            (center, y + 29),
            "/".join(str(value) for value in tiers),
            font=_font(11, bold=True),
            fill=_TEXT,
            anchor="ma",
        )
        draw.text((center, y + 51), f"Σ{sum(tiers)}", font=_font(10), fill=_GOLD, anchor="ma")


def _ordered_inventory(
    snapshot: LiveMatchSnapshot,
    player: LivePlayer,
) -> tuple[ItemSummary, ...]:
    order = {"weapon": 0, "vitality": 1, "spirit": 2}
    indexed = tuple(enumerate(snapshot.inventory(player)))
    return tuple(
        item
        for _, item in sorted(
            indexed,
            key=lambda pair: (order.get(pair[1].slot_type or "", 3), pair[0]),
        )
    )[:12]


def _decode_item_icons(
    icon_bytes: Mapping[int, bytes],
    *,
    size: tuple[int, int] = (16, 20),
) -> dict[int, Image.Image]:
    decoded: dict[int, Image.Image] = {}
    for item_id, payload in icon_bytes.items():
        try:
            with Image.open(BytesIO(payload)) as source:
                icon = source.convert("RGBA")
                decoded[item_id] = ImageOps.fit(
                    icon,
                    size,
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, ValueError):
            continue
    return decoded


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


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if _text_width(draw, value, font) <= max_width:
        return value
    clipped = value
    while clipped and _text_width(draw, clipped + "…", font) > max_width:
        clipped = clipped[:-1]
    return clipped + "…"


def _text_width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), value, font=font)
    return box[2] - box[0]


def _team_color(team: int | None) -> tuple[int, int, int]:
    return _AMBER if team == 2 else _SAPPHIRE if team == 3 else (130, 135, 140)


def _team_name(team: int | None) -> str:
    return "Amber" if team == 2 else "Sapphire" if team == 3 else "Unknown team"


def _avatar_color(hero_id: int | None) -> tuple[int, int, int]:
    value = hero_id or 0
    return (65 + value * 37 % 115, 70 + value * 61 % 105, 78 + value * 83 % 112)


def _upgrade_color(index: int) -> tuple[int, int, int]:
    palette = ((221, 173, 76), (201, 92, 80), (113, 163, 99), (132, 93, 173))
    return palette[index % len(palette)]


def _item_color(item: ItemSummary | None) -> tuple[int, int, int]:
    return {
        "weapon": (215, 157, 58),
        "vitality": (94, 159, 96),
        "spirit": (126, 88, 166),
    }.get(item.slot_type if item is not None else None, (128, 132, 137))


def _compact(value: int) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if abs(value) >= 1_000:
        rendered = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{rendered}k"
    return str(value)


def _duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}:{remaining:02d}"
