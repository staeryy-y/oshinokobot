# oshinokobot — Plan

## What this is

A Discord bot (discord.py) + FastAPI/htmx admin site, running as **one combined process** on watcher (single `run.sh`, single `$PORT`), backed by SQLite. Core feature: a daily character poll — bot posts a character with an image, server members vote (a) which audience archetype the character appeals to, and (b) an S/A/B/C/D tier rating. Admin manages characters, tags, config, and results through the web UI. Built as a cogs-based bot so more "misc fun" commands can be bolted on later.

Directly modeled on `~/Projects/scheduler-bot`'s conventions (confirmed against `watcher.staery.com/spec`): gateway bot, numbered SQL migrations + `schema_migrations` table + dedicated migration runner, `run.sh` that builds a venv → sources `.env` → runs migrations → `exec`s the app, secrets never committed, stdout-primary logging mirrored to a rotating file.

## Confirmed decisions

- **Appeal vote** = fixed archetype/audience tags, admin-managed (e.g. "tomboy fans", "kuudere fans"). Voter picks one tag from a dropdown.
- **Single process**: FastAPI/uvicorn is the thing bound to `127.0.0.1:$PORT`; discord.py's gateway client runs as a background `asyncio` task inside the same event loop (started from FastAPI's `lifespan`). One deploy, one SQLite file, no cross-process sync.
- **Scheduling**: a daily background task picks a **random character from the unused pool** (characters never yet posted) and posts it. No admin-curated ordering needed for v1.
- **Scope**: single guild (bot only ever lives in one server — `DISCORD_GUILD_ID` in `.env`, like scheduler-bot), but the **posting channel is admin-configurable** in the DB, not hardcoded.

## Assumptions (flag anything wrong — adjust before building)

1. **Poll lifecycle**: a poll stays open until the *next* day's poll job runs, at which point it auto-closes (final tallies locked, message edited to show results) and the new poll posts. No manual `/close` command, no fixed 24h timer — simplest MVP, avoids a separate expiry scheduler.
2. **Voting**: one vote per user per question, changeable until the poll closes (re-picking updates your vote, doesn't stack). Both votes required to fully participate, but they're independent — voting on tier doesn't require voting on appeal, or vice versa.
3. **Images**: admin uploads image files directly (not URLs) via the FastAPI form; stored on local disk under a `media/` dir in the persistent working directory (gitignored, survives redeploys — same pattern as the SQLite file). The bot attaches the file directly to the Discord message (`discord.File`) rather than needing a public URL, so the admin site never needs to be internet-reachable for images to show up.
4. ~~**Auth**: literal HTTP Basic Auth (browser-native prompt) on all `/admin/*` routes~~ — **superseded**: replaced with a real login page + server-side sessions (SQLite-backed, opaque cookie) after the initial build. See `architecture.md` for the as-built version. Password hashing (stdlib `hashlib.pbkdf2_hmac`, no bcrypt/argon2 C-extension dependency) and CLI-only user creation via `python -m cli.create_user` are unchanged.
5. **Health check**: `/healthz` is a small unauthenticated JSON `200 OK` route, separate from the admin UI (which lives under `/admin/*` behind auth) — so watcher's poller never gets bounced through a login redirect or counted against admin traffic.
6. **Timezone/time-of-day** for the daily post: one configurable `poll_post_time` (HH:MM) + IANA timezone, stored in the same admin-editable config row as the channel ID. Defaults to something reasonable (e.g. 09:00 America/New_York) until the admin changes it.
7. **Tier vote UI**: 5 buttons (S/A/B/C/D) with live vote-count labels, same in-place-update pattern as scheduler-bot's day/hour buttons (no separate "you voted" confirmation message). ~~Appeal-tag vote is a `discord.ui.Select` dropdown~~ — **superseded**: switched to one button per tag after initial build, for a clearer visual split between the two questions (tag buttons fill rows 0-3, tier buttons always sit alone on row 4 — a full row of gap makes the grouping obvious without needing anything beyond discord.py's classic layout). Caps at 20 tags now, not 25 — a message maxes out at 25 components total and the tier row always claims 5 of them. See `architecture.md`.
8. Only one open poll can exist at a time (matches "one per day"); no support for concurrent/backlog polls in v1.
9. Bulk import (see below) commits valid rows immediately rather than staging them for review first — bad entries are reported per-row, not blocked pre-commit. If a "preview before commit" step turns out to matter in practice, that's a small addition on top of the same endpoint, not a redesign.

## Bulk character import (Claude-assisted)

Point: a Claude session can scrape character data (wiki, fandom pages, etc.) and hand the admin a single JSON file; the admin UI ingests it in one step instead of uploading characters one at a time.

**Format** — top-level object wrapping an array, so a `version` or other metadata field can be added later without breaking the shape:

```json
{
  "characters": [
    {
      "name": "Ai Hoshino",
      "series": "Oshi no Ko",
      "image_base64": "iVBORw0KGgoAAAANSUhEUgA...",
      "image_mime": "image/png",
      "source_url": "https://example.fandom.com/wiki/Ai_Hoshino"
    }
  ]
}
```

- `name` and `image_base64` required; `series`, `image_mime`, `source_url` optional.
- `image_mime` must be explicit (`image/png`, `image/jpeg`, `image/webp`, or `image/gif`) rather than sniffed from bytes — Claude producing the file knows the source content type, and guessing from magic bytes is a needless failure mode. Defaults to `image/png` if omitted.
- `source_url` is stored on the character row purely for provenance/audit (where did this come from), not used for anything functional.
- Raw base64, no `data:` URI prefix.

**Endpoint** — `GET/POST /admin/characters/import`:
- GET renders a form with both a file-upload input (`.json`) and a paste-in textarea — either path accepted, whichever's more convenient in the moment.
- POST parses whichever was provided, then processes every entry in one pass rather than failing the whole batch on the first bad row:
  - **Duplicate check**: case-insensitive match on `(name, series)` against existing characters → skipped, reported, not an error.
  - **Malformed entry** (missing `name`/`image_base64`, invalid base64, unsupported mime) → reported as an error with the reason, rest of the batch still processes.
  - **Valid + new** → base64 decoded, written to `media/` with an extension derived from `image_mime`, row inserted.
- Response is an htmx partial: a results table (one row per input entry — name, status, detail) plus the refreshed character list/count. No separate confirm step.
- Batch size capped (e.g. 200 entries per request) purely to keep one request's memory/DB-transaction footprint sane — not a hard product constraint, just an implementation guardrail.

## Data model

```
schema_migrations   id, name, applied_at

users                                  -- admin accounts, CLI-created only
  id, username (unique), password_hash, created_at

characters
  id, name, series (nullable), image_path, source_url (nullable),
  uploaded_by -> users.id (nullable), created_at

archetype_tags
  id, name (unique), created_at

guild_config                            -- singleton row
  guild_id, channel_id (nullable until admin sets it),
  poll_post_time (HH:MM), poll_timezone (IANA name)

polls
  id, character_id -> characters.id, channel_id, message_id,
  status  enum(open, closed),
  posted_at, closed_at (nullable)

appeal_votes
  poll_id -> polls.id, user_id (discord id), tag_id -> archetype_tags.id
  PK (poll_id, user_id)                 -- one tag per user, overwritable

tier_votes
  poll_id -> polls.id, user_id (discord id), tier enum(S,A,B,C,D)
  PK (poll_id, user_id)                 -- one tier per user, overwritable
```

`characters` with no row in `polls` = the unused pool the daily job draws from.

## Layout

```
oshinokobot/
  run.sh                    # venv → pip install → source .env → migrate → exec uvicorn entrypoint
  watcher.config.json       # health_check_path: /healthz
  requirements.txt
  architecture.md           # design record, scheduler-bot-style
  setup.md                  # first-deploy runbook
  .gitignore                # .venv, .env, *.db, media/, __pycache__
  migrations/
    0001_initial.sql
  app/
    config.py               # env loading, fails loudly on missing DISCORD_BOT_TOKEN
    db.py                   # aiosqlite connect + query helpers (split by table if it grows)
    migrate.py              # python -m app.migrate
    logging_setup.py        # stdout + rotating file, mirrors scheduler-bot
    bot/
      client.py             # OshinokoBot(commands.Bot), started as asyncio.create_task from lifespan
      cogs/
        polls.py            # daily scheduling loop, vote interaction handlers
      views.py              # tag buttons + tier buttons, live count labels, row layout
    admin/
      server.py             # FastAPI app factory + lifespan (starts bot task, holds shared db conn)
      auth.py                # session-based login dependency (see architecture.md)
      routes/
        characters.py         # upload, list, delete, + bulk JSON import
        tags.py, polls.py, config.py
      templates/             # Jinja2 + htmx partials
      static/
    media/                   # uploaded images — gitignored, persists in working dir
  cli/
    create_user.py           # python -m cli.create_user
```

## Build order

1. **Scaffold + spec conformance**: `run.sh`, `watcher.config.json`, `config.py`, `db.py`, migration runner, `/healthz`, logging — get a bare FastAPI app deploying green on watcher before any features exist.
2. **Migrations + CLI user creation** — schema above, `python -m cli.create_user`.
3. **Admin UI: characters** — upload (name, series, image), list, delete, and bulk JSON import (see above). This unblocks having data to poll on.
4. **Admin UI: tags + guild config** — manage archetype tags, set channel/post-time.
5. **Discord bot**: gateway client, daily scheduling loop, poll posting (embed + image attachment + View), vote interaction handlers, auto-close-on-next-post logic.
6. **Admin UI: poll results/history** — view past polls, tallies, ~~and (per assumption 4) not raw per-user votes~~ — **superseded**: per-voter detail (who voted for what, with a display-name snapshot captured at vote time) was added after initial build. See `architecture.md`.
7. **`architecture.md` / `setup.md`** written for real, deploy checklist, first live test in a real guild.

## Post-v1 additions

Not in the original plan — added after the v1 build, on request:

- **`/results` slash command** — the bot's first slash command. First
  version showed just the most recently closed poll's results; corrected
  to the server's **cumulative** tier list instead — every closed poll's
  character grouped by its result tier, across the server's whole
  history, since that's the actual point of the daily poll (building up a
  running tier list over time). See `architecture.md` → *Slash commands*.
- **`/force-poll` slash command** — closes the current poll and posts a
  new one immediately, gated to members with Manage Server permission by
  default. Same underlying `post_new_poll()` the admin UI's manual
  trigger button already used — one code path, two front doors. See
  `architecture.md` → *Slash commands*.
- **Result tier**: closing a poll now computes `polls.result_tier` —
  whichever tier got the most votes, `NULL` if there were zero tier votes
  (not defaulted to any tier), ties broken randomly among only the tied
  tiers. See `architecture.md` → *Poll lifecycle*.
- **Results text is grouped by category**, not just counts — `**A**: Bob,
  Jim` rather than `**A**: 2`. Applies to both the closed-poll embed and
  `/results`; the admin site's poll detail page already had a per-voter
  table so it was left as-is. See `architecture.md` → *Poll lifecycle*.
- **Game filter**: `guild_config.active_series` restricts the daily pool
  to specific games/series (reusing `characters.series`, no new column
  there). Default is unrestricted — new games stay included automatically
  until an admin explicitly narrows it via the admin UI, at which point
  it's a sticky allowlist. The existing "never repick a used character"
  rule applies inside whatever the active filter is. See `architecture.md`
  → *Game filter*.
- **"Core"**: a second result alongside the result tier —
  `polls.result_tag_id`, whichever appeal tag got the most votes, same
  majority+random-tiebreak rule as `result_tier`. Shown everywhere the
  result tier is (Discord close embed, admin UI, public results page).
- **Public results page** (`/results`, `/results/<poll_id>`) — the one
  deliberately unauthenticated part of the web app. Three sections: "Tier
  List" (a cumulative *average* tier ranking, distinct from `result_tier`
  itself, which is the mode not the mean), "Types of Characters"
  (grouped by core), and "Individual Poll Results" (every closed poll,
  most recent first) — plus a per-character page with the same
  tier/appeal/per-voter breakdown the admin poll detail page has (minus
  the raw Discord user id). Deliberately no explanatory copy on the page,
  just headers, tables, and data — an earlier version had a methodology
  paragraph under each section, removed on request. See `architecture.md`
  → *Public results page*.
- **Poll-close backfill**: `result_tier`/`result_tag_id` are computed
  once, at close time — any poll that closed before those columns (or
  that computation) existed had them stuck `NULL` forever despite its
  votes being fully intact, which surfaced as a real bug (the public
  results page showing "no core" for characters that clearly had appeal
  votes). `app/migrate.py` now backfills both fields for any closed poll
  missing either one, every deploy, idempotently. See `architecture.md` →
  *Migrations*.
- **Poll deletion**: admins can delete a poll and its votes from
  `/admin/polls` (list) or the poll detail page. Since a character's
  "used" status is derived from whether a `polls` row exists for it,
  deleting a poll fully frees the character back into the unused pool —
  not just a hide. Doesn't touch a still-live Discord message if the
  poll being deleted was open. See `architecture.md` → *Poll deletion*.

## Open for later (not blocking v1)

- What happens if the character pool runs dry (no unused characters left) when the daily job fires — post nothing + log a warning, vs. recycle oldest-posted? Deferred until it's closer to actually happening.
- Multi-guild support was explicitly declined for now — if that ever changes, `guild_config` becomes a table instead of a singleton row, which is a straightforward migration.
- Additional "misc fun" cogs — no design work here since none were specified; the cog structure just leaves room for them.
