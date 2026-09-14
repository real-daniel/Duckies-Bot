# Duckies Bot

Discord bot for PvE Escape from Tarkov information. It uses the supported
static datasets at `https://json.tarkov.dev`.

It also supports reusable Steam account links and Deadlock live-match lookups:

- `/steam link steam_id:<id>` associates your Discord user with a SteamID64,
  SteamID3, SteamID2, or numeric Steam profile URL.
- `/steam show user:<optional>` shows the account used by account-aware commands.
- `/steam unlink` removes your association.
- `/deadlock player query:<optional> user:<optional>` shows a player's rank,
  recorded win rate, recent form, activity, and five most-played heroes. Search
  by Steam name to get selectable avatar/profile cards, use an ID or numeric
  profile URL directly, select a linked Discord user, or omit both options to
  use your own linked account.
- `/deadlock scout match_id:<id-or-top-200> screenshot:<image>` is intended for the start of
  a match. Provide either the numeric ID or a full game screenshot; screenshot
  mode crops and reads only the bottom-right quadrant locally. It waits for the
  broadcast roster, then shows each player's rank, total ranked games, ranked
  hero game share and win rate, and latest five ranked results. Enrichment is cached for 15
  minutes. The result opens as a graphical full-lobby overview with a dropdown
  for navigating to detailed player cards. Each detailed card also lists the player's five
  most-played heroes with games, win rate, and play share; only the requester can
  control the dropdown.
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
- Companion-triggered matches wait for a complete lobby (6v6 standard or 4v4
  Street Brawl), post a ranked scouting graphic, wait three seconds, and then
  open the live watch using the same complete broadcast snapshot.
  They tolerate the normal Source TV startup delay. If Valve reports `Demo not
  available` or the stream opens without producing a player snapshot, the bot
  keeps the connecting notice, clears the cached broadcast URL, and retries for
  up to five minutes.
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

## Deadlock companion (first slice)

The companion watches Deadlock's local Source 2 console log and reports a new
match ID as soon as the game writes its connection or lobby line. It is
read-only: it does not inspect game memory, inject code, or intercept network
traffic.

1. Add `-condebug` to Deadlock's Steam launch options.
2. Install this project with `python -m pip install -e .`.
3. Run `duckies-companion --launch` to start Deadlock with the required flag
   and keep the companion watching in the same process.

If Deadlock is already running with logging enabled, use `duckies-companion`
without `--launch`. The launcher calls Steam directly as
`steam -applaunch 1422450 -condebug`; it does not edit Deadlock files or your
global Steam launch options. Use `--steam-path` only if Steam itself cannot be
found automatically.

Steam and custom library locations are detected automatically on Windows and
Linux. Use `--game-folder` or `--log-path` only when automatic discovery cannot
find the install. By default, each detected match is printed as one JSON line:

```json
{ "match_id": 100141930, "detected_at": "...", "source": "deadlock-console" }
```

The bot includes the authenticated receiver. In Discord, run
`/deadlock companion-pair` in the channel that should receive automatic watches,
or select another channel. The ephemeral response contains a one-time token and
the exact PowerShell commands for the gaming PC. Pairing again rotates the old
token; `/deadlock companion-disable` revokes it. Only token hashes are stored.

`python main.py` starts the receiver on `127.0.0.1:8080` by default. Keep that
port private and place an HTTPS reverse proxy in front of it. Set
`COMPANION_PUBLIC_URL=https://companion.example.com` on the VPS after the proxy
is ready. See `docs/companion-vps.md` for the Ubuntu/Nginx handoff.

Run `duckies-companion --help` for path overrides and one-shot mode. The
companion intentionally begins at the end of an existing log, preventing a
completed match from being submitted as live when it starts.

### Simple desktop UI

Launch the window with:

```powershell
duckies-companion-ui
```

The UI provides:

- a manual Steam `steamapps` folder field and directory browser;
- an additional launch-options field;
- companion endpoint and masked pairing-token fields;
- a **Play Deadlock** button; and
- live detection and delivery status.

The selected Steamapps path should directly contain `common/Deadlock`. The Play
button starts Steam with `-applaunch 1422450 -condebug` and appends the parsed
additional options. Arguments are passed directly to Steam without a command
shell. Settings are saved in the current user's local application-data folder,
so the pairing token should still be treated like a password. The companion
checks for Deadlock while its window is open, starts reading the log whenever
the game process appears, and pauses when the game exits. This works whether
Deadlock is started with the Play button, a mod launcher, or another shortcut;
those other launch methods must include `-condebug`. Pressing Play again does
not create a second monitor.

To build a standalone Windows executable that does not require Python on the
target PC:

```powershell
python -m pip install -r requirements-companion-build.txt
powershell -ExecutionPolicy Bypass -File scripts/build_companion.ps1
```

The distributable is written to `dist\DuckiesCompanion.exe`. It is a windowed,
single-file build with the Duckies app icon; copy that one executable to another
Windows PC and run it.

Optional settings:

- `TARKOV_API_URL` defaults to `https://json.tarkov.dev`.
- `DEADLOCK_API_URL` defaults to `https://api.deadlock-api.com`.
- `DEADLOCK_API_KEY` is optional.
- `DEADLOCK_LIVE_EVENTS_URL` defaults to `http://127.0.0.1:3000`.
- `DEADLOCK_SCOUT_TEMPLATE_PATH` defaults to
  `data/deadlock_scout_template.json`.
- `DATABASE_PATH` defaults to `data/duckies.sqlite3` and stores Steam links.
- `COMPANION_API_HOST` defaults to `127.0.0.1` so the receiver is private.
- `COMPANION_API_PORT` defaults to `8080`.
- `COMPANION_PUBLIC_URL` is the public HTTPS origin shown by the pairing command.
- `HTTP_TIMEOUT_SECONDS` defaults to `30` because the first dataset download is large.

The client caches the assembled PvE dataset for five minutes, uses ETags for
conditional refreshes, and keeps stale data available if a refresh fails.
The first quest-log scan lazily initializes RapidOCR and can take a few seconds;
screenshots are processed locally and are not sent to a separate OCR service.

## Docker deployment

The included Compose stack runs the Python bot and the Deadlock live-events
parser on one host. The parser is reachable only from the private Compose
network; port 3000 is not published to the internet.

1. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
2. Optionally set `DISCORD_GUILD_ID` and `DEADLOCK_API_KEY`.
3. Create the persistent data directory: `mkdir -p data`.
4. Build and start both services: `docker compose up -d --build`.
5. Follow startup logs: `docker compose logs -f --tail=100`.

SQLite data and the cached Deadlock scout template are stored in the host's
`data` directory and survive container replacement. Back up that directory
regularly. Do not commit `.env` or the contents of `data`.

To update the deployment after pulling repository changes:

```bash
docker compose pull
docker compose up -d --build
```

## Tests

Run `python -m unittest discover -s tests -v`.

## Embed style

All Discord embeds use the shared helpers in `src/duckies_bot/presentation.py`.
New features should use `make_embed`, `field_name`, and `finish_embed` so the
restrained palette, plain section labels, pagination, and minimal source footer
remain consistent. Decorative author branding and non-functional emoji should
not be added.

workflow test
