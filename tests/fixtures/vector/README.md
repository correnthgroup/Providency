# Vector Web visual fixtures

`no_match_real_001.png` is an exact pixel crop of a user-provided Vector Web
screenshot. The crop contains only the chart, axes, public market prices and
timestamps. Account identifiers, balances, positions, orders and operating
system UI were excluded before the fixture entered Git.

The screenshot is manually labelled `NO_MATCH`: the final five detected
candles do not provide the strictly ascending three-close context required by
`bearish_engulfing`.

No sanitized real `MATCH` or `FORMING` observation was available on 2026-09-05.
Those two classes remain explicit calibration dataset gaps; synthetic fixtures
cover their deterministic geometry and predicate behavior.
