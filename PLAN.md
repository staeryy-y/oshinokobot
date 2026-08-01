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
4. **Auth**: literal HTTP Basic Auth (browser-native prompt) on all `/admin/*` routes, checked per-request against a `users` table. Passwords hashed with stdlib `hashlib.pbkdf2_hmac` (no bcrypt/argon2 C-extension dependency — keeps us inside watcher's guaranteed toolset, which doesn't promise a compiler). Users created only via `python -m cli.create_user` (prompts username + password with `getpass`, never accepts a password as a CLI arg).
5. **Health check**: `/healthz` is a small unauthenticated JSON `200 OK` route, separate from the admin UI (which lives under `/admin/*` behind auth) — so watcher's poller never triggers a Basic Auth prompt or gets counted against admin traffic.
6. **Timezone/time-of-day** for the daily post: one configurable `poll_post_time` (HH:MM) + IANA timezone, stored in the same admin-editable config row as the channel ID. Defaults to something reasonable (e.g. 09:00 America/New_York) until the admin changes it.
7. **Tier vote UI**: 5 buttons (S/A/B/C/D) with live vote-count labels, same in-place-update pattern as scheduler-bot's day/hour buttons (no separate "you voted" confirmation message). Appeal-tag vote is a `discord.ui.Select` dropdown (tags list, capped at Discord's 25-option limit — flagged below as a v1 limit).
8. Only one open poll can exist at a time (matches "one per day"); no support for concurrent/backlog polls in v1.

## Data model

```
schema_migrations   id, name, applied_at

users                                  -- admin accounts, CLI-created only
  id, username (unique), password_hash, created_at

characters
  id, name, series (nullable), image_path, uploaded_by -> users.id (nullable), created_at

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
      views.py              # tag Select + tier buttons, live count labels
    admin/
      server.py             # FastAPI app factory + lifespan (starts bot task, holds shared db conn)
      auth.py                # HTTP Basic dependency
      routes/
        characters.py, tags.py, polls.py, config.py
      templates/             # Jinja2 + htmx partials
      static/
    media/                   # uploaded images — gitignored, persists in working dir
  cli/
    create_user.py           # python -m cli.create_user
```

## Build order

1. **Scaffold + spec conformance**: `run.sh`, `watcher.config.json`, `config.py`, `db.py`, migration runner, `/healthz`, logging — get a bare FastAPI app deploying green on watcher before any features exist.
2. **Migrations + CLI user creation** — schema above, `python -m cli.create_user`.
3. **Admin UI: characters** — upload (name, series, image), list, delete. This unblocks having data to poll on.
4. **Admin UI: tags + guild config** — manage archetype tags, set channel/post-time.
5. **Discord bot**: gateway client, daily scheduling loop, poll posting (embed + image attachment + View), vote interaction handlers, auto-close-on-next-post logic.
6. **Admin UI: poll results/history** — view past polls, tallies, and (per assumption 4) not raw per-user votes unless that's wanted for anti-cheat/audit — worth confirming when we get there.
7. **`architecture.md` / `setup.md`** written for real, deploy checklist, first live test in a real guild.

## Open for later (not blocking v1)

- What happens if the character pool runs dry (no unused characters left) when the daily job fires — post nothing + log a warning, vs. recycle oldest-posted? Deferred until it's closer to actually happening.
- Multi-guild support was explicitly declined for now — if that ever changes, `guild_config` becomes a table instead of a singleton row, which is a straightforward migration.
- Additional "misc fun" cogs — no design work here since none were specified; the cog structure just leaves room for them.
