# Standards — multi-account-same-strategy-fan-out

## Library configuration (defaults, not industry standards)

No regulator or exchange publishes a mandatory apportionment algorithm, minimum
allocation size, or client-order-ID format. The values below are this library's
defaults; calibrate each against your broker's constraints and your firm's allocation
policy, and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| Apportionment method | Largest-remainder (Hamilton) | Floors each exact entitlement, then hands the stranded shares to the largest fractional remainders. Guarantees $\sum_i Q_i = Q_{\text{master}}$ exactly. Computed in exact rational arithmetic (`fractions.Fraction`) over the true values of the float bases — flooring in floating point misfloors entitlements that are mathematically integers. |
| Allocation basis | `PRO_RATA_NAV` | $b_i = \text{NAV}_i$. Use `EXPLICIT_WEIGHT` ($b_i$ = held quantity) to unwind a position. |
| Remainder tie-break | `account_id` ascending | Deterministic so an auditor can re-derive the split from the recorded bases. Fair within a batch, **not** rotated across batches — see the fairness note below. |
| `min_order_qty` | 1 | Accounts entitled to a non-zero quantity below the floor are **excluded** and their shares re-apportioned. The floor never raises an allocation. |
| Client order ID format | `{prefix}_{batch_id}_{account_id}` | Deterministic per `(batch_id, account_id)`, so replaying a batch after an ambiguous timeout reproduces identical IDs. `batch_id` may not contain `_`, which keeps the three fields unambiguously parseable. Venue-specific `ClOrdID` length limits are **not** enforced here — verify yours. |
| Dispatch | Out of scope | The module returns instructions. Submission, retry classification, and fill tracking belong to the broker adapter. |

## Why not `round()` per account

`round(Q × w_i)` computed independently per account has no summation guarantee, and
Python's `round()` is round-half-to-even (IEEE 754 default), so `round(2.5) == 2`
while `round(3.5) == 4`. Both effects are observable:

| Case | Independent `round()` | Largest remainder |
|---|---|---|
| 3 equal accounts, 10 shares | 3 + 3 + 3 = **9** (1 share never traded) | 4 + 3 + 3 = **10** |
| 7 equal accounts, 10 shares | 1 × 7 = **7** (30% of the signal dropped) | 2+2+2+1+1+1+1 = **10** |
| 4 equal accounts, 6 shares | 2 × 4 = **8** (33% over-execution) | 2+2+1+1 = **6** |
| 2 equal accounts, 5 shares | 2 + 2 = **4** | 3 + 2 = **5** |

## Regulatory touchpoints (verified)

> Not legal or compliance advice. Applicability depends on entity type, registration
> status, jurisdiction, and product. Confirm with qualified counsel.

### US futures — bunched orders and post-execution allocation

Source: **17 CFR § 1.35(b)(5)**, "Records of commodity interest and related cash or
forward transactions" (CFTC)
([eCFR text via Cornell LII](https://www.law.cornell.edu/cfr/text/17/1.35)).

| Requirement | Location | Verbatim / verified wording |
|---|---|---|
| Who may place a bunched order for post-execution allocation | (b)(5)(i)–(ii) | An account manager granted **written investment discretion** over the participating customer accounts; the listed eligible categories include a CFTC-registered commodity trading advisor and a CFTC-registered futures commission merchant. |
| Fairness standard | (b)(5)(iv)(B) | "Allocations must be fair and equitable. No account or group of accounts may receive consistently favorable or unfavorable treatment." |
| Timing | (b)(5)(iv)(A) | Allocations "must be made as soon as practicable after the entire transaction is executed"; for cleared trades, allocation information must reach the FCM "no later than a time sufficiently before the end of the day the order is executed to ensure that clearing records identify the ultimate customer for each trade." |
| Objectivity / auditability | (b)(5)(iv)(C) | The allocation methodology must be objective enough to "permit independent verification of the fairness of the allocations" by regulators and auditors. |
| Recordkeeping | (b)(5)(v) | Records must permit reconstruction "from the time of placement by the account manager to the allocation to individual accounts." |
| Customer disclosure | (b)(5)(iii) | The general nature of the allocation methodology; whether accounts in which the manager has an interest may be bunched with customer accounts; and summary or composite data letting a customer compare its results with comparable customers. |

Implementation impact: the deterministic tie-break and the per-order
`allocation_basis` / `allocation_weight` / `exact_quantity` /
`received_remainder_share` fields exist to satisfy (b)(5)(iv)(C) and (b)(5)(v). The
timing and disclosure obligations are **operational** and are not enforced by this
library.

### US futures — average pricing over a bunched order

Source: **CME Rule 553, Average Price System**, CME/CBOT/NYMEX Rulebook Chapter 5
([CBOT Chapter 5](https://www.cmegroup.com/rulebook/CBOT/I/5.pdf),
[CME Chapter 5](https://www.cmegroup.com/rulebook/CME/I/5/5.pdf)); mechanics in the
[CME Clearing Average Pricing Algorithm](https://www.cmegroup.com/clearing/files/cme-clearing-average-pricing-algorithm.pdf).

Verified points: a clearing member may average multiple execution prices across an
order or series of orders executed the same trading day for the same account or group
of accounts and the same product/expiry (and, for options, the same put/call and
strike). The weighted average is contracts × price summed and divided by total
contracts; the average is rounded to a tick — **up for a buy group, down for a sell
group** — and the residual created by rounding **must be paid to the customer**.
Final account-specific allocations must be submitted to the Exchange clearing system
**no later than the end of each trading day**.

Implementation impact: this is the mechanism that equalizes fill prices across client
accounts. It operates on a *bunched* order at the clearing layer and is therefore
outside this module, which produces separate per-account orders. Documented in
`SKILL.md` under **When NOT to Use** so the two patterns are not confused.

### US securities — investment adviser allocation policy

Sources: **Investment Advisers Act of 1940 § 206** (antifraud) and **Rule 206(4)-7**,
adopted in Release Nos. **IA-2204 / IC-26299** (File No. S7-03-03), "Compliance
Programs of Investment Companies and Investment Advisers," adopted 17 December 2003,
effective 5 February 2004
([SEC final rule](https://www.sec.gov/files/rules/final/ia-2204.htm)).

Rule 206(4)-7 itself does **not** prescribe an allocation method — it requires written
policies and procedures reasonably designed to prevent violation of the Act. The
adopting release identifies trading practices, "including procedures by which the
adviser satisfies its best execution obligation, uses client brokerage to obtain
research and other services … and allocates aggregated trades among clients," as an
area those policies should address. Preferential allocation ("cherry-picking") is
charged under § 206 antifraud together with Rule 206(4)-7.

Implementation impact: the objective, reproducible apportionment and the retained
allocation basis are what make a written allocation policy testable. No threshold or
method is mandated by rule, so none is hard-coded here.

### Broker allocation surfaces

Source: **IBKR TWS API — Financial Advisors / Placing Orders to an FA account**
([financial_advisor.html](https://interactivebrokers.github.io/tws-api/financial_advisor.html),
[financial_advisor_methods_and_orders.html](https://interactivebrokers.github.io/tws-api/financial_advisor_methods_and_orders.html)).

Verified: IBKR performs the allocation **server-side** from a single order. Group
methods are `EqualQuantity`, `NetLiq` (ratios from each account's net liquidation
value), `AvailableEquity` (ratios from available equity) and `PctChange`; the first
three take `Order.FaGroup` + `Order.FaMethod`, while `PctChange` takes
`Order.FaPercentage` and **no order size**. Profile-style allocations (Percentages,
Ratios, absolute Shares) existed separately; from TWS/IBGW build 983+ the
"Use Account Groups with Allocation Methods" setting unifies groups and profiles, and
profile names are passed as `FaGroup`. IBKR's own docs do not publish the rounding
rule or residual handling for `NetLiq`/`AvailableEquity`, so per-account quantities
from a broker-side allocation must be **read back from the executions**, not predicted.

Implementation impact: recorded in `SKILL.md` under **When NOT to Use** — on an FA
account, prefer one order plus `faGroup`/`faMethod` over N client-side orders.

## Known limitations of this module

- **Fair within a batch, not across batches.** The `account_id` tie-break sends the
  leftover share to the same accounts whenever NAVs are stable. That is a
  1.35(b)(5)(iv)(B) exposure. `received_remainder_share` is recorded so the skew is
  auditable; no rotation policy is imposed, because the right one is a firm-level
  compliance decision rather than a library default.
- **No price equalization.** Separate per-account orders receive separate fills.
- **Position-blind.** NAV sizes entries only; exits must use `EXPLICIT_WEIGHT` with
  held quantities.
- **No pre-trade risk checks.** Buying power, margin, borrow, and restricted lists are
  out of scope and must sit upstream.
- **No dispatch, no fill tracking.** The module returns instructions only.
- **NAV is a `float` snapshot.** Only ratios are used and the apportionment itself is
  exact integer arithmetic, so float NAVs do not accumulate allocation error — but the
  snapshot must be taken once per batch, not read live mid-apportionment.

## Category

`broker-integration` — see top-level `mappings/` directory.
