# Workflows for Tax Lot Accounting Methods

US federal securities lot matching. See `standards.md` for the sourced rules.

## 1. Decide the method — before the sale, not after

| Strategy | Ranking | Identification record required? |
|---|---|---|
| `FIFO` | oldest `acquisition_date_iso` first | No — this is the treatment absent an adequate identification |
| `LIFO` | newest `acquisition_date_iso` first | **Yes** |
| `HIFO` | highest `cost_basis_per_share` first | **Yes** |
| `SPECIFIC_LOT` | exactly `target_lot_ids`, in the order given | **Yes** |

LIFO and HIFO are not separate regulatory methods. They are standing
instructions for which particular shares to deliver — i.e. specific
identification — and inherit its timing requirement: the identification must
exist no later than the earlier of the settlement date or the Rule 15c6-1
settlement time (T+1 since 2024-05-28). Pass it as `identification_reference`;
the engine raises without one.

An unrecognised strategy raises. It must never fall back to FIFO — a typo
silently treated as FIFO changes which basis is consumed and the tax owed.

## 2. Validate the inventory before consuming anything

Reject, with no lots consumed:

- lots spanning more than one `symbol` (a sale of one security must never
  consume another's basis);
- duplicate `lot_id`s (a specific identification could not name one
  unambiguously);
- non-positive `quantity`, negative `cost_basis_per_share`, non-finite values;
- unparseable `acquisition_date_iso`;
- any lot acquired **after** `sale_date` — usually a backfill or timestamp bug,
  and undetectable without a sale date;
- a sale quantity exceeding available inventory. A shortfall means a missing
  acquisition, a lot recorded under the wrong symbol or account, or a
  double-counted sale. It must never be closed by inventing basis.

Parse dates into `date` objects. Never sort the raw strings:
`"2024-10-05" < "2024-9-01"` lexicographically, which puts October before
September and silently reverses FIFO and LIFO.

## 3. Plan, then commit

Build the whole match plan against copies of the lots first, and only decrement
quantities once the plan is known to be satisfiable. A failure therefore leaves
the caller's inventory untouched rather than half-consumed.

For `SPECIFIC_LOT` the candidate set is restricted to the designated lots —
undesignated lots are not candidates at all. If the designation does not cover
`sale_qty`, raise. Spilling into undesignated lots would deliver shares the
taxpayer never identified, and produce a basis figure that cannot be supported.
The caller's options are to designate more lots or to run the undesignated
remainder as a separate, explicit FIFO sale.

## 4. Realize per lot

For each matched lot:

$$\text{Proceeds}_{\text{lot}} = Q_{\text{matched}} \times P_{\text{sale}}$$

$$\text{Basis}_{\text{lot}} = Q_{\text{matched}} \times C_{\text{per share}}$$

$$\text{Gain/Loss}_{\text{lot}} = \text{Proceeds}_{\text{lot}} - \text{Basis}_{\text{lot}}$$

`cost_basis_per_share` is taken as given. It must already include acquisition
commissions and any prior wash-sale or corporate-action adjustment — this engine
computes neither.

`sale_price` of zero is permitted: a worthless or zero-proceeds disposition
realizes the full basis as a loss. Negative is not.

## 5. Classify by calendar anniversary

The holding period begins the **day after** acquisition and includes the day of
disposition. Long-term means held **more than one year**:

$$\text{term} = \begin{cases}
\text{LTCG} & \text{if } D_{\text{sale}} > \text{anniversary}(D_{\text{acq}}) \\
\text{STCG} & \text{otherwise}
\end{cases}$$

A sale **on** the one-year anniversary is exactly one year — short-term.

Do not test `days_held > 365`. Across a leap year 366 elapsed days can still be
exactly one year: bought 2024-01-01, sold 2025-01-01 is one year to the day, and
a day count reports it as long-term. Equally, do not store `holding_period_days`
on the lot — the holding period depends on the sale date, so a stored value is
stale for every later sale. Report elapsed days for the audit trail only.

February 29 has no anniversary in a common year; this module resolves it to
March 1, the later and more conservative boundary. No consulted source settles
that case.

## 6. Emit per-lot rows

Each match becomes one `RealizedLotMatch` carrying its own acquisition date,
sale date, elapsed days, proceeds, basis and term — because a single sale can
straddle Form 8949 Part I (short-term) and Part II (long-term). The report
exposes `is_mixed_term` and logs a warning when it is true.

Aggregates (`total_stcg_gain_loss_usd`, `total_ltcg_gain_loss_usd`) are derived
so that the split always reconciles to `total_realized_gain_loss_usd` exactly;
that total can differ from `total_sale_proceeds_usd - total_cost_basis_usd` by up
to a cent from float rounding. For filing-grade output, use `matched_lots` and
reconcile against the broker's Form 1099-B.

## 7. Carry the inventory forward

`remaining_open_lots` are copies, in the caller's original order, with fully
depleted lots dropped. Feed them back as the inventory for the next sale. The
engine is stateless and never mutates the lots it was passed.
