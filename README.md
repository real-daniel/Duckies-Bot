# Duckies Bot

Discord bot for PvE Escape from Tarkov information. It uses the supported
static datasets at `https://json.tarkov.dev`.

It also supports reusable Steam account links and Deadlock live-match lookups:

- `/steam link steam_id:<id>` associates your Discord user with a SteamID64,
  SteamID3, SteamID2, or numeric Steam profile URL.
- `/steam show user:<optional>` shows the account used by account-aware commands.
- `/steam unlink` removes your association.
- `/deadlock scout match_id:<id-or-top-200> screenshot:<image>` is intended for the start of
  a match. Provide either the numeric ID or a full game screenshot; screenshot
  mode crops and reads only the bottom-right quadrant locally. It waits for the
  broadcast roster, then shows each player's rank, total recorded games, current
  hero game share and win rate, and latest five recorded results. Enrichment is cached for 15
  minutes. The result opens on a full roster overview with buttons to cycle
  through detailed player cards. Each detailed card also lists the player's five
  most-played heroes with games, win rate, and play share; only the requester can
  control the buttons.
- `/deadlock scout-preview` is an owner-only formatting sandbox. It instantly
  opens the last successfully cached scouting report without contacting Docker,
  Valve, OCR, or the Deadlock API. A representative 12-player sample is used
  until a real report has been cached.
- `/deadlock chat match_id:<id-or-top-200> screenshot:<image>` creates a public Discord
  thread and relays sanitized all-chat and team-chat messages from the live
  broadcast. Duplicate relays are prevented and each server may run up to three
  at once.
- `/deadlock watch match_id:<optional-id-or-top-200> screenshot:<optional-image>`
  defaults to finding your linked Steam account in Deadlock's top-200 active
  Watch feed. You can instead provide a match ID or screenshot. It posts one
  live match dashboard with detailed tabs and edits it at most once every 20
  seconds as player stats change. It
  keeps one Valve event stream open rather than polling Deadlock API. Each
  server may run up to three match watches. Interrupted streams reconnect with
  the cached broadcast URL; the embed displays reconnecting, unavailable, and
  match-ended states instead of silently freezing. A 45-second event inactivity
  watchdog also recovers parser connections that remain open after Valve stops
  sending frames.
- Valve broadcast URLs are fetched once per match and cached in SQLite for six
  hours, including across bot restarts. Live and scouting lookups reuse the URL
  instead of repeatedly calling Deadlock API. Chat startup currently makes one
  match-URL request because the upstream parser's direct-URL chat flag is broken.
- Hero metadata is fetched in one bulk request and cached for 24 hours instead of
  issuing one asset request per player.
- Entering `top-200` in a live, scout, chat, or watch match-ID field randomly selects a
  match from Deadlock's active top-200 Watch feed. The feed is cached for 30 seconds.
- `/deadlock chat-stop` and `/deadlock watch-stop` accept either a match ID or
  screenshot and stop a session when run by its requester or an appropriate moderator.

- `/tarkov item name:<name>` shows PvE flea prices, the best trader sell price,
  and related tasks.
- `/tarkov task name:<name> detailed:<true|false>` shows a raid-prep summary
  with required items, keys, objectives, clickable maps, prerequisites, and
  rewards. Detailed mode expands every objective and reward.
- `/tarkov quest-log screenshot:<image> detailed:<true|false>` reads up to four
  English quest-log screenshots locally, recognizes task names, and combines
  their items, Found-in-Raid requirements, keys, maps, and objectives. Uncertain
  text is shown separately rather than silently guessed.
- `/tarkov status` shows the global EFT status, every reported service component,
  and active or recent notices from `json.tarkov.dev/status`.
- `/tarkov craft-profit top:<1-20> sort_by:<profit|hourly>` ranks PvE crafts
  using consumed material cost, reusable tool capital, and robust recent flea
  floors.
- `/tarkov barter-profit top:<1-20>` ranks PvE trader barters by gross savings.
- `/tarkov flips top:<1-20> include_task_locked:<true|false> pmc_level:<1-100>
  high_liquidity_only:<true|false>` ranks items bought from traders and resold
  on the PvE flea market. It can exclude task unlocks, estimate accessible
  loyalty levels from PMC level, and hide low-liquidity items.

Profit rankings use median recent flea-floor samples with outlier filtering and
fall back to the latest low or trader price when history is unavailable. Values
are gross estimates and do not include flea fees, fuel, or user-specific trader
access.

## Setup

1. Install the package: `python -m pip install -e .`
2. Set `DISCORD_TOKEN` in `.env`.
3. Optionally set `DISCORD_GUILD_ID` for immediate development command sync.
4. Start the Deadlock live parser with `docker compose up -d`.
5. Run `python main.py`.

Optional settings:

- `TARKOV_API_URL` defaults to `https://json.tarkov.dev`.
- `DEADLOCK_API_URL` defaults to `https://api.deadlock-api.com`.
- `DEADLOCK_API_KEY` is optional.
- `DEADLOCK_LIVE_EVENTS_URL` defaults to `http://127.0.0.1:3000`.
- `DEADLOCK_SCOUT_TEMPLATE_PATH` defaults to
  `data/deadlock_scout_template.json`.
- `DATABASE_PATH` defaults to `data/duckies.sqlite3` and stores Steam links.
- `HTTP_TIMEOUT_SECONDS` defaults to `30` because the first dataset download is large.

The client caches the assembled PvE dataset for five minutes, uses ETags for
conditional refreshes, and keeps stale data available if a refresh fails.
The first quest-log scan lazily initializes RapidOCR and can take a few seconds;
screenshots are processed locally and are not sent to a separate OCR service.

## Tests

Run `python -m unittest discover -s tests -v`.

## Embed style

All Discord embeds use the shared helpers in `src/duckies_bot/presentation.py`.
New features should use `make_embed`, `field_name`, and `finish_embed` so the
restrained palette, plain section labels, pagination, and minimal source footer
remain consistent. Decorative author branding and non-functional emoji should
not be added.
