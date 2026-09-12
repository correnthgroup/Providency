# Guided browser onboarding

The launcher starts Streamlit before the engine. The loading page displays two bundled
GIFs and reads launcher-owned checkpoints from the local data directory. Progress
reaches 100% only after the engine health check succeeds. Startup cancellation and
the existing application shutdown remain explicit actions.

The default navigation is Início, Configurações, Atividades. Iniciar BOT
starts preparation only: browser pairing, Vector chart discovery and confirmation,
Telegram Web selected-chat discovery and destination confirmation. The last screen
does not start monitoring, execute orders or send messages. Advanced controls remain
under Configurações.

## Existing browser connection

Install `browser-extension` using Load unpacked in Brave, Chrome or Edge. Generate
a pairing code in Providency and enter that code and the displayed local port in
the extension popup. Codes are session-scoped and consumed on successful pairing;
they are not written to configuration files. The extension uses a loopback WebSocket
relay and Chrome debugger protocol. Python uses Playwright through that relay.

Only Vector Web and Telegram Web origins are exposed. There is no browser-history
scan, credential export or cookie copying. Disconnecting closes the automation
transport without closing the user's browser or tabs. Reopening Providency requires
pairing again. Firefox, Safari and Telegram Desktop are not supported by this connector.

Vector discovery reads every internal asset tab, including its own timeframe and
stable chart identifier. Telegram discovery reads only the selected-chat header and
peer reference. Confirmation identifies the intended destination; it does not prove
Bot API membership, group type or sending permission.

## Telegram Bot API setup

In Configurações, create a bot through the official BotFather and add it to the
intended group. Save its token using the password field: only the operating system
credential store receives the secret, under Providency / telegram-bot-token.
Testar bot calls getMe; success validates the credential, not message delivery.

The intended approver sends `/start@YourBot` in the destination group, then clicks
Identificar grupo e usuário in Providency. Discovery reads recent Bot API messages
without acknowledging update offsets. It only suggests explicit /start senders;
anonymous administrators and bots are excluded. The operator must confirm the
displayed chat and user. Browser peer identifiers are never treated as Bot API IDs.

Confirmation persists chat, approver, TTL and recheck tolerance in the local SQLite
database, applies them immediately, and enables polling for the next running session.
Configuration changes are blocked during an active session. Saved settings survive
restart; an explicit Settings.telegram_configuration override takes precedence in
embedded/test usage, otherwise persisted settings precede environment defaults.

No test message or trading proposal is sent during setup. Real delivery and approval
testing requires a created bot, its credential, group access and the chosen approver.

## Validation and remaining integration

The integration test loads the real extension in isolated Chromium and serves local
fixtures at the allowed origins. It verifies two internal charts with different
timeframes, all preparation transitions, Telegram destination confirmation and tab
preservation after disconnect. It does not establish compatibility with every live
Telegram Web layout. Live use requires the operator to install/pair the extension
and select their destination. No external messages are sent by these tests.

Windows packaging includes the extension folder and loading GIFs. macOS packaging
must be built and validated on macOS before distribution.
