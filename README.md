# Shoe Bot

Shoe Bot is a small, privacy-conscious Discord game built with Python,
`discord.py`, and SQLite. Each Discord server chooses one text channel. In that
channel, an original message containing the case-insensitive substring `shoe`
gets a ✅ and advances the streak; any other original message gets a ❌ and
breaks a non-zero streak.

The bot is designed for one running process and any number of independently
configured Discord servers. It does not need to be publicly installable while
you test it in your own server. You can enable public installation later without
changing this code.

## Game behavior

- An administrator selects one channel with `/shoesetup channel:#channel`.
- `shoe`, `SHOE`, `shoes`, `shoe!!!`, `shoelace`, and any other text containing
  `shoe` count as valid.
- Messages from bots and webhooks, DMs, and messages outside that server's
  configured channel are ignored before their content is inspected.
- A valid message increments the server total, current streak, and that user's
  server-specific count. It may also set a new best streak.
- An invalid message resets the current streak. When it breaks a non-zero
  streak, the bot announces the user and the length of the broken streak.
- Edits and deletions never alter counters. Only the original `MESSAGE_CREATE`
  event is processed.
- A bounded cache of 10,000 message IDs prevents duplicate counting during one
  running session. Those IDs exist only in memory and are never written to disk.
- Tied leaderboard counts receive the same competition rank. For example, ranks
  can be 1, 2, 2, 4.
- If the bot is removed from a server, that server's configuration, counters,
  and user IDs are deleted in one cascading SQLite transaction. Reinstalling is
  safe but an administrator must run `/shoesetup` again. Startup reconciliation
  also purges stored guilds that are no longer installed when the bot reconnects.

## Slash commands

| Command | Who can use it | Purpose |
| --- | --- | --- |
| `/shoesetup channel:#channel` | Administrators | Select a dedicated channel such as `#shoe` without resetting existing statistics. |
| `/shoestats` | Everyone | Show this server's total, current streak, and best streak. |
| `/shoestats user:@user` | Everyone | Show that user's count and server leaderboard rank. |
| `/shoeleaderboard` | Everyone | Show this server's top 10 users in a clean Discord embed. |
| `/shoecount` | Everyone | Show this server's total Shoe count. |
| `/shoeconfig` | Everyone | Show the configured channel or explain that it is gone. |
| `/shoeforgetme confirm:Yes…` | Everyone | Delete the caller's stored user ID/count after an explicit choice. Anonymous server totals stay unchanged. |
| `/shoereset` | Administrators | Open a private 30-second confirmation before resetting only this server's statistics and leaderboard. |

The administrator commands have both Discord default-permission metadata and a
runtime Administrator check. The bot itself must **not** be granted
Administrator.

## Requirements

- Python 3.10 or newer
- A Discord application and bot token
- A local or persistent-disk location for SQLite
- One running bot process

## 1. Create the Discord application and bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and select **New Application**.
2. Name it **Shoe Bot** (or any name you prefer).
3. On **General Information**, copy the **Application ID**. This is the value for
   `APPLICATION_ID`.
4. Open **Bot**. Create the bot user if the portal asks you to do so.
5. Under the token section, select **Reset Token** or **Copy Token**, then save
   the token only in your local `.env` or your host's secret manager. This is the
   value for `DISCORD_TOKEN`.

Treat the token like a password. Do not paste it into Discord, source code,
screenshots, logs, or Git. If it is ever exposed, reset it immediately in the
Developer Portal and update the deployed secret.

### Private testing versus a public bot

The code supports multiple servers even if the application is private. Keep
**Public Bot** disabled while developing if only the owner or application team
needs to install it. When you want unrelated server owners to install it, enable
**Public Bot**, use a Discord-provided install link, and leave **Require OAuth2
Code Grant** disabled for this simple install flow. Public installation does not
automatically list the app in Discord's App Directory. Discord may separately
require application verification and privileged-intent review as the bot grows;
see the Message Content Intent section below.

## 2. Enable Message Content Intent

Shoe Bot must read the text of a new message long enough to evaluate
`"shoe" in message.content.casefold()`. Discord therefore requires the
privileged **Message Content Intent**.

1. In the Developer Portal, open the application.
2. Select **Bot**.
3. Find **Privileged Gateway Intents**.
4. Enable **Message Content Intent** and save.

The code also explicitly sets `intents.message_content = True`; both the code
setting and portal setting are required. Without it, Discord does not provide
normal guild message content and the game cannot classify messages correctly.
Only the Guilds and Guild Messages event categories are enabled alongside that
intent; the bot does not subscribe to DM message events.

For an app with access to fewer than 10,000 unique users across its installed
servers, enabling the portal toggle is sufficient. Under Discord's privileged-
intent policy effective June 10, 2026, Discord notifies the app owner at 10,000
accessible users and opens a 90-day application window. Approved privileged
intent access is reviewed annually. This user-based review is separate from app
verification, which Discord requires before an app can scale beyond 100
servers; Discord documents that separately in its [app verification
guide](https://support-dev.discord.com/hc/en-us/articles/23926564536471-How-Do-I-Get-My-App-Verified).
See Discord's [current privileged-intent review
guide](https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review)
and [policy announcement](https://support-dev.discord.com/hc/en-us/articles/40281523410967-Changes-to-Privileged-Intent-Access-for-Discord-Apps).

In an intent application, explain that the game cannot work through slash
commands alone, inspects only the server-configured channel, performs the
substring test in memory, immediately discards the content, and stores only IDs
and counters. The bot does not request the Server Members or Presence intents.

Shoe Bot processes content only after a message is confirmed to be in the
configured channel. It does not save or log the content. Discord.py's normal
in-memory message cache is disabled with `max_messages=None`, and optional guild
member caching is disabled with `MemberCacheFlags.none()`.

## 3. Configure installation and permissions

In the Developer Portal's **Installation** settings, enable a **Guild Install**.
Configure the guild-install scopes:

- `bot`
- `applications.commands`

Grant only these bot permissions:

- **View Channels**
- **Send Messages**
- **Add Reactions**
- **Read Message History**
- **Embed Links**

Their combined bot permission bitfield is `85056` if a host or URL builder asks
for the integer directly.

The `applications.commands` scope installs slash commands. Discord's **Use
Application Commands** channel permission controls whether server members may
invoke them and is normally enabled for members; it is not a reason to grant the
bot Administrator. Embed Links is used only for the leaderboard card. No
permissions for Manage Messages, Manage Channels, external emojis, kicking,
banning, or member management are needed.

Shoe Bot validates its five required channel permissions when an administrator
runs `/shoesetup`. Channel-specific overrides can otherwise prevent the game
from reacting or announcing a broken streak.

### Create and use the install link

Use the install link generated on the portal's **Installation** page. If you use
the OAuth2 URL Generator instead, select the `bot` and `applications.commands`
scopes and only the five permissions above. Open the link, choose a server where
you have **Manage Server**, and authorize it. Do not select Administrator.

## 4. Install the project

From the `shoe-bot` directory, create a virtual environment and install the two
runtime dependencies.

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 5. Configure `.env`

Copy the example file:

```powershell
Copy-Item .env.example .env
```

On macOS or Linux, use `cp .env.example .env`. Then edit `.env`:

```dotenv
DISCORD_TOKEN=your_bot_token
APPLICATION_ID=your_application_id
DEV_GUILD_ID=
DATABASE_PATH=data/shoe_bot.sqlite3
```

- `DISCORD_TOKEN` and `APPLICATION_ID` are required.
- `DEV_GUILD_ID` is optional. Set it to one test server ID for fast,
  guild-scoped command registration during development. Leave it empty or
  remove it for global production registration.
- `DATABASE_PATH` is optional. A relative path is resolved from the project
  directory. Use a path on persistent local storage in production.

There is deliberately no guild ID or Shoe channel ID in `.env`; each server
stores its own channel selection through `/shoesetup`.

To copy a server ID for `DEV_GUILD_ID`, enable **Developer Mode** in Discord's
User Settings under **Advanced**, right-click the server icon, and select
**Copy Server ID**.

## 6. Slash command registration

Command definitions are the same in both modes; `.env` controls where they are
synced at startup.

- **Development:** Set `DEV_GUILD_ID`. The bot copies the command definitions to
  that one guild and syncs them there, so command changes normally appear
  instantly. The application must already be installed in that guild.
- **Production:** Leave `DEV_GUILD_ID` unset. The bot syncs global commands,
  making them available to every server that installs the application. Global
  changes can take longer to appear everywhere.

Changing modes does not require editing Python. Restart the bot after changing
`.env`. If an old global command was registered during earlier testing, switching
to development mode does not remove that already-published global command; run
once in production mode after changing command definitions to reconcile the
global command set.

This follows Discord's recommendation to use [guild commands for testing and
global commands for public use](https://docs.discord.com/developers/interactions/application-commands#making-a-guild-command).

## 7. Run the bot

With the virtual environment active:

```powershell
python -m bot.main
```

The process logs connection state, command-sync results, and safe error types.
It pins `discord.gateway` logging at WARNING because gateway DEBUG logs can
contain raw message payloads. It never logs message text. Stop it cleanly with
Ctrl+C.

In each server:

1. Ensure the bot has the required permissions in the desired channel.
2. As an administrator, run `/shoesetup channel:#your-channel`.
3. Run `/shoeconfig` to verify the selection.
4. Start sending Shoe messages in that channel.

Changing the configured channel preserves existing statistics. If the channel
is deleted, the bot ignores all messages until an administrator selects a new
one; `/shoeconfig` explains the problem. Removing the bot purges that server's
row and leaderboard for privacy. A later reinstall starts unconfigured and does
not crash; an administrator simply runs `/shoesetup` again.

## SQLite persistence and concurrency

The database is created automatically at `data/shoe_bot.sqlite3` unless
`DATABASE_PATH` overrides it. The schema contains:

```text
guild_settings
- guild_id (primary key)
- shoe_channel_id
- total_shoes
- current_streak
- best_streak

user_stats
- guild_id + user_id (composite primary key)
- shoe_count
```

`user_stats.guild_id` references `guild_settings.guild_id`, and the leaderboard
has a `(guild_id, shoe_count)` index. Every query includes the guild ID, so one
server's setup, counters, reset, and leaderboard cannot affect another server.

Each message update uses one `BEGIN IMMEDIATE` transaction covering both the
server counters and user count. A process lock serializes access to the shared
SQLite connection. WAL mode, full synchronous writes, foreign keys, and a
five-second busy timeout are enabled. `/shoereset` updates server counters and
deletes that server's user rows in one transaction. A server uninstall deletes
its settings row and cascades to its user rows in one transaction.

## Privacy: exact data inventory

Persisted in SQLite:

- Discord guild/server ID
- Configured Discord channel ID
- Discord user ID, only after that user contributes a valid Shoe message
- Total Shoe count, current streak, best streak, and per-user Shoe count

Held temporarily in memory:

- Discord delivers transient Message objects, including author metadata and
  content, for new messages in guild channels the bot can view. Discord.py holds
  each object while dispatching it, but disables its message cache. Shoe Bot
  checks guild/channel IDs before it ever reads content.
- Guild and channel metadata needed to route events and check permissions;
  optional member caching is disabled
- The Boolean result of the `shoe` substring check
- Up to 10,000 recent Discord message IDs for same-session deduplication
- The administrator's user ID for at most 30 seconds while a reset confirmation
  is active

Not stored or logged by Shoe Bot:

- Message text or message history
- Usernames, display names, discriminators, or nicknames
- Avatars or other profile data
- Email addresses or IP addresses
- Edited or deleted-message content
- Analytics, telemetry, tracking identifiers, or third-party service data

Leaderboard output uses `<@user_id>` mentions at response time. Mentions in
leaderboards and statistics are configured not to ping. If a user has left or
cannot be resolved, Discord may show an unresolved mention; the command still
works and no username cache is needed.

Users can run `/shoeforgetme` with its required confirmation choice to delete
their user-ID/count row in the current server. The server's historical total and
streak numbers remain as anonymous aggregates. A future valid message creates a
new row. `/shoereset` deletes all user rows and aggregates for one server, while
uninstalling the bot deletes that server's complete live-database record.

To minimize even transient gateway delivery, a server administrator may deny the
bot role **View Channel** everywhere except the configured Shoe channel (while
leaving application commands usable where desired).

Before public distribution, customize and publish [PRIVACY.md](PRIVACY.md) at a
stable public URL, add the URL and an operator support contact in the Developer
Portal, and follow the stated backup-retention/deletion process. Discord's
[Developer Terms](https://support-dev.discord.com/hc/articles/8562894815383-Discord-Developer-Terms-of-Service)
require a public, accurate privacy policy and an accessible deletion method.

## Back up and restore

The simplest consistent backup is:

1. Stop the bot cleanly so SQLite checkpoints the WAL.
2. Copy `data/shoe_bot.sqlite3` to protected backup storage.
3. Restart the bot.

Alternatively, with the SQLite command-line tool installed, its online backup
command can create a consistent snapshot while the bot runs:

```bash
sqlite3 data/shoe_bot.sqlite3 ".backup 'shoe_bot-backup.sqlite3'"
```

Keep backups private because they contain Discord IDs. To restore, stop the bot,
replace the database with a known-good backup at `DATABASE_PATH`, and start the
bot again. Never commit the database, its `-wal`/`-shm` sidecars, `.env`, or a
token; the included `.gitignore` excludes them.

## Basic 24/7 deployment guidance

- Run `python -m bot.main` under a process supervisor or container restart policy.
- Put `.env` values in the host's secret manager when one is available.
- Mount `DATABASE_PATH` on persistent, encrypted **local** disk. Ephemeral
  filesystems lose all statistics on redeploy. Host-volume or full-disk
  encryption protects Discord IDs at rest without adding another database.
- Run exactly one bot process against this database. Multiple replicas would
  have separate message-deduplication caches and could process one gateway event
  twice even though SQLite itself serializes writes.
- Back up the database regularly and test restoration.
- Give the host account access only to the application directory, database, and
  required secrets.
- Keep normal logs at INFO or WARNING. Do not add debug statements that include
  `message.content`, and do not lower `discord.gateway` to DEBUG.
- Update dependencies deliberately, then run the tests before redeploying.

For a much larger, sharded deployment, revisit the single-process/SQLite design
and cross-process deduplication before adding replicas. That complexity is
intentionally outside this lightweight bot.

## Tests

Run the standard-library test suite from the project directory:

```powershell
python -m unittest discover -s tests -v
```

The tests cover multi-server isolation, persistence, streak behavior, tie ranks,
isolated and concurrent resets, channel changes, uninstall deletion, rapid
concurrent writes, configured-channel privacy filtering, runtime deduplication,
Discord permission failures, gateway-intent scope, individual deletion, and
reset authorization.

