# Shoe Bot Privacy Policy

Effective date: August 25, 2026

Shoe Bot is operated by **Yung Cholesterol**. Privacy questions and data
requests may be sent to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com).

## Scope

This policy covers the Shoe Bot Discord application. It does not cover Discord,
Railway, GitHub, or external websites, which have their own privacy practices.

In short: Shoe Bot does not save or log message text. It stores only the Discord
IDs, channel configuration, and counters required to run the game.

## How a message is processed

Discord delivers new-message events for server channels the bot is able to
view. Shoe Bot processes each event in this order:

1. Ignore direct messages.
2. Ignore messages from bots and webhooks.
3. Compare the server and channel IDs with that server's configured Shoe
   channel.
4. If the message is outside the configured channel, ignore it without reading
   its text.
5. In the configured channel, check whether the new message contains `shoe`
   using a case-insensitive comparison.
6. Save only the resulting counters and, for a valid message, the author's
   Discord user ID.
7. Add the appropriate Discord reaction and discard the message text.

For an invalid message, no user ID is written to SQLite. If it breaks a non-zero
streak, the author is mentioned in the immediate streak-break response without
their username or ID being added to the database.

Shoe Bot does not process edits or deletions, so they never alter stored
counters.

## Exact data inventory

| Data | Location | Purpose | Retention |
| --- | --- | --- | --- |
| Discord server ID and configured channel ID | SQLite | Route the game to the correct server and channel | Until the bot is removed from the server |
| Server total, current streak, and best streak | SQLite | Provide server game statistics | Until an administrator resets the server or the bot is removed |
| Discord user ID and valid Shoe count | SQLite | Provide personal statistics and the server leaderboard | Until the user runs `/shoeforgetme`, an administrator resets the server, or the bot is removed |
| New message text in the configured channel | Process memory only | Perform the case-insensitive `shoe` check | Discarded after that message event is handled |
| Up to 10,000 recent Discord message IDs | Process memory only | Prevent duplicate counting during one runtime session | Removed as the cache fills or when the process restarts |
| Administrator user ID for a reset confirmation | Process memory only | Ensure only the administrator who started a reset can confirm it | Up to 30 seconds |

Discord may include additional account or message metadata in an event, such as
an author's username, display name, or avatar information. Shoe Bot does not use
or persist those profile fields. Discord.py's normal message cache and optional
member cache are disabled.

## Data Shoe Bot does not store

Shoe Bot does not store:

- Message text, attachments, stickers, embeds, or message history
- Usernames, display names, nicknames, discriminators, or avatars
- Email addresses, phone numbers, IP addresses, or physical locations
- Edited or deleted-message content
- Advertising identifiers, cookies, analytics, telemetry, or user profiles

Shoe Bot does not sell personal data and does not use it for advertising,
profiling, or unrelated purposes.

## Why the stored data is used

The stored IDs and counters are used only to:

- Run the game in each server's configured channel
- Show server and user statistics
- Rank valid Shoe contributions on a per-server leaderboard
- Apply configuration, reset, uninstall, and deletion requests
- Keep data from different Discord servers separated

## Logs

Application logs contain operational information such as timestamps, component
names, command-sync counts, server counts, and error types. Shoe Bot does not
place message text, usernames, user IDs, message IDs, or bot tokens in its
application logs. Discord gateway debug logging is disabled because raw gateway
payloads can contain message content.

Railway may maintain infrastructure and platform logs under its own privacy and
retention practices.

## Service providers and disclosure

Shoe Bot relies on:

- [Discord](https://discord.com/privacy) to provide the Discord platform,
  accounts, messages, reactions, and application commands
- [Railway](https://railway.com/legal/privacy) to run the bot and host its
  persistent SQLite volume

These providers process data under their own policies. Shoe Bot does not send
its database to analytics, advertising, or tracking providers. Data may also be
disclosed if required by law or reasonably necessary to protect the service,
its users, or another person's rights and safety.

## Retention, access, and deletion

- `/shoestats user:@user` shows a user's stored count and leaderboard rank in
  that server.
- `/shoeforgetme` deletes the caller's user ID and personal count in that
  server after confirmation. Historical server totals and streak records remain
  as aggregates no longer tied to that user. A later valid message creates a new
  row.
- `/shoereset` lets a server administrator delete that server's counters and
  all user leaderboard rows after confirmation. The selected Shoe channel
  remains configured.
- Removing Shoe Bot from a server deletes that server's live configuration,
  counters, and user rows. Startup reconciliation handles removals that occurred
  while the bot was offline.
- If Shoe Bot is discontinued, the live database will be deleted unless longer
  retention is legally required.

The operator does not currently create separate application-level database
backups. Railway may retain infrastructure data according to its own policies.
If Shoe Bot's backup practices change, this policy will be updated.

Because message text is not retained, the operator cannot reconstruct the
contents of past messages when resolving a disputed count.

To make an access, correction, or deletion request by email, contact
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com) and provide the
relevant Discord user ID and server ID. This information is used only to verify
and complete the request. Do not send message contents, passwords, or a bot
token.

## Server administrator controls

Server administrators choose the Shoe channel and control where the bot has
permission to view messages. To minimize even transient delivery by Discord,
administrators may deny the bot role **View Channel** everywhere except the
configured Shoe channel.

## Security

Shoe Bot uses limited Discord intents and permissions, keeps its token outside
the source repository, disables message and member caching, and uses atomic
SQLite transactions. The production database is stored on a persistent Railway
volume.

No internet service or storage system can be guaranteed completely secure.
Suspected privacy or security issues should be reported to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com).

## Age

Shoe Bot is intended only for people permitted to use Discord under
[Discord's Terms of Service](https://discord.com/terms). Shoe Bot does not ask
for or store a user's age.

## Changes

This policy will be updated before Shoe Bot adopts a materially different data
practice. The effective date at the top identifies the current version.

