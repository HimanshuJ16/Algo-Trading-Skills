# Workflows for Cross-Vendor Timestamp Precision Reconciliation

1. **Vendor Parsing**:
   - Parse $T_{\text{raw}}$ per `precision_format` (`SECONDS`, `MILLISECONDS`,
     `MICROSECONDS`, `NANOSECONDS`, `ISO8601`).
   - `int` / `str` inputs are parsed exactly via `Decimal`; a `float` is read as
     `Decimal(str(x))` (the shortest round-tripping decimal) and logged as a lossy source.
   - A `float` declared `NANOSECONDS` raises: float64 is exact only to $2^{53}$, far
     below epoch nanoseconds, so the value is already corrupt on arrival.
   - Any residue below 1 ns is reported before it is discarded.
2. **Nanosecond Epoch Normalization**:
   - $t_{\text{ns}} = \text{Decimal}(T) \times \{10^9, 10^6, 10^3, 1\}$, rounded to an
     integer — never `int(T \times 10^9)` in float64, which is biased low by truncation.
   - ISO-8601: regex split into date/time, fractional digits, and offset; whole seconds
     via exact `timedelta` division, fraction added as an integer padded to 9 digits.
     `Z`, `z`, `+HH:MM` and `+HHMM` are accepted, `,` is accepted as the decimal
     separator, naive input is assumed UTC **with a warning**, and more than 9 fractional
     digits raise unless the surplus is zero padding.
   - Precision tier from fractional digit count: 0 gives `SECONDS`, up to 3 gives
     `MILLISECONDS`, up to 6 gives `MICROSECONDS`, up to 9 gives `NANOSECONDS`.
   - Reject any result outside the signed int64 nanosecond range.
   - Render `iso_utc_str` by integer `divmod`, with all 9 fractional digits.
3. **Temporal Sorting & OOO Interception**:
   - Walk the input in **arrival** order; flag and count any tick with
     $t_{\text{ns}} < \max(t_{\text{ns}})_{\text{seen}}$ ($\Delta t < 0$).
   - Reject duplicate `tick_id` values before ordering depends on them.
   - Sort output by $(t_{\text{ns}}, \text{arrival index})$ for a reproducible sequence.
4. **Precision Audit**:
   - Compare each tick's tier against `required_precision_tier`; flag shortfalls per
     record and count them. Choose the required tier from the applicable obligation
     (see `references/standards.md`), not from the storage schema.
5. **Matched-Event Skew Audit**:
   - Group by $(\text{symbol}, \text{event key})$; keep each vendor's earliest timestamp
     for that event; compute signed pairwise skew for vendors ordered lexicographically,
     so the sign identifies which vendor is ahead.
   - Warn only when $|\text{skew}|$ **exceeds** the threshold (equality does not warn).
   - With no event keys present, skip the analysis and report `skew_pairs_evaluated = 0`
     rather than comparing unrelated consecutive ticks.
6. **Audit Reporting**:
   - Emit `TimestampReconciliationReport`: normalized ticks, out-of-order count, tier
     distribution, precision violations, signed skew observations, and warnings.
