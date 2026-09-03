# Standards — strategy-decommissioning-and-position-unwind-procedure

> Nothing here is legal or compliance advice. Applicability depends on your entity type,
> licence and venue. See `mappings/regulatory-coverage.md` for the repository-wide index.

## 1. What is actually mandated

The obligations below are real and quoted from primary sources. None of them prescribes a
liquidation schedule, a participation rate, or an unwind horizon.

| Source | Provision | Verbatim requirement | Scope |
|---|---|---|---|
| MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589, Art. 12(1) | Kill functionality | "An investment firm shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected ('kill functionality')." | EU/EEA investment firms engaged in algorithmic trading. Art. 12(3) additionally requires the firm to identify which algorithm, trader, desk or client is responsible for each order sent. |
| RTS 6, Art. 16(5) | Real-time monitoring | "An investment firm shall have a process in place to take remedial action as soon as possible after an alert has been generated, including, where necessary, an orderly withdrawal from the market." | Same. This is where "orderly withdrawal" first appears; the five-second alert bound in the same paragraph applies to alert generation, not to the unwind. |
| RTS 6, Art. 17(1) | Post-trade controls | "Where a post-trade control is triggered, the investment firm shall undertake appropriate action, which may include adjusting or shutting down the relevant trading algorithm or trading system or an orderly withdrawal from the market." | Same. Note "**may** include" — the article authorises an orderly withdrawal as a remedy; it does not mandate one, and it defines no method. |
| RTS 6, Art. 17(3) | Reconciliation | The firm "shall keep records of trade and account information, which are complete, accurate and consistent" and "shall reconcile its own electronic trading logs with information about its outstanding orders and risk exposures as provided by the trading venues to which it sends orders, by its brokers or DEA providers, by its clearing members". | Same. This is the obligation the engine's reconciliation-break ledger supports — and the reason its inventory must be reconciled against the broker, not trusted on its own. |
| SEC Rule 22e-4 (17 CFR 270.22e-4) | Liquidity classification | Highly liquid: convertible to cash "in three business days or less"; moderately liquid: "more than three calendar days but in seven calendar days or less"; illiquid: cannot be sold "in seven calendar days or less … without the sale or disposition significantly changing the market value". Para. (b)(1)(ii)(B) requires the fund to consider whether "trading varying portions of a position … in sizes that the fund would reasonably anticipate trading" affects liquidity. No fund may acquire an illiquid investment if it would then hold more than 15% of net assets in illiquid investments. | **US registered open-end funds and In-Kind ETFs only.** Not a proprietary trading firm's rule. Cited here because it is the one regulated framework that formalises "how many days would it take to get out at a size I would actually trade", which is the question a participation cap answers. |

Source texts: RTS 6 as published by the European Commission
([160719-rts-6_en.pdf](https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160719-rts-6_en.pdf),
consolidated text on [EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng));
Rule 22e-4 as codified at [17 CFR 270.22e-4](https://www.law.cornell.edu/cfr/text/17/270.22e-4).

## 2. What is NOT mandated

| Common claim | Status |
|---|---|
| "Liquidation slices must not exceed 10% of ADV." | **Not a regulatory requirement.** A full-text search of RTS 6 returns no reference to average daily volume or any liquidation percentage. 10% is a widely used practitioner participation ceiling and this library's default; it is house policy and must be calibrated per instrument and recorded as such. |
| "Capital must be returned to treasury only after 100% liquidation." | **Not a regulation.** It is a sound internal control — releasing capital against an open book overstates available capital and orphans the residual position — and this engine enforces it, but no regulator states it. |
| "Entry signals must be hard-blocked on decommissioning." | **Not stated as such.** The nearest binding obligations are RTS 6 Art. 12 (cancel unexecuted orders) and Art. 15 (pre-trade limits blocking or cancelling breaching orders). Blocking new entries is the firm's own control. |
| "A slower unwind is the compliant unwind." | **No.** RTS 6 asks for an *orderly* withdrawal, not a slow one. Extending the unwind holds the retired strategy's market risk longer — the impact/timing-risk trade-off formalised as the efficient frontier in Almgren & Chriss, "Optimal execution of portfolio transactions", *Journal of Risk* **3**(2), Winter 2000, pp. 5–39. |

## 3. Library defaults (calibrate before use)

| Parameter | Default | What it does |
|---|---|---|
| `max_adv_slice_pct` | `10.0` | Participation ceiling per wave, as a percentage of the symbol's ADV. Bounded to `(0, 100]`. House policy — see above. |
| `lot_size` | `1.0` | Minimum tradable increment. Non-final waves are floored to a whole multiple; the final wave ships the odd-lot residual. |
| `QUANTITY_EPSILON` | `1e-6` | Float-residue tolerance for "flat". Not a residual-position allowance — share and contract quantities are orders of magnitude coarser. |

The engine holds no default for ADV, price, or reason: each must be supplied, because a
fabricated default in an audit trail is worse than a missing one.

## 4. Engine invariants (enforced in code, tested)

| Invariant | Enforcement |
|---|---|
| New entries are permitted only in `ACTIVE`. | `new_entries_allowed` is derived from state; `assert_entry_allowed()` raises `EntryBlockedError` otherwise. |
| Decommissioning cannot be restarted. | `initiate_decommissioning()` raises outside `ACTIVE`, preserving the original audit reason. |
| A symbol has at most one authorised, unfilled wave at a time. | Symbols with an open slice are skipped by the next generation call; `cancel_slice()` releases them. |
| No wave exceeds the configured participation cap. | Non-final waves are `floor(cap / lot_size) × lot_size`; a cap below one lot yields no wave and is reported in `unsliceable_symbols`. |
| An overfill is recorded, never clamped. | The position flips sign and a `ReconciliationBreak` is raised; treasury return is blocked until acknowledged. |
| A repeated `execution_id` is applied once. | Suppressed with an info log. Absent an `execution_id`, the engine warns that suppression is disabled. |
| `FULLY_UNWOUND` requires a flat book, no open slice, and no unconfirmed working-order cancellation. | `_refresh_terminal_state()`, called after every fill and cancellation confirmation. |
| Liquidated notional reflects executions, never authorisations. | Accumulated in `record_slice_execution()`; `initial_total_notional_usd` is marked once at load. |

## 5. Known limitations

- The engine's inventory is whatever was loaded. It detects overfills against its own book,
  not against the broker's — independent reconciliation remains mandatory (RTS 6 Art. 17(3)).
- Single-currency notional. Multi-currency books must be normalised before loading; see
  `multi-currency-pnl-and-fx-conversion`.
- No expiry, assignment, settlement or corporate-action awareness. Derivatives with delivery
  or early-assignment mechanics need those handled before an unwind is scheduled.
- No hedge or leg awareness: unwinding a spread symbol-by-symbol will leave a naked leg.
- ADV is a static input. A liquidity collapse during the unwind will not shrink the cap on
  its own; re-load the position with a current ADV.
