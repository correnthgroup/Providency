# Observation and Telegram validation — 0.11.1

Status: live Vector capture, analysis, Telegram delivery and Windows package smoke
test passed on 12 September 2026.

## Sources and routing

The applicable company authority, graph routing matrix, Providency specifications,
operational contracts, observation signals plan and pattern predicate contract were
consulted together with current source and tests. Providency has no registered
Graphify root: canonical source/test fallback was used, with no graph rebuild.

Applied capabilities: browser-automation-integration, domain-implementation,
api-contract-testing, test-automation, runtime-skill-routing, release-readiness and
long-running-verification-supervision. Temporary credential use was added after the
operator explicitly requested no persistence for the supplied testing credential.

## Changes

- Observation borrows the browser connection established during onboarding and its
  lock. Each internal asset tab is selected and verified before and after capture.
  Production start requires a connected, prepared browser.
- Vector's chart workspace uses `role="dialog"`; modal detection now excludes a
  dialog that contains the chart canvas. After switching an internal asset tab, the
  adapter waits for Vector's replacement canvas before applying fail-closed capture
  checks.
- The screenshot is saved before image analysis. Capture metadata and hashes are
  retained with each result. The catalog shows a saved, explicitly labeled didactic
  illustration and the latest available real capture for a matching pattern.
- Closed-bar patterns exclude the rightmost detected candle. A missing live chart
  clock prevents a positive closed-bar confirmation. Full live calibration remains
  part of the release gate.
- Telegram controls remain expanded. Saved credential status is separate from the
  intentionally empty password field. Malformed input cannot overwrite the cofre.
  HTTP and network failures have safe, actionable messages without token URLs.
- The temporary-token option holds the credential in process memory only, clears
  the UI field, returns only status flags and clears memory during shutdown.
- The Windows bundle defaults to observation-only mode.

## Evidence so far

- Full suite including the launcher-timeout and temporary-token regressions: 187 passed;
  Ruff and mypy passed.
  After refining network error wording for temporary credentials, all 18 Telegram
  transport tests passed again. Existing Starlette/anyio deprecation warnings remain.
- Packaging/API regressions after the version change: 5 passed.
- Temporary-token API regressions: 2 passed, covering no file persistence, no secret
  response, shutdown cleanup, invalid formats, external origins and active sessions.
- Telegram UI tests: 3 passed, including the temporary button never writing keyring.
- A real local UI getMe test authenticated `ProvidencyBOT` using the temporary option.
  The UI displayed the connected identity and an empty token field.
- Live destination discovery returned no recent eligible start message. The browser
  bridge was disconnected after the necessary engine restart. The operator was
  asked to reconnect and send a fresh start command in the intended group.
- At the end of the blocked live test, the temporary credential was cleared and the
  previous invalid cofre entry was deleted. Both the keyring read and safe API status
  confirmed absence. A focused scan of source, documentation, configuration, logs and
  database files in the repository and application data found no plaintext token for
  the tested bot. This does not remove the operator's original conversation message.
- Runtime routing transition on 12/09/2026: an unexpected packaged-launcher failure
  invalidated the previous Windows artifact evidence. Providency has no executable
  skill router and no registered Graphify root, so the canonical source/test fallback
  and the still-applicable `release-readiness` capability were used. The launcher had
  a 20-second unhandled process wait while observation cleanup may validly take 25
  seconds. The launcher now allows 35 seconds and contains `TimeoutExpired`, falling
  through to its bounded terminate/kill cleanup instead of exposing a PyInstaller
  traceback.
- A clean Windows candidate was built after the fix. In an isolated packaged-process
  smoke test, API and Streamlit both became healthy, the `--ui` role was terminated to
  reproduce the reported failure path, and the launcher exited with zero remaining
  packaged role processes and no unhandled exception dialog.
- Runtime routing transition on 12/09/2026: the packaged password field displayed a
  value while the Streamlit form submitted an empty string. This invalidated the prior
  real-UI credential-entry evidence, but not the Telegram API authentication evidence.
  The token input now lives outside `st.form`, validation failures preserve the value,
  and the field is cleared only after a successful keyring save or session activation.
  Focused Telegram/UI coverage passed with 24 tests, Ruff and mypy. A rebuilt packaged
  candidate was exercised with a synthetic invalid value: the backend received it,
  returned the format-specific error rather than the empty-value error, and retained
  the masked field for correction. The synthetic value was cleared before handoff.
- Live observation cycle `f5e96e69-001b-42c9-a66b-ab10b65a8c1a` identified BTC/BRL,
  ETH/BRL, LINK/BRL and AAVE/BRL at 15Min, saved four distinct WebP captures, verified
  their persisted SHA-256 hashes and analyzed every chart. The enabled bearish
  engulfing pattern returned `NO_MATCH` for all four, so the user-facing conclusion
  was “nenhum padrão identificado” for each chart.
- The corresponding observation outbox row reached `SENT` without error and recorded
  Telegram message id 8 for the confirmed group. The bot was stopped before the next
  45-minute interval.
- After the live test, the supplied credential was deleted from Windows Credential
  Manager. Both a direct keyring read and `/telegram/credential-status` confirmed that
  no Telegram token remained saved or active in memory.
- The final Windows artifact was rebuilt from the validated source. `dist` and
  `release/Providency` contain byte-identical 491,562,913-byte executables with SHA-256
  `2cc1e0afd94069580f5fc723c1cd93a978edb403fa13849ab80b2409dcd76b1a`. A packaged
  smoke test confirmed healthy API and Streamlit endpoints, absence of a persisted
  Telegram credential and clean shutdown with both local ports released.

The Windows release gate is satisfied. The supplied credential is not part of this
report, the repository or the local credential store.
