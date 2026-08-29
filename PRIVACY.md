# Shoe Bot Privacy Policy

Effective date: August 25, 2026

Shoe Bot is operated by **Yung Cholesterol**. Privacy questions and data
requests may be sent to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com).

## Scope

This policy covers the public Shoe Bot Discord application operated by Yung
Cholesterol. It does not cover Discord, Railway, GitHub, the creator's website,
or independently operated forks of this source code. Those services and forks
have their own operators and policies.

In short: the Discord application does not save or log message text. Its
SQLite database stores only the Discord IDs, settings, counters, and aggregate
records needed to operate the game. A person who separately emails support
sends ordinary email data to the support mailbox as described below.

## How Discord events are handled

Discord sends new-message events for server channels the bot is permitted to
view. Discord and discord.py necessarily deliver and deserialize those event
payloads in process memory before Shoe Bot's handler can filter them. The
handler then:

1. Ignores direct messages, bots, webhooks, and Discord-generated system
   notices. Ordinary messages and replies remain eligible.
2. Compares the event's server and channel IDs with the configured game channel.
3. Returns immediately for every other channel without accessing
   `message.content` in Shoe Bot's game logic.
4. In the configured channel, applies the server's fixed Classic or Creative
   matching rules to the new message text and, in Creative mode, any sticker
   names included with the event.
5. Passes only a match result and necessary Discord IDs to SQLite.
6. Adds a reaction, optionally sends a streak-break response, and releases the
   event from the handler.

Creative matching normalizes text in memory and recognizes fixed spelling
forms, footwear or skate emoji, and qualifying custom-emoji or sticker names.
It does not download, open, or analyze attachments, images, GIFs, videos, links,
or other media. It does not inspect reactions as game input.

Shoe Bot does not register edit or delete handlers. Edited and deleted messages
therefore never retroactively change counters.

## Data inventory

### Persisted in SQLite

| Data | Purpose | Retention |
| --- | --- | --- |
| Discord server ID and configured channel ID | Keep each server's game separate and route new events | Until the bot is removed from the server |
| Matching mode and gameplay mode | Apply that server's selected rules | Until changed or the bot is removed |
| Random Shoe enabled state, destination and audit-log channel IDs, timing range, UTC quiet hours, and next scheduled time | Deliver and audit optional administrator-configured Random Shoe posts and resume timing after restarts | Until changed or the bot is removed |
| Global total, current streak, and best streak | Provide server statistics | Until the reset control in `/shoesettings` is confirmed or the bot is removed |
| Discord user ID and accepted-message count | Personal statistics, milestones, and leaderboard ranking | Until `/forgetme`, a confirmed server reset, or removal |
| Last accepted contributor's Discord user ID in an active Relay | Enforce different consecutive contributors across messages and restarts | Until the streak ends, relevant settings change, `/forgetme`, a confirmed server reset, or removal |
| Completed streak length, completion time, and legacy-record flag | Maintain an aggregate top-10 Hall of Fame | Until pruned from the top 10, a confirmed server reset, or removal |

Hall of Fame rows contain no contributor or breaker ID. Milestones are
calculated from the stored personal count and do not require a separate
milestone profile. A legacy Hall of Fame record has no exact completion time
because it was migrated from the best streak stored by an earlier release.

SQLite may use associated WAL and shared-memory files containing the same
database records. The application enables SQLite secure deletion and attempts a
WAL checkpoint and truncation after user, reset, and server deletions.

### Used temporarily in process memory

| Data | Purpose | Typical lifetime |
| --- | --- | --- |
| Visible-channel message event content and metadata delivered by Discord | Receive and immediately route Discord events | Handler duration; game logic accesses content only in the configured channel |
| Configured-channel message text and sticker/custom-emoji names | Apply the selected fixed matcher | One message-handler invocation |
| Author ID and Discord mention object | Apply Relay, attribute an accepted count, and send an immediate break message | One message-handler invocation; the active Relay ID may be persisted as listed above |
| Server ID, author ID when matched, and a Boolean match result awaiting the ordered SQLite worker | Commit game transitions without blocking Discord's event loop | Until the queued database operation completes; message text and sticker/custom-emoji names are not placed on this queue |
| Up to 10,000 recent Discord message IDs | Prevent duplicate processing during one process session | Until evicted from the bounded cache or process restart |
| Discord guild/channel cache data, such as IDs, names, types, roles, and permissions | Maintain the Discord connection and evaluate channel access | Process lifetime; not copied to Shoe Bot's database |
| Slash-command interaction data, including the invoker, permissions, locale, and selected user/channel options | Authorize and answer commands | Interaction-handler duration |
| Setup/settings requester ID, server ID, selected channel/modes, interaction message reference, and associated interaction/webhook state | Keep administrator setup and the settings control center private and requester-bound | Up to 300 seconds (the setup wizard may expire sooner) |
| Reset requester ID, server ID, opaque in-memory token, interaction message reference, and associated interaction/webhook state | Prevent wrong-user, stale, or duplicate reset confirmation | Up to 30 seconds |

This implementation configures discord.py's normal message cache and optional
member cache off. It retains Discord's guild/channel cache for connection
routing and permission evaluation, but does not write cached names, roles, or
profile information to SQLite.

## Data the Discord application does not persist

The Discord application and its SQLite database do not persist the following
from Discord events or game interactions:

- Message text, embeds, links, components, polls, or message history
- Attachments, images, GIFs, videos, audio, or analyzed media
- Message IDs, sticker names, custom-emoji names, or reactions
- Usernames, display names, nicknames, discriminators, avatars, or user profiles
- Email addresses, phone numbers, IP addresses, physical locations, or ages
- Edited or deleted-message content
- Advertising identifiers, cookies, analytics events, telemetry, or behavioral
  profiles

The Discord application does not sell personal data and does not use it for
advertising, profiling, model training, or purposes unrelated to the game.
Support-email data is separate and described below.

## Information shown inside a server

Game responses share limited information with members who can use commands or
see the configured channel. Public statistics, leaderboard, and profile
responses may turn a stored Discord user ID into a Discord mention and show its
count, rank, or derived milestones. A streak-break message shows the breaker's
Discord mention and the aggregate streak length that ended. Hall of Fame and
server-stat responses show aggregate counts without contributor identities.
Discord controls who can see each channel and interaction response.

## Why the stored data is used

The stored data is used only to:

- Run the selected game in each configured server channel
- Enforce Relay alternation when enabled
- Show server and personal statistics, rankings, Hall of Fame records, and milestones
- Apply administrator settings, diagnostics, resets, and uninstall cleanup
- Fulfill a user's personal deletion request

Server data is isolated by Discord server ID. A user count in one server is not
combined with the same user's count in another server.

## Logs

Application logs contain operational details such as timestamps, component
names, command-sync totals, connected-server totals, and exception type names.
Shoe Bot does not intentionally write message text, usernames, user IDs, message
IDs, sticker names, custom-emoji names, bot tokens, or database contents to
application logs. Discord gateway debug logging is disabled because raw gateway
payloads can contain message content.

Railway may maintain infrastructure, deployment, and application-log copies
under its own retention and privacy practices.

## Support email

If a person contacts the operator by email, the support mailbox receives the
sender's email address, message, and ordinary email headers. This information is
kept outside Shoe Bot's SQLite database, used to answer the request or meet a
legal obligation, and retained only as long as reasonably necessary for those
purposes. Support email is provided through Google under
[Google's Privacy Policy](https://policies.google.com/privacy). The live bot
does not send Discord API data to Google. Do not email a Discord user/server ID,
message content, password, authentication code, or bot token. Use the
authenticated in-Discord controls described below for game records and
deletion. If those controls are unavailable, send a general request without
Discord identifiers; the operator can arrange an in-Discord verification step.

## Service providers and disclosure

Shoe Bot relies on:

- [Discord](https://discord.com/privacy) for accounts, servers, message events,
  reactions, and application commands
- [Railway](https://railway.com/legal/privacy) to execute the bot and host its
  persistent SQLite volume
- [GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
  to publish source code and policy documents; the live bot does not send game
  data to GitHub
- [Google](https://policies.google.com/privacy) to receive support email when a
  person chooses to contact the operator; the live bot does not send game or
  Discord API data to Google

Railway's [Terms](https://railway.com/legal/terms) are binding on use of its
service and limit private submissions to use needed to provide that service.
Railway's [Data Processing Addendum](https://railway.com/legal/dpa) describes
database encryption and access controls and states that separate execution is
required for the DPA itself to become binding. This policy does not claim that a
separately executed DPA applies where one has not been completed.

Shoe Bot does not send its database or game events to analytics, advertising,
or tracking services. Data is disclosed only through the in-server outputs and
service providers described in this policy, when a user directs a supported
sharing action, when legally required, or when reporting a security incident or
policy violation to Discord or another required authority.

## Access, deletion, and retention

- `/profile user:@user` displays the stored count, rank, and milestones derived
  from that count. Omitting the user displays the caller's profile.
- `/leaderboard` displays the top contributors and provides a Hall of Fame view
  containing aggregate completed-streak records.
- `/forgetme` deletes the caller's Discord user ID and personal count in that
  server. Aggregate total and best-streak values remain and may no longer equal
  the sum of visible leaderboard rows. If the caller is the last contributor to
  an active Relay, the current streak is ended so the bot no longer retains
  their user ID as Relay state. The completed aggregate streak length may enter
  the Hall of Fame, which contains no contributor ID. A later accepted message
  creates a new row. The deletion result is ephemeral and no public notice is
  sent, so the bot does not announce that someone made a privacy request.
- Saving a different configured channel, matching mode, or gameplay mode ends a
  non-zero active streak and may add its aggregate length to the Hall of Fame.
  Totals, best streak, user counts, and existing Hall records are not cleared.
  Saving identical settings leaves the active streak unchanged.
- The reset control inside `/shoesettings` lets a current server Administrator
  delete all of that server's counts, streaks, user rows, Hall of Fame rows, and
  Relay state after a private, requester-bound confirmation. The selected
  channel and modes remain.
- Removing Shoe Bot from a server deletes that server's live configuration and
  game data. Startup reconciliation handles a removal that occurred while the
  bot was offline.
- If the operated Shoe Bot service is discontinued, its live database will be
  deleted unless retention is legally required.

The operator does not currently create separate application-level database
backups. Railway may maintain infrastructure copies or backups under its own
policies and legal obligations. Logical deletion takes effect in the live
SQLite database immediately after the transaction commits, but provider-level
copies may expire on Railway's schedule.

Because message text is not retained, the operator cannot reconstruct past
messages when resolving a disputed count.

Use `/profile` to view your personal count, rank, and milestones, `/forgetme` to
delete your own stored user row, or ask a server Administrator to use the reset
control inside `/shoesettings` for a complete server reset. These authenticated
Discord actions are the preferred identity checks. If they are unavailable, contact
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com) without including
a Discord user/server ID or other Discord API data; the operator can arrange an
in-Discord verification step. Do not send message content, passwords,
authentication codes, or a bot token.

## Server administrator controls

Administrators select the channel and modes. They also control the channels
Discord is allowed to deliver to the bot. For the narrowest scope, deny the bot
role **View Channel** everywhere except the dedicated game channel.

Administrator-only commands use both Discord's default command permission and
a runtime Administrator check. Setup, settings, and reset components also
recheck the initiating user, server, current Administrator permission, and
in-memory confirmation token before changing data.

## Security

Shoe Bot uses limited Discord intents and permissions, keeps credentials out of
source control, configures normal message and optional member caches off, uses
atomic SQLite transactions, enables foreign keys and secure deletion, and runs
one production replica with Railway-hosted persistent storage under the Railway
terms described above.

No network or storage system can be guaranteed completely secure. Suspected
privacy or security issues should be reported to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com). Reports are
reviewed, and Discord, affected users, or authorities will be notified of
unauthorized access when required by law or Discord's rules.

## Age

Shoe Bot is intended only for people permitted to use Discord under
[Discord's Terms](https://discord.com/terms). It does not ask for or store a
user's age.

## Forks and self-hosted copies

This policy does not automatically apply to a fork or self-hosted copy. Its
operator must publish an accurate policy for its own word/phrase rules, hosting,
data collection, use, sharing, retention, security, and deletion process, and
must comply with Discord's current rules and applicable privacy law. Fork
operators must use encrypted persistent storage for Discord API data and
ensure every hosting, email, logging, monitoring, or other provider that
processes it has written service-provider terms satisfying Discord's Developer
Terms. Operators must also execute any DPA or addendum their circumstances
require before sending API data to that provider.

## Changes

This policy will be updated before the operated bot adopts a materially
different data practice. The effective date at the top identifies the current
version.
