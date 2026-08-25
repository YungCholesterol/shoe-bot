# Shoe Bot Privacy Policy

Effective date: August 25, 2026

> Deployment owner: replace `YOUR_OPERATOR_NAME` and `YOUR_SUPPORT_CONTACT`
> below, then publish this file at a stable public URL before making your Discord
> application public.

## Operator and contact

Shoe Bot is operated by **YOUR_OPERATOR_NAME**. To request access, correction,
or deletion of data, contact **YOUR_SUPPORT_CONTACT**. Users can also delete
their own leaderboard record at any time with `/shoeforgetme`.

## Data processed

Shoe Bot receives new-message events from Discord for guild channels the bot can
view. It reads message text only after confirming that the event belongs to the
server's configured Shoe channel, and only long enough to perform a
case-insensitive substring check for `shoe`. It does not persist or log message
text. Its Discord.py message and optional member caches are disabled.

The live SQLite database stores only:

- Discord server ID and configured channel ID
- Server total Shoe count, current streak, and best streak
- Discord user ID and valid Shoe count, separately for each server

It does not store usernames, display names, avatars, emails, IP addresses,
message text, message history, analytics, telemetry, or advertising identifiers.

Up to 10,000 Discord message IDs are held only in process memory to prevent
same-session duplicate counting. Reset-confirmation user IDs are held in memory
for at most 30 seconds. Neither is persisted.

## Purpose and sharing

The IDs and counters are used only to run the Shoe game, show server statistics,
and produce per-server leaderboards. The operator does not sell this data or
share it with analytics, advertising, or tracking services. Discord processes
data under its own terms and privacy policy.

## Retention and deletion

Data remains in the live database only while needed to operate the game:

- `/shoeforgetme` deletes the caller's user ID and personal count in that server.
  Historical server totals/streaks remain as aggregates that are no longer tied
  to that user. Future valid messages create a new row.
- `/shoereset` lets a server administrator delete that server's counters and all
  user leaderboard rows after a second confirmation.
- Removing Shoe Bot from a server deletes that server's live configuration,
  counters, and user rows. Startup reconciliation handles removals that occurred
  while the bot was offline.
- If the operator stops offering Shoe Bot, the operator will delete the live
  database and retained backups unless retention is legally required.

The deployment operator must define a backup-retention schedule of no more than
30 days and propagate verified deletion requests through retained backups within
that period. Backups must be encrypted and access-controlled.

## Security

The bot requests only the Discord intents and channel permissions needed for the
game. The database uses atomic transactions, foreign-key isolation, WAL recovery,
and full synchronous writes. The deployment operator must keep the token in a
secret manager or private `.env`, run the database on encrypted local storage,
restrict filesystem access, and maintain protected backups.

## Changes

The operator will keep the published version of this policy current. Material
changes to the bot's data practices must be reflected here before deployment.

