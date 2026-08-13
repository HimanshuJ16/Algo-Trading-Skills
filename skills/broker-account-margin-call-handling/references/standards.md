# Broker & Framework Coverage — broker-account-margin-call-handling

## How to read this document

Broker policies below are quoted from each broker's own published documentation and
were correct as at the sources linked. **They change, and they differ by account type,
jurisdiction and product.** Verify against your own broker's current terms before
encoding any number here into a live risk control. Nothing in this skill's default
thresholds (85% / 95% / 100%) comes from a regulator or a broker — they are house
policy you choose.

## 1. Broker triggers

| Broker / Margin System | Margin warning trigger | Forced liquidation trigger | Pre-trade margin impact |
|---|---|---|---|
| **Interactive Brokers** (Reg T / Portfolio Margin) | Margin warning when **Equity with Loan Value ≤ Maintenance Margin Requirement × 110%** — a 10% cushion above the *requirement*, not above NLV | **IBKR does not make margin calls.** Real-time liquidation occurs when the account has a margin deficiency (Excess Liquidity < 0), without prior notice and without letting you choose the positions, timing or order | `Order.whatIf = true` passed to `placeOrder`; the `openOrder` callback returns an `OrderState` carrying `initMarginChange`, `maintMarginChange` and `equityWithLoanChange` |
| **Zerodha** (India) | Margin shortfall reported against exchange-mandated margins; exchange **penalty of 0.5%** of the shortfall below ₹1 lakh and **1%** at or above it | Policy is **MTM-loss and time based, not margin-utilisation based**: positions may be squared off when MTM losses exceed ~50% of account funds; MTF positions at ~20% of the funded amount; and **all intraday (MIS) positions are auto squared off around 15:20 IST** regardless of margin health | Margin calculator / basket margin API |
| **Alpaca** | Equity approaching maintenance margin requirement | Equity below maintenance margin requirement; separate day-trade (PDT) margin call rules | Pre-trade estimation from account buying power |
| **CME SPAN** | A *margin methodology*, not a broker policy — SPAN produces the requirement; the warning and liquidation policy is set by your clearing FCM | Intraday variation/initial margin calls issued by the FCM | SPAN risk arrays evaluated against the portfolio |

**Sources:** IBKR Campus glossary (Margin Warning, Excess Liquidity, Current Available
Funds); [IBKR TWS API — Checking Margin Changes](https://interactivebrokers.github.io/tws-api/margin.html);
IBKR *Disclosure of Risks of Margin Trading*; Zerodha support articles on RMS square-off
and [margin penalties](https://zerodha.com/z-connect/general/margins-margin-penalties-when-trading-with-leverage).

> **Correction of a common error:** `reqSecDefOptParams` is *not* a margin call. It
> returns option chain metadata — trading class, exchange, expiries and strikes. The
> pre-trade margin mechanism at IBKR is the `whatIf` order above.

## 2. The two cushions, and why they disagree

Interactive Brokers defines:

- **Available Funds** = Equity with Loan Value − **Initial** Margin
- **Excess Liquidity** = Equity with Loan Value − **Maintenance** Margin

Both are computed from **Equity with Loan Value (ELV)**, not from Net Liquidation Value.
ELV excludes value that does not count toward margin equity, so ELV ≤ NLV, sometimes
materially.

This is the trap the engine exists to close. An account can show
`maintenance_margin / NLV = 0.70` — comfortably inside any house "NORMAL" band — while
Excess Liquidity is already negative and the broker is liquidating. The house ratio is a
useful *early warning* because it moves before the breach; it is not authoritative about
whether a breach has occurred. **`excess_liquidity < 0` is the condition that matters**,
and `evaluate_margin_health` escalates on it regardless of the ratio.

For calibration: IBKR's warning fires at ELV ≤ 1.1 × maintenance margin, i.e. a
maintenance/ELV ratio of about **90.9%** — which sits between this skill's default 85%
warning and 95% critical tiers.

## 3. Regulatory reference points (US)

Jurisdiction: United States. These bind the **broker-dealer**; they constrain what your
account will be permitted to do, and they do not apply to non-US venues or to
non-securities margin.

- **Federal Reserve Regulation T** (12 CFR 220) sets *initial* margin: a broker may lend
  up to **50%** of the purchase price of a margin equity security, so 50% must be
  deposited. Short sales of non-exempted securities require **150%** of current market
  value (the 100% proceeds plus 50%).
- **FINRA Rule 4210** sets *maintenance* margin: at least **25%** of current market value
  for long positions, with higher requirements for shorts and for leveraged ETPs (the
  rule text gives 2× the base figures for 2× leveraged products — 50% long, 60% short).
  Rule 4210 also sets initial requirements where Reg T is silent.
- **Portfolio margin** (FINRA Rule 4210(g)) replaces fixed percentages with a stress test
  of the portfolio's maximum theoretical loss across a defined range. The range is **not**
  a single number: roughly −8%/+6% for high-capitalisation broad-based indices, ±10% for
  other broad-based indices, and **±15% for sector indices and individual equities**.

Consequence for this skill: a **pre-trade** check must be made against **initial** margin,
not maintenance margin. At Reg T levels the initial requirement is double the maintenance
minimum, so an order can pass a maintenance-margin projection and still be refused, or be
accepted and leave the account with far less room than the projection implied. That is why
`guard_new_order` accepts an `initial_margin_impact` and tests it against `available_funds`.

**Sources:** [FINRA Rule 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210),
[FINRA Margin Regulation](https://www.finra.org/rules-guidance/key-topics/margin-accounts),
[FINRA Portfolio Margin FAQ](https://www.finra.org/rules-guidance/key-topics/portfolio-margin/faq).

## 4. Operational notes

- **Margin is not separable per position under Portfolio Margin or SPAN.** Both compute the
  requirement on the portfolio's stressed loss, so closing one leg of a hedge can *increase*
  total margin. `plan_deleveraging` uses a linear per-position model; under these regimes
  treat its output as a candidate ordering and re-price each slice through the broker
  (an IBKR `whatIf` order, or a SPAN evaluation) before sending it.
- **Liquidity spiral risk.** Liquidating a large illiquid position at market can collapse
  its price, lowering NLV and creating a secondary deficiency. Hence the ADV participation
  cap. The cap also means a plan may not clear the deficit in a single pass — that is the
  intended trade-off, not a bug.
- **Tail risk.** Unhedged short options carry the largest adverse convexity and are
  prioritised for liquidation regardless of PnL. Note this is a *risk* ordering: it will
  often mean realising losses on the worst positions first.
- **Time-based square-off.** In markets where the broker force-closes intraday products on
  a schedule (Zerodha's ~15:20 IST MIS square-off), margin health is not the only trigger.
  A bot that is margin-healthy at 15:19 will still be flattened. Model the clock as well as
  the ratio.
- **You may not get a window.** Where the broker liquidates in real time without notice, the
  BREACH tier is not a state you plan to act from — it is a state you plan never to reach.
  The pre-breach tiers carry the actionable decisions.
