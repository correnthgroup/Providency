# Guided browser onboarding

The launcher starts Streamlit before the engine. The loading page displays two bundled
GIFs and reads launcher-owned checkpoints from the local data directory. Progress
reaches 100% only after the engine health check succeeds. Startup cancellation and
the existing application shutdown remain explicit actions.

The default navigation is Início, Conexões, Configurações, Atividades. Iniciar BOT
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
Bot API membership, group type or sending permission. Those remain a subsequent step.

## Validation and remaining integration

The integration test loads the real extension in isolated Chromium and serves local
fixtures at the allowed origins. It verifies two internal charts with different
timeframes, all preparation transitions, Telegram destination confirmation and tab
preservation after disconnect. It does not establish compatibility with every live
Telegram Web layout. Live use requires the operator to install/pair the extension
and select their destination. No external messages are sent by these tests.

Windows packaging includes the extension folder and loading GIFs. macOS packaging
must be built and validated on macOS before distribution.
