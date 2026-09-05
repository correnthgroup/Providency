# Daily demo qualification

Providency 0.8 keeps daily evidence local and treats human review as an
append-only annotation. A review never changes the stored pattern result,
candidate decision, approval, order, or protection record.

## Canonical baseline session

Before a supervised demo session, record and verify:

1. the configuration version shown by the UI;
2. symbol, primary, context, and trailing timeframes;
3. traditional candlesticks and the expected Vector Web layout;
4. a positively identified demo account and no unexplained open exposure;
5. readable position and protection state before enabling observation;
6. the session identifier created by **RUN**.

Use **Session overview** to inspect the capture-to-close funnel and export its
deterministic JSON report. Missing data and ambiguous UI state remain blocking.

## Evidence and review

Create a sanitized copy before review. The sanitizer verifies the original
SHA-256, removes image metadata, converts to lossless WebP, applies any explicit
redaction rectangles, and removes sensitive metadata fields. Sanitized evidence
is stored under the local data directory, never uploaded automatically.

The default retention window is 30 days (`PROVIDENCY_EVIDENCE_RETENTION_DAYS`).
Expired sanitized files are moved to a local `.trash` directory so removal is
recoverable. Original operational captures continue to follow their existing
session policy.

Review labels mean:

- `TRUE_POSITIVE`: a confirmed visual pattern was correctly recognized;
- `FALSE_POSITIVE`: a confirmed recognition was visually incorrect;
- `FALSE_NEGATIVE`: a reviewed visual pattern should have been recognized;
- `TRUE_NEGATIVE`: the absence of a visual pattern was correct;
- `NO_DECISION`: evidence was insufficient or intentionally fail-closed;
- `OPERATIONAL_FAILURE`: capture, layout, connection, or state prevented review.

Profit and loss never determines these labels. Saving another review creates a
new linked revision; the latest revision drives metrics while history remains
available.

## Metrics and calibration gate

The UI reports TP/FP/FN/TN counts, numerator, denominator, review window,
detector version, and Pattern Package version. Precision is descriptive until
the confirmed-review denominator reaches 20. Recall is shown only when reviewed
false negatives provide a denominator.

The initial sanitized real corpus contains one hard `NO_MATCH` case. There was
no reviewed false signal supporting a causal detector change at release time,
so version 0.8 intentionally makes no recognition-rule adjustment and adds no
new Pattern Package. The next calibration lot must begin with a sanitized case
that fails before the fix and positive and negative regression coverage.

## Qualification matrix

Automated CI covers Windows and macOS for restart, disconnection, altered
layout, protected positions, and controlled emergency behavior through fake
adapters. A real demo session remains supervised and must record its exported
session report; credentials, balances, personal identifiers, and real-money
accounts are never acceptable test data.
