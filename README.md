<img src="assets/shoe-bot-profile.jpg" alt="Shoe Bot profile picture" width="96" align="right">

# Shoe Bot

A small Discord game that counts how long a server can keep a Shoe streak alive.

Shoe Bot is independent. It is not affiliated with or endorsed by Barack Obama,
the Obama Foundation, the White House, or the United States government.

[Privacy](PRIVACY.md) · [Terms](TERMS.md) · [Notice](NOTICE.md) ·
[License](LICENSE) · [Creator](https://yungcholesterol.com/)

## How the game works

An administrator runs `/setup`, chooses one dedicated text channel, and selects
two game modes. The recommended defaults are **Creative** matching and
**Relay** gameplay.

### Matching

- **Creative** accepts a case-insensitive `shoe` substring; fixed forms such as
  `s-h-o-e`, `s h o e`, `sh0e`, repeated letters, full-width or styled Unicode
  text; footwear and skate emoji; and a custom emoji or sticker whose name
  contains a valid Shoe form.
- **Classic** accepts only a case-insensitive `shoe` substring. This includes
  `shoe`, `SHOE`, `shoes`, `horseshoe`, `shoelace`, and `shoe!!!`.

Feet, socks, footprints, skateboards, attachments, images, GIFs, videos, and
reactions do not count. One Discord message can add at most one count even when
it contains several matches.

The fixed Creative emoji set is `👞 👟 👠 👡 👢 🥾 🥿 🩰 🩴 ⛸️ 🛼`.

### Gameplay

- **Relay** requires different users on consecutive accepted messages. If the
  same user contributes twice in a row, the second message receives ❌, does not
  add to any count, and ends the streak.
- **Standard** allows the same user to contribute consecutive messages.

An accepted message receives ✅. Any other message receives ❌. A rejected
message announces the end of a non-zero streak, but no announcement is sent
when the streak was already zero. Bots, webhooks, and Discord-generated system
notices are ignored. Ordinary messages and replies count. Edits and deletions
never change counters.

Saving a different channel, matching mode, or gameplay mode completes any
active streak and applies the normal Hall of Fame rules. The total, best streak,
personal counts, and existing records are not cleared. Saving the same settings
leaves the active streak unchanged.

## Commands

| Command | Access | Result |
| --- | --- | --- |
| `/streak` | Everyone | Show the current streak, best streak, total count, and active modes. |
| `/profile [user]` | Everyone | Show a user's accepted count, rank, and milestones; defaults to the caller. |
| `/leaderboard` | Everyone | Show the top 10 contributors and switch to the Hall of Fame. |
| `/shoehelp` | Everyone | Show active rules, or recommended defaults before setup, plus commands, policies, and support. |
| `/forgetme` | Everyone | Delete the caller's stored user ID and personal count. |
| `/setup` | Administrator | Walk through channel, matching, gameplay, and permission setup. |
| `/shoesettings` | Administrator | Change settings, run diagnostics, or open the protected server reset. |
| `/shoelog` | Administrator | Choose the private audit-log channel required for Random Shoe posts. |
| `/shoetiming` | Administrator | Configure randomized timing and UTC quiet hours. |
| `/shoestatus` | Everyone | Report bot and Random Shoe configuration health. |
| `/forceshoe channel:#channel` | Administrator | Immediately send one Shoe post to exactly the selected channel without changing the timer. |

## Random Shoe posts

Random Shoe posts are a first-class optional feature and remain **off by
default**. Administrators can select one or more destinations in `/setup` or
`/shoesettings`, toggle scheduling, use `/shoetiming` for a randomized 5-minute
to 24-hour range and UTC quiet hours, and use `/shoelog` to choose the required
private audit channel. `/forceshoe` posts immediately to the one channel chosen
for that command, even when scheduled posts are off or unconfigured, without
resetting the timer. `/shoestatus` reports database, image, timing, quiet-hours,
destination, and audit-log health.

Each scheduled cycle draws a fresh delay and eligible channel, then sends
`Shoe` with the bundled image. Quiet hours postpone delivery. With one selected
channel every post goes there. The original image is stored and sent without
resizing or recompression.

The reset control inside `/shoesettings` atomically deletes the server's total,
current and best streaks, personal counts, Hall of Fame records, and Relay
state. It preserves the configured channel and selected modes. The settings
command, reset button, and separate confirmation all recheck the initiating
user's Discord **Administrator** permission.

If `/forgetme` removes the last-contributor ID from an active Relay, that streak
is completed and its aggregate length can enter the Hall of Fame. The result is
shown only to the requester; the bot does not publicly announce a privacy
request.

Shoe Bot itself should never receive Discord's Administrator permission.

## Minimum bot permissions

The bot role needs only:

- View Channel
- Send Messages
- Add Reactions
- Read Message History

The combined permission integer is `68672`. Installation uses the `bot` and
`applications.commands` OAuth scopes. “Use Application Commands” is not an
extra bot-role permission; application commands are installed through the
`applications.commands` scope. Members must still be allowed **Use Application
Commands** to invoke them, and server administrators can restrict access to the
app or individual commands in Discord's integration settings.

The bot does not need to manage channels, messages, roles, or members. For the
smallest transient data scope, deny the bot role **View Channel** everywhere
except the dedicated game channels.

## Message Content Intent

Message Content Intent is required because the game must inspect new message
content to decide whether it matches.

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Open the application.
2. Select **Bot**.
3. Under **Privileged Gateway Intents**, enable **Message Content Intent**.
4. Save, then restart the bot.

The code enables the same intent; the Portal and code must both enable it.
Discord's current review threshold is based on reach. When an app reaches
10,000 unique users who can access it and has a privileged intent enabled,
Discord sends a Developer Portal notification; the developer then has 90 days
from that notification to apply. Discord currently allows the app to keep
operating and growing while that review is pending and requires an annual
re-review.
This is separate from app verification, which is required to scale beyond 100
servers. See Discord's [privileged-intent review guide](https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review)
and [verification guide](https://support-dev.discord.com/hc/en-us/articles/23926564536471-How-Do-I-Get-My-App-Verified).

## Run your own instance

### Requirements

- Python 3.10 or newer
- A Discord application and bot token
- Persistent storage for SQLite
- Exactly one running bot process

### 1. Create and configure the Discord app

1. Create an application in the [Developer Portal](https://discord.com/developers/applications).
2. Under **General Information**, copy the Application ID.
3. Under **Bot**, create the bot, copy or reset its token, and enable Message
   Content Intent. An owner-only instance can leave **Public Bot** disabled. If
   other server owners should be able to install it, enable **Public Bot**.
4. Leave **Require OAuth2 Code Grant** disabled; this project does not implement
   that authorization flow.
5. Under **Installation**, enable **Guild Install** only and disable **User
   Install**. New applications may enable both by default. For Guild Install,
   select the `bot` and `applications.commands` scopes.
6. Grant only the four permissions listed above.
7. Add public URLs for this instance's current privacy policy and terms in the
   Developer Portal.
8. Keep the user-facing app profile accurate. State that it is a Shoe streak
   game, include a policy/support route, and—if using this profile image—state
   that it is independent and not affiliated with or endorsed by Barack Obama.
9. Use Discord's generated install link to add the bot to a server.

Making a bot publicly installable does not automatically list it in Discord's
App Directory; directory discovery is a separate program and configuration.

The person installing an app needs Discord's **Manage Server** permission. A
server **Administrator** must run Shoe Bot's setup and management commands.

Treat the bot token like a password. Never place it in source code, GitHub,
screenshots, Discord messages, or logs.

### 2. Find Discord IDs

Enable **User Settings → Advanced → Developer Mode**. Right-click a server and
select **Copy Server ID** when using `DEV_GUILD_ID`. The game-channel ID does
not belong in `.env`; `/setup` saves a channel separately for each server.

### 3. Install

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 4. Configure `.env`

Copy `.env.example` to `.env` and set:

```dotenv
DISCORD_TOKEN=your_bot_token
APPLICATION_ID=your_application_id
DEV_GUILD_ID=your_test_server_id
DATABASE_PATH=data/shoe_bot.sqlite3
```

- `DISCORD_TOKEN` and `APPLICATION_ID` are required.
- `DEV_GUILD_ID` is optional. Set it for fast command updates in one test
  server. Leave it blank when registering global commands for public servers.
- `DATABASE_PATH` is optional. Hosting platforms should point it at persistent
  storage.

### 5. Run and register commands

```powershell
python -m bot.main
```

Commands sync at startup. Guild commands update immediately when
`DEV_GUILD_ID` is set. Global commands are used when it is blank and can take
longer to update. Guild and global command namespaces can coexist; use a
separate development application or explicitly clean up old test-guild commands
before a public rollout.

After connection:

1. Run `/setup` as a server administrator.
2. Select the dedicated channel, matching mode, and gameplay mode.
3. Fix any missing permissions, recheck, and save.
4. Open `/shoesettings`, select **Run diagnostic**, and send test messages in
   the selected channel.

### Railway

`railway.toml` starts the bot with `python -m bot.main`. Add the environment
variables, mount a persistent volume at `/data`, and set:

```dotenv
DATABASE_PATH=/data/shoe_bot.sqlite3
```

Run exactly one replica. The runtime duplicate-message cache is process-local,
so multiple replicas could count the same Discord event twice. Railway states
that customer databases are encrypted at rest in its
[Data Processing Addendum](https://railway.com/legal/dpa).

## Data and reliability

SQLite stores only the data described in [PRIVACY.md](PRIVACY.md): Discord
server, channel, and user IDs; selected modes; counters; the active Relay
contributor ID; and aggregate Hall of Fame lengths and timestamps. It does not
store message text, usernames, attachments, or analyzed sticker/custom-emoji
names.

Every message transition uses one immediate SQLite transaction. WAL mode, full
synchronous writes, foreign keys, a busy timeout, secure deletion, and an
in-process re-entrant lock protect counters. Runtime database work goes through
one ordered worker thread, so durable disk waits do not block Discord's event
loop and Relay transitions cannot overtake each other. Configuration changes
and message classification share a transition lock so one message cannot cross
two rulesets. A bounded in-memory cache prevents duplicate processing only while
a message ID remains among its 10,000 entries. That protection does not survive
a restart and does not coordinate multiple replicas.

## Make a bot for another word or phrase

You are encouraged to fork this code and make a focused game for another word
or phrase. The source is available under the [MIT License](LICENSE). Use your
own Discord application and token, clearly identify your bot, keep permissions
and data collection minimal, and publish privacy and terms pages that accurately
describe your version. A fork must follow Discord's
[Terms](https://discord.com/terms), [Community Guidelines](https://discord.com/guidelines),
[Developer Terms](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service),
[Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy),
server rules, and applicable law. Store Discord API data only on encrypted
persistent storage. Before any hosting, email, logging, monitoring, or other
provider processes that data, require written service-provider terms that
satisfy Discord's Developer Terms, including access only for operating your app
at your direction. Complete any provider DPA or other addendum your situation
requires; Railway's published [DPA](https://railway.com/legal/dpa) requires
separate execution to become binding. Shoe Bot's policies and provider
arrangements do not automatically cover a separately operated fork.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers matching, system-message filtering, Relay and settings races,
ordered worker cancellation and shutdown, transaction and migration rollback,
persistence, Hall of Fame pruning, rankings, deletion/reset ordering,
administrator component authorization, duplicate clicks, post-commit response
failures, least-privilege intents, and runtime deduplication.

## Creator

Shoe Bot was created by [Yung Cholesterol](https://yungcholesterol.com/), a
rapper, music producer, audio engineer, and multidisciplinary creator.

Contact: [yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com)
