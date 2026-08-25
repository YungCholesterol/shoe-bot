<img src="https://upload.wikimedia.org/wikipedia/commons/8/8d/President_Barack_Obama.jpg" alt="Shoe Bot profile picture" width="132" align="right">

# Shoe Bot

A Discord game that counts how long a server can keep saying `shoe`.

In the configured channel, a message containing `shoe` advances the streak. A
message without it ends the streak. Matching is case-insensitive, so `SHOE`,
`shoes`, `shoelace`, and `shoe!!!` all count.

[Privacy](PRIVACY.md) · [Terms](TERMS.md) · [Image and affiliation notice](NOTICE.md) · [Creator](https://yungcholesterol.com/)

Shoe Bot is an independent project. It is not affiliated with or endorsed by
Barack Obama, the Obama Foundation, or the United States government.

## What it tracks

- Total valid Shoe messages for each Discord server
- Current Shoe streak
- Best Shoe streak
- Valid Shoe messages contributed by each user
- A top-10 server leaderboard

The bot does not save or log message text. It processes only new messages in the
channel selected with `/shoesetup`. Messages from bots and webhooks are ignored,
and edits or deletions do not change the counters.

## Commands

| Command | Access | Result |
| --- | --- | --- |
| `/shoesetup channel:#channel` | Administrator | Select or change the server's Shoe channel. Existing statistics are kept. |
| `/shoestats` | Everyone | Show the server's total, current streak, and best streak. |
| `/shoestats user:@user` | Everyone | Show a user's valid-message total and leaderboard rank. |
| `/shoeleaderboard` | Everyone | Show the server's top 10 users. |
| `/shoecount` | Everyone | Show the server's total Shoe count. |
| `/shoeconfig` | Everyone | Show the configured Shoe channel. |
| `/shoeforgetme` | Everyone | Delete the caller's stored user ID and personal count after confirmation. |
| `/shoereset` | Administrator | Reset the server's statistics after confirmation. |

Administrator commands verify permissions when they run. Shoe Bot itself should
never be granted the Discord Administrator permission.

## Permissions

Shoe Bot needs only:

- View Channels
- Send Messages
- Add Reactions
- Read Message History
- Embed Links
- Use Application Commands

For installation, use the `bot` and `applications.commands` scopes. The bot does
not need permission to manage channels, messages, roles, or members.

## Message Content Intent

Message Content Intent is required because the game must briefly inspect a new
message to determine whether it contains `shoe`.

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Open the application.
2. Select **Bot**.
3. Find **Privileged Gateway Intents**.
4. Enable **Message Content Intent** and save.

The code enables the same intent. Both settings are required. The content is
discarded immediately after the check and is never written to SQLite or logs.
Discord may require a privileged-intent review if the application grows; see
Discord's [current intent documentation](https://docs.discord.com/developers/events/gateway#message-content-intent).

## Run it yourself

### Requirements

- Python 3.10 or newer
- A Discord application and bot token
- Persistent storage for SQLite
- One running bot process

### 1. Create the Discord application

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **General Information** and copy the Application ID.
3. Open **Bot**, create the bot user, and copy or reset its token.
4. Enable Message Content Intent.
5. Under **Installation**, enable Guild Install with the `bot` and
   `applications.commands` scopes.
6. Grant only the permissions listed above and use Discord's generated install
   link to add the bot to a server.

Treat the bot token like a password. Never place it in source code, screenshots,
logs, Discord messages, or GitHub.

### 2. Install the project

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

### 3. Configure `.env`

Copy `.env.example` to `.env`, then fill in:

```dotenv
DISCORD_TOKEN=your_bot_token
APPLICATION_ID=your_application_id
DEV_GUILD_ID=
DATABASE_PATH=data/shoe_bot.sqlite3
```

- `DISCORD_TOKEN` and `APPLICATION_ID` are required.
- `DEV_GUILD_ID` is optional. Set it while testing to sync commands quickly to
  one server. Leave it blank in production to register commands globally.
- `DATABASE_PATH` is optional. Production deployments should point it to
  persistent storage.

The Shoe channel is not configured in `.env`. An administrator selects it in
each server with `/shoesetup`.

### 4. Run and register commands

```powershell
python -m bot.main
```

Commands are synced at startup. With `DEV_GUILD_ID` set, they are registered in
that test server. Without it, they are registered globally and may take longer
to appear.

After the bot connects, run:

1. `/shoesetup channel:#your-channel`
2. `/shoeconfig`
3. Send a message in the selected channel to test the game.

### Railway

The included `railway.toml` starts the bot with `python -m bot.main`. Configure
the environment variables in Railway and mount a persistent volume at `/data`.
Set:

```dotenv
DATABASE_PATH=/data/shoe_bot.sqlite3
```

Run one replica. Multiple bot processes could receive and count the same Discord
event independently.

## Storage and reliability

SQLite stores only server IDs, configured channel IDs, user IDs, and counters.
Usernames and message contents are not stored.

Counter changes use atomic SQLite transactions. WAL mode, full synchronous
writes, foreign keys, a busy timeout, and a process lock protect the database
during concurrent messages. A bounded in-memory cache prevents one Discord
message from being counted twice during a single runtime session.

Removing the bot from a server deletes that server's live configuration,
counters, and leaderboard rows. See [PRIVACY.md](PRIVACY.md) for the complete
data inventory and deletion options.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers streak behavior, server isolation, persistence, rankings,
concurrent writes, permission checks, deletion, reset authorization, and message
deduplication.

## Creator

Shoe Bot was created by [Yung Cholesterol](https://yungcholesterol.com/), a
rapper, music producer, audio engineer, and multidisciplinary creator.

Contact: [yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com)

