# Standards for Point-in-Time Index Constituent Tracking

## Membership Interval Convention

| Rule | Standard |
|---|---|
| PIT membership | `add_date <= T` AND (`del_date IS NULL` OR `del_date > T`) — the half-open interval `[add_date, del_date)`. |
| Addition effective on `T` | The name **is** a member for the whole session of `T`. |
| Deletion effective on `T` | The name is **not** a member on `T`. |
| Survivorship-bias elimination | Names removed after a bankruptcy, merger, or delisting MUST remain in the historical universe for every date they were members. |
| Rebalance effective date | Membership changes take effect on the index provider's official effective date, not the announcement date and not the date the vendor loaded the record. |

The half-open reading is not a stylistic choice. S&P Dow Jones Indices announced that
"Tesla Inc. (NASD:TSLA) will be added to the S&P 500 effective prior to the open of trading
on Monday, December 21" — a change effective *prior to the open* means the name trades in
the index for that entire session, so an addition dated `T` is a member on `T` and a
deletion dated `T` is not. This matches the interval convention in
`backtest-look-ahead-in-universe-selection`; the two skills are deliberately consistent.

### Vendor end-date hazard

Vendors differ on what the deletion date means:

- **Half-open** — `del_date` is the first day the name was *not* a member. This is what the
  engine expects.
- **Inclusive** — `del_date` is the *last* day of membership.

Feeding an inclusive end date into a half-open engine removes every name one session early,
across the whole universe, with no error. Add one session to inclusive end dates before
ingesting. Confirm which convention your feed uses; do not infer it.

## Date Representation

- Effective dates and as-of dates must be zero-padded ISO-8601 `YYYY-MM-DD` strings or
  `datetime.date` objects. Anything else raises `IndexConstituentError` at ingest.
- Lexicographic string comparison is date comparison **only** for zero-padded ISO-8601.
  `'2020-1-5'` sorts after `'2020-12-31'`, so a single non-padded record silently reorders
  the event log everywhere it appears.
- The engine models calendar dates, not sessions. It does not consult an exchange calendar,
  so an effective date falling on a market holiday is resolved arithmetically. Reconcile
  effective dates to trading sessions upstream if the distinction matters to your rebalance.

## Security Identity

Membership is keyed by `security_id` when supplied and by `symbol` otherwise. Ticker-only
keying is unsafe because exchanges reassign tickers across issuers: after the old General
Motors Corporation filed for bankruptcy in 2009 its shares traded as `GMGMQ` and then
`MTLQQ` (effective 2009-07-15), and the `GM` ticker was subsequently reassigned to the new
General Motors Company at its November 2010 IPO. Two issuers keyed on one ticker collapse
into a single membership timeline.

Supply a stable identifier — CUSIP, SEDOL, CRSP PERMNO, or FIGI. When two distinct
`security_id` values resolve as members simultaneously under the same ticker, both are
reported, which surfaces the overlap rather than hiding it.

## Same-Day Event Ordering

An addition and a deletion sharing one effective date for one security is a feed anomaly.
The engine resolves it deterministically rather than by ingest order:

1. If the feed supplies `sequence` on the events, that ordering wins.
2. Otherwise the deletion is applied first, so a same-day delete/re-add ends as a member.

Either way the ambiguity is reported in `PITIndexReport.data_quality_warnings`. Supplying
`sequence` for some but not all events on a date is itself reported.

## Report Status Values

| Status | Meaning |
|---|---|
| `UNIVERSE_RESOLVED_PIT` | Membership resolved. The universe may still legitimately be empty. |
| `INDEX_NOT_FOUND` | No events have ever been ingested for this index name. An empty result here is a configuration error, not an empty index. |
| `ENGINE_DISABLED` | `config.enabled` is `False`; nothing was resolved. |

`survivorship_bias_ghost_count` is `None` unless a `current_static_universe` was supplied.
`None` means **not audited** and must never be reported as zero ghosts.

## Scope Boundary

This engine resolves the effective (valid-time) membership axis. It does **not** model the
knowledge axis (announcement timing), settle delisting or merger terminal values, produce
point-in-time index weights, consult an exchange trading calendar, or verify that the event
log itself is genuinely point-in-time rather than reverse-engineered from current
membership. Those belong to the sibling skills listed in `SKILL.md`.

## On Quantifying the Bias

Published estimates of the return inflation caused by survivorship bias in equity backtests
vary widely — by index, rebalancing frequency, sample period, and which specific names were
removed — and the secondary sources that circulate single headline figures do not agree with
each other. No single constant is defensible here. Measure it on your own universe by
comparing the point-in-time result against the same strategy run on current membership, and
report the ghost count alongside it.

## Sources

- S&P Dow Jones Indices press release, "Tesla Set to Join S&P 500", 2020-11-16 —
  <https://press.spglobal.com/2020-11-16-Tesla-Set-to-Join-S-P-500>
  (establishes the "effective prior to the open of trading" convention and the
  announcement-to-effective gap).
- S&P Dow Jones Indices, "Equity Indices Policies & Practices" methodology —
  <https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-equity-indices-policies-practices.pdf>
- Motors Liquidation Company ticker history (old GM: `GMGMQ` → `MTLQQ`, effective
  2009-07-15) and the new General Motors Company November 2010 IPO under `GM` — the
  worked ticker-reuse example above.

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category
rolls up across the full skill library.
