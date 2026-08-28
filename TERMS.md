# Shoe Bot Terms of Use

Effective date: August 25, 2026

These terms apply when you install, access, or use the public Shoe Bot Discord
application operated by **Yung Cholesterol**.

## Discord's rules apply

You may use Shoe Bot only if you are permitted to use Discord. You must follow
the current versions of:

- [Discord's Terms of Service](https://discord.com/terms)
- [Discord's Community Guidelines](https://discord.com/guidelines)
- Other Discord policies that apply to your account, server, content, or use
- The rules of every Discord server where you use Shoe Bot
- Applicable laws and regulations

Discord's current rules apply even if Discord changes them after these Shoe Bot
terms are published. If these terms conflict with a Discord requirement, follow
Discord's requirement. Breaking Discord's rules is also a violation of these
terms.

Anyone who operates a fork, self-hosted copy, or other Discord application using
this code must also follow Discord's
[Developer Terms](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service),
[Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy),
documentation, and applicable review requirements.

## What Shoe Bot does

Shoe Bot is a word game for one administrator-selected server channel.

- **Classic matching** accepts a case-insensitive `shoe` substring.
- **Creative matching** additionally accepts a fixed set of spelling variants,
  Unicode forms, footwear or skate emoji, and qualifying custom-emoji or
  sticker names.
- **Standard gameplay** allows consecutive accepted messages from the same
  user.
- **Relay gameplay** requires different consecutive contributors. A repeat by
  the same user rejects that message and ends the current streak.

One Discord message can add at most one count. Invalid messages end a non-zero
streak. Bots and webhooks are ignored. Edits and deletions do not retroactively
change statistics. Discord-generated system notices are also ignored; ordinary
messages and replies remain eligible. The exact active rules are available
through `/shoehelp`.

Random Shoe posts are optional and disabled by default. A server Administrator
may enable them through `/setup` or `/shoesettings` and choose one or more
eligible channels. While enabled, Shoe Bot periodically selects one of those
channels and sends `Shoe` with the bundled image at randomized intervals between
50 and 103 minutes. Administrators are responsible for choosing appropriate
channels and may disable the feature at any time.

Administrators may configure a delay range from 5 minutes through 24 hours,
set UTC quiet hours, use `/forceshoe` to send an immediate post to one selected
channel, and select a private audit channel. Forced posts work independently of
scheduled-post settings and do not reset the timer. They are deliberate administrator actions
and are not restricted by quiet hours. Administrators remain responsible for
channel choice, frequency, and server-member expectations.

The leaderboard ranks accepted personal contributions. `/profile` shows fixed
milestones derived from a user's stored count. The Hall of Fame view inside
`/leaderboard` shows the ten highest distinct completed streak lengths for that
server; an active streak is not added until it ends. These features are a game,
not an authoritative record of message history or user conduct.

## Administrator controls and responsibility

A Discord server Administrator can:

- Select or change the dedicated channel, matching mode, and gameplay mode
- Run a read-only permission diagnostic
- Permanently reset that server's total, streaks, user counts, Hall of Fame, and
  Relay state after confirmation

Saving a different channel, matching mode, or gameplay mode completes a
non-zero active streak and applies the normal Hall of Fame insertion and pruning
rules. Existing totals, best streak, personal counts, and prior records are not
cleared. Saving identical settings leaves the active streak and Relay
last-contributor state unchanged.

Server owners and administrators are responsible for:

- Choosing an appropriate dedicated channel and ruleset
- Granting only the permissions Shoe Bot needs
- Restricting who can view or post in the selected channel when appropriate
- Informing members that Discord delivers new message events to the bot and
  that configured-channel content is checked under the selected matcher
- Handling server moderation and enforcing server rules
- Removing the bot when its use is no longer appropriate

Shoe Bot should not be granted Discord's Administrator permission. Its matching
does not moderate content, determine whether a message is safe, or replace human
server moderation.

## User conduct

Do not use Shoe Bot or its commands to:

- Harass, threaten, deceive, impersonate, or target another person
- Spam, flood, disrupt, or intentionally overburden a server, Discord, the bot,
  or its hosting
- Exploit bugs, bypass safeguards, gain unauthorized access, or interfere with
  other servers' data
- Violate intellectual-property, privacy, publicity, or other rights
- Break Discord's rules, server rules, or the law
- Suggest that Shoe Bot or its profile image is officially connected to Barack
  Obama or any related person, office, foundation, or government organization

Access may be limited or removed to protect the service, Discord, users, or
other people, or when these terms are violated.

## Privacy and deletion

Use of the operated bot is subject to the [Privacy Policy](PRIVACY.md). Message
text and media are not persisted by Shoe Bot, but Discord delivers event data in
process memory and configured-channel text is briefly checked for the game.

- `/forgetme` deletes the caller's stored user ID and personal count in that
  server. Aggregate totals and the best streak remain. If that user is the last
  contributor in an active Relay, the active streak ends so their ID is no
  longer retained as Relay state. Its aggregate length may enter the Hall of
  Fame without an identity. The outcome is shown ephemerally to the requester;
  no public privacy-request notice is sent.
- The reset control inside `/shoesettings` allows a current server Administrator
  to delete all server game counts and records after a private confirmation
  while preserving the channel and modes.
- Removing the bot deletes that server's live Shoe Bot configuration and game
  data.

## Third-party services

Shoe Bot relies on Discord for accounts, servers, messages, interactions, and
reactions, and on Railway for execution and persistent storage. Discord and
Railway are independent services with their own terms, privacy policies,
security, availability, and enforcement decisions. These terms do not replace
their terms, and Shoe Bot does not control their services. Google receives only
support email a person chooses to send; the live bot does not send game or
Discord API data to Google. Do not include Discord IDs, message content, tokens,
or authentication information in support email.

## Source code and forks

The source code is available under the [MIT License](LICENSE). You are
encouraged to adapt it into a focused bot for another specific word or phrase.
The software license governs use of the code; these terms govern use of the
public Shoe Bot service.

A fork is a separate application operated by its own developer. Its operator
must use their own Discord application and credentials, clearly identify their
bot, request only necessary permissions and intents, secure API data, and
publish accurate terms and a public privacy policy describing the fork's actual
collection, use, sharing, retention, and deletion practices. Shoe Bot's name,
hosted service, support, privacy policy, and terms do not automatically cover or
endorse a fork. Before a provider processes Discord API data for a fork, its
operator must secure written service-provider terms satisfying Discord's
Developer Terms and execute any required DPA or addendum.

## No affiliation or endorsement

Shoe Bot is an independent project. It is not affiliated with, operated by, or
endorsed by Barack Obama, the Obama Foundation, the White House, or the United
States government. Barack Obama's image is used only as the bot's profile
picture and does not imply endorsement. See [NOTICE.md](NOTICE.md).

## Availability, statistics, and warranties

Shoe Bot is provided as available and without warranties. It may change, be
interrupted, contain errors, lose statistics, or be discontinued. Discord
delivery order, missing permissions, outages, API changes, hosting failures,
software errors, or administrator actions can affect the game.

To the extent permitted by law, the operator is not responsible for indirect or
consequential losses, lost data, server disputes, Discord enforcement actions,
third-party outages, or reliance on Shoe Bot statistics. Nothing in these terms
limits a right or liability that applicable law does not allow to be limited.

## Changes and contact

These terms may be updated as the bot, law, or relevant platform rules change.
The effective date at the top identifies the current version. Continued use
after an update means the current terms apply.

Questions and reports of safety issues, abuse, privacy concerns, security
incidents, or Discord-policy violations may be sent to
[yungcholesterol@gmail.com](mailto:yungcholesterol@gmail.com). Reports are
reviewed and appropriate action is taken when warranted.
