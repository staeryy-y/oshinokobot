# oshinokobot — Setup

First-deploy runbook. See [`architecture.md`](architecture.md) for the
design decisions this assumes (single combined process, `.env`-based
secret ingestion, migrations run from `run.sh`).

**1. Discord Developer Portal (one-time, per bot)**
- Create the Application + Bot user at discord.com/developers/applications;
  capture `DISCORD_BOT_TOKEN` and note the guild (server) ID you'll invite
  it to (`DISCORD_GUILD_ID` — right-click the server icon with Developer
  Mode on, Copy Server ID).
- Nothing to enable under Privileged Gateway Intents —
  `Intents.default()` already covers this bot (no message content, member
  list, or presence needed; voting is entirely button/select interactions).
- Build an OAuth2 invite URL with scopes `bot` + `applications.commands`,
  permissions at minimum **View Channel**, **Read Message History**,
  **Send Messages**, **Embed Links**, and **Attach Files** (the poll image is sent as a message attachment, not a
  URL).
- Invite the bot to the target guild using that URL.

**2. Point watcher at the repo**
- Register the repo/branch with watcher through its own dashboard/CLI —
  not something the fetched spec page documents, so not detailed here.
- `watcher.config.json` is already committed and correct as-is
  (`health_check_path: /healthz`).

**3. Place the secret on the host, before first start**
- Create `.env` in the persistent working directory (alongside `run.sh`):
  ```
  DISCORD_BOT_TOKEN=...
  DISCORD_GUILD_ID=...
  ```
  Placed out-of-band (scp/ssh/whatever host access already exists) — never
  through git, never through `watcher.config.json`.
- `DISCORD_BOT_TOKEN` is optional: the app starts fine without it and runs
  admin-only (character/tag management, past results), just with no
  Discord bot online — useful if you want to stage characters before the
  bot is ready to invite. Set it whenever the daily poll should actually
  start posting.

**4. First deploy verification**
- Watch stdout logs (watcher's only log channel) for: migrations applying
  cleanly, `Uvicorn running on http://127.0.0.1:$PORT`, and then one of —
  "bot setup complete" (successful Discord login), "DISCORD_BOT_TOKEN not
  set — running admin-only" (expected if you skipped it in step 3), or a
  loud `Discord bot crashed` traceback if a token was set but is wrong.
- Confirm watcher reports the app healthy (it's polling `GET /healthz`) —
  true regardless of which of the three above you saw.

**5. Create the first admin account**
- On the host, in the app's working directory:
  ```
  ./.venv/bin/python -m cli.create_user
  ```
  Prompts for username and password (min 8 characters) — there is no other
  way to create an account. `.venv` already exists after the first
  `run.sh` run.

**6. Configure the bot through the admin site**
- Visit `http://<host>:<port>/admin/characters` (add whatever reverse
  proxy / port-forwarding gets you there — the app itself only binds
  `127.0.0.1`). You'll land on a login page — sign in with the account
  from step 5. The session cookie lasts 7 days; "Log out" is in the nav
  on every admin page.
- **Tags** (`/admin/tags`): add at least one archetype tag — with zero
  tags configured, the appeal-vote dropdown posts disabled.
- **Config** (`/admin/config`): set the channel ID (Developer Mode →
  right-click the target channel → Copy ID), daily post time (24-hour
  HH:MM), and IANA timezone.
- **Characters** (`/admin/characters`): upload at least one character —
  manually, or via bulk JSON import (see `architecture.md` → *Bulk
  character import* for the format; handy for handing a Claude scraping
  session's output straight to the admin UI).

**7. Confirm the first poll**
- Once a channel is set and at least one character exists, the next
  `poll_post_time` in the configured timezone posts automatically (within
  the next minute). If the bot starts after that time and has not posted
  today, it catches up. Check `/admin/polls` and the Discord channel to
  confirm it landed. The admin "Post a new poll now" button or `/force-poll`
  can test posting immediately; a manual poll counts as today's poll.
- For missed polls, check the deployed log for `daily_poll_check tick
  failed`, `failed to send poll message`, unreachable-channel errors, or
  an exhausted character pool. Verify the saved timezone, channel, game
  filter, and that the bot is connected (HTTP health alone doesn't prove it).
- Scheduler INFO logs go to stdout and the rotating `oshinokobot.log`
  (or `LOG_PATH`). Startup logs show whether it is waiting for Discord or
  checking every 60 seconds. Each check logs the local time, timezone,
  configured posting time and channel, followed by the skip reason or
  posting attempt. Successful sends include poll and Discord message IDs.
- Scheduler regression tests run locally without Discord credentials:
  `./.venv/bin/python -m unittest discover -s tests -v`.

**8. Token rotation**
- If the token ever leaks: regenerate it from the Developer Portal, update
  `.env` on the host, restart via watcher. No code or git changes needed —
  the token is never embedded anywhere else.

**Character notes:** members can use `/oshinoko-note message:your thoughts`
while a poll is open. Notes show anonymously in Discord and on the public
character result page after closing; admins see authors in poll detail and
exports. Deploy through `run.sh` to apply migration `0009_poll_notes.sql`;
startup syncs the new slash command (instant in configured guilds, subject
to Discord propagation delay globally).
