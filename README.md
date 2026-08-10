# Duckies Bot

Discord bot for PvE Escape from Tarkov information. It uses the supported
static datasets at `https://json.tarkov.dev`.

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

## Setup

1. Install the package: `python -m pip install -e .`
2. Set `DISCORD_TOKEN` in `.env`.
3. Optionally set `DISCORD_GUILD_ID` for immediate development command sync.
4. Run `python main.py`.

Optional settings:

- `TARKOV_API_URL` defaults to `https://json.tarkov.dev`.
- `HTTP_TIMEOUT_SECONDS` defaults to `30` because the first dataset download is large.

The client caches the assembled PvE dataset for five minutes, uses ETags for
conditional refreshes, and keeps stale data available if a refresh fails.
The first quest-log scan lazily initializes RapidOCR and can take a few seconds;
screenshots are processed locally and are not sent to a separate OCR service.

## Tests

Run `python -m unittest discover -s tests -v`.
