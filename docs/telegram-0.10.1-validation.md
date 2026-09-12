# Telegram setup correction — 0.10.1

## Cause

The browser onboarding confirmed an open Telegram conversation, while the approval
service only read chat/user IDs from environment configuration. Those IDs were zero
in the running application. The operator also confirmed that a BotFather bot had
not yet been created. A browser conversation alone is not a configured Bot API bot.

## Change

The settings page now guides bot creation, stores the token in the OS credential
store, tests getMe, discovers explicit /start senders and asks the operator to
confirm the chat and approver. Non-secret configuration persists in SQLite and
updates the approval service and polling without restarting. Active sessions block
identity changes. The browser peer is informational, never an inferred Bot API ID.

## Validation

- Regression initially failed because the configuration endpoint did not exist.
- Full suite: 157 passed; two existing third-party deprecation warnings.
- Ruff, mypy (30 source files), and git diff --check passed.
- UI integration verifies token clearing, credential-store-only persistence,
  discovery, explicit confirmation and configuration without environment variables.
- Restart persistence, invalid IDs, external origins, session guards and delayed
  polling activation are covered. Existing approval/dry-run tests remain passing.
- Automated Telegram traffic uses fixtures. No real messages or orders were sent.
- Windows executable built successfully and installed at the existing release path,
  preserving a 0.10.0 executable backup. On 2026-09-10 the packaged application
  started successfully: /health returned ok and OpenAPI/UI reported 0.10.1.

## Sources and limits

Read the referenced Providency tasks as historical context; verified the diagnosis
against the running local API, source and tests. Applied the workspace authority,
Providency specifications and operational contracts, plus domain-implementation and
test-automation skills. No Graphify root is registered for Providency: used the
canonical source/test fallback. No project skill router was found in this Git root;
packaging follows the existing build script and product packaging specification.

Real delivery and approval remain dependent on the operator saving the newly
created bot token, adding the bot to the group and confirming an approver. This
increment does not enable financial execution or implement continuous monitoring.
