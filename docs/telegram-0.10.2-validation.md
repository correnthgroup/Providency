# Telegram responsiveness correction — 0.10.2

## Verified incident

The local Core Engine was healthy and getMe authenticated ProvidencyBOT using the
stored credential. Discovery took 16.02 seconds and returned no eligible messages.
The Streamlit HTTP client timed out at 10 seconds and incorrectly reported that the
engine was unavailable. The Telegram transport itself allowed 15 seconds per socket
operation. Credential-store access also ran synchronously on the engine event loop.

The selected Telegram group had two human members and no bot. A recent command was
written with a space before the bot mention. Adding the bot and configuring the
approver requires the operator's explicit confirmation; no browser peer is inferred
to be a Bot API identity.

## Correction and evidence

- Telegram UI requests allow 35 seconds; the integration has a 25-second overall
  deadline. Timeouts are reported as timeouts, not as an offline engine.
- Credential lookup and network I/O run together outside the event loop.
- The UI shows progress and an exact /start command after testing the bot.
- Two new regressions failed before the fix and passed afterward.
- Full suite: 159 tests passed. Ruff passed. Existing deprecation warnings remain.
- Live getMe using the corrected source authenticated ProvidencyBOT in 16.66 seconds;
  a concurrent event-loop heartbeat had a maximum interval of 0.12 seconds.
- Tests and probes did not send Telegram messages or execute financial actions.

The token was never added to source, reports or configuration files. Tokens visible
in the group's existing history were reported to the operator for rotation, without
reproducing them here. Canonical source/test fallback remains applicable because
Providency has no registered Graphify root.
