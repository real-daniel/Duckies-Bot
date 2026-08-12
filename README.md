# Duckies Bot

Discord bot for PvE Escape from Tarkov information. It uses the supported
static datasets at `https://json.tarkov.dev`.

It also supports reusable Steam account links and Deadlock live-match lookups:

- `/steam link steam_id:<id>` associates your Discord user with a SteamID64,
  SteamID3, SteamID2, or numeric Steam profile URL.
- `/steam show user:<optional>` shows the account used by account-aware commands.
- `/steam unlink` removes your association.
- `/deadlock live user:<optional>` checks the linked account against the Deadlock
  active Watch feed. The feed is limited to the top 200 watch-listed matches, so
  a miss does not prove the user is offline.
- `/deadlock live match_id:<id-or-top-200>` reads Valve's broadcast through the local
  live-events parser and displays both teams, heroes, K/D/A, and current souls.
  A linked Steam account is highlighted when it appears in the supplied match.
- `/deadlock scout match_id:<id-or-top-200> screenshot:<image>` is intended for the start of
  a match. Provide either the numeric ID or a full game screenshot; screenshot
  mode crops and reads only the bottom-right quadrant locally. It waits for the
  broadcast roster, then shows each player's rank, selected-hero comfort (games
  and win rate), and latest five recorded results. Enrichment is cached for 15
  minutes. The result opens on a full roster overview with buttons to cycle
  through detailed player cards; only the requester can control the buttons.
- `/deadlock scout-preview` is an owner-only formatting sandbox. It instantly
  opens the last successfully cached scouting report without contacting Docker,
  Valve, OCR, or the Deadlock API. A representative 12-player sample is used
  until a real report has been cached.
- `/deadlock chat match_id:<id-or-top-200>` creates a public Discord thread and relays
  sanitized all-chat and team-chat messages from the delayed live broadcast.
  Duplicate relays are prevented and each server may run up to three at once.
- Valve broadcast URLs are fetched once per match and cached in SQLite for six
  hours, including across bot restarts. Live and scouting lookups reuse the URL
  instead of repeatedly calling Deadlock API. Chat startup currently makes one
  match-URL request because the upstream parser's direct-URL chat flag is broken.
- Hero metadata is fetched in one bulk request and cached for 24 hours instead of
  issuing one asset request per player.
- Entering `top-200` in a live, scout, or chat match-ID field randomly selects a
  match from Deadlock's active top-200 Watch feed. The feed is cached for 30 seconds.
- `/deadlock chat-stop match_id:<id>` stops a relay when run by its requester or
  a moderator with Manage Threads.

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
