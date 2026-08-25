# Shoe Bot Privacy Policy

Effective date: August 25, 2026

Shoe Bot is operated by **Yung Cholesterol**. Questions and data requests may be
sent to [yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com).

## Data processed

Shoe Bot receives new-message events from Discord for channels it can view. It
checks message text only after confirming that the message is in the server's
configured Shoe channel, and only long enough to determine whether it contains
`shoe` without regard to capitalization.

The live SQLite database stores:

- Discord server ID
- Configured Discord channel ID
- Discord user ID after that user sends a valid Shoe message
- Server total, current streak, and best streak
- Per-user valid Shoe count for that server

Shoe Bot does not store or log:

- Message text or message history
- Usernames, display names, nicknames, or avatars
- Email addresses or IP addresses obtained from Discord
- Analytics, telemetry, advertising identifiers, or tracking data

Up to 10,000 Discord message IDs are temporarily held in process memory to stop
duplicate counting during one runtime session. These IDs are not written to the
database. A reset-confirmation user ID may also remain in memory for up to 30
seconds.

## How the data is used

The stored IDs and counters are used only to:

- Run the Shoe game in the configured channel
- Show server statistics
- Produce a server leaderboard
- Apply setup, reset, and deletion requests

The data is not sold and is not used for advertising, analytics, or profiling.

## Service providers

Discord provides message and account identifiers to the bot under Discord's own
terms and privacy policy. Railway hosts the running bot and its persistent
SQLite volume. These providers may process operational data under their own
policies.

No analytics or advertising provider receives Shoe Bot data.

## Retention and deletion

- `/shoeforgetme` deletes the caller's user ID and personal count in the current
  server. Anonymous server totals and streak records remain.
- `/shoereset` lets a server administrator delete all counters and user rows for
  that server after confirmation.
- Removing Shoe Bot from a server deletes that server's live configuration,
  counters, and user rows. Startup reconciliation handles removals that occurred
  while the bot was offline.
- If Shoe Bot is discontinued, its live database will be deleted unless a
  longer period is legally required.

The operator does not currently maintain separate application-level database
backups. If backup practices change, this policy will be updated before the new
practice begins.

To request deletion by email, contact
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com) with the relevant
Discord user ID and server ID. Do not include message contents or a bot token.

## Security

Shoe Bot requests only the Discord permissions and intents required for the
game. The bot token is kept outside the source repository. SQLite updates use
atomic transactions, and the production database is stored on a persistent
Railway volume.

No online service can be guaranteed completely secure. Suspected privacy or
security problems should be reported to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com).

## Changes

This policy will be updated when Shoe Bot's data practices materially change.
The effective date at the top will identify the current version.

