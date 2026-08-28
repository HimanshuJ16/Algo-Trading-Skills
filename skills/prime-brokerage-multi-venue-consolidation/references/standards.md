# Prime Brokerage Consolidation Standards — prime-brokerage-multi-venue-consolidation

## Consolidation definitions

Quantities are unsigned on input; direction is carried by `side` and the sign is
derived. $i$ indexes fills within one instrument key, $m$ is the contract multiplier
(constant per key), $P_i$ the fill price and $Q_i$ the fill quantity.

| Metric | Calculation | Description |
|---|---|---|
| Instrument Key | $(\text{symbol}, \text{currency})$, single $m$ | The netting unit. Same ticker in another currency, or under another multiplier, is another instrument |
| Signed Quantity | $+Q_i$ for BUY, $-Q_i$ for SELL | Never inferred from an unrecognised side value |
| Net Quantity | $Q_{\text{net}} = \sum_i \text{signed}(Q_i)$ | What remains to be financed after consolidation |
| Gross Quantity | $Q_{\text{gross}} = \sum_i Q_i$ | What actually crossed the tape; what the fee legs price off |
| Fill Notional | $N_i = Q_i \cdot P_i \cdot m$ | In the fill's own currency. Never FX-converted here |
| Gross Notional | $N_{\text{gross}} = \sum_i N_i$ | Per instrument and per currency |
| VWAP | $\bar{P} = N_{\text{gross}} / (Q_{\text{gross}} \cdot m)$ | Per unit of underlying, not per contract |
| Residual Notional | $\|Q_{\text{net}}\| \cdot \bar{P} \cdot m$ | The exposure left after internal offsetting, valued at the batch's own VWAP |
| Instrument Offset Ratio | $(1 - \|Q_{\text{net}}\| / Q_{\text{gross}}) \cdot 100$ | Quantity-based, valid **only within one instrument** |
| Portfolio Offset (per currency) | $(1 - \sum_s \text{residual}_s / \sum_s N_{\text{gross},s}) \cdot 100$ | Notional-weighted. Comparable across instruments; still **not** a margin figure |
| PB Clearing Fee | $\sum_i Q_i \cdot f$, charged in `fee_currency` | Independent of the currency the fill is priced in |
| Executing Broker Commission | $\sum_i c_i$, bucketed by fill currency | The second fee leg a give-up incurs |

**Dimensional rule.** A quantity ratio may only be formed within a single instrument
key. Summing share and contract counts across instruments and calling the ratio a
capital or margin figure is invalid: the units are not commensurable, and the result
is dominated by whichever instrument happens to trade in the largest unit count.

## Engineering standards

| Standard | Requirement |
|---|---|
| Give-Up Idempotency | `execution_id` MUST be unique per fill. A repeat within a batch always raises; across batches it raises while `enforce_cross_batch_idempotency` is on. Registration is atomic — a rejected batch registers nothing |
| Side Strictness | `side` MUST be exactly `BUY` or `SELL` (case-insensitive). Unrecognised values raise; they are never coerced to a sell |
| Currency Isolation | Monetary totals MUST stay in the currency they were traded in. No FX conversion, no cross-currency sum |
| Instrument Homogeneity | One symbol MUST NOT be netted across currencies or across contract multipliers |
| Cut-Off Configuration | Submission deadlines MUST be supplied per trade date and MUST be timezone-aware. A partially configured check raises rather than reporting "on time". The boundary is inclusive: a submission stamped exactly at the cut-off is on time, and only a strictly later one is flagged |
| Margin Claims | This module MUST NOT emit a margin or capital-savings figure. Margin relief is the PB's margin model output, not a function of traded quantities |

## Regulatory and market-infrastructure anchors

Jurisdiction is stated on every row. None of these are defaults inside the code; they
are the sources an operator uses to configure cut-offs and expectations.

| Fact | Jurisdiction / Source | Bearing on this skill |
|---|---|---|
| Allocation, confirmation and affirmation must be completed "as soon as technologically practicable and no later than the end of the day on trade date" for institutional transactions | US — SEC Rule 15c6-2, 17 CFR 240.15c6-2 (compliance date 28 May 2024) | The upper bound on how late trade-date give-up/affirmation processing may run for US cash equities |
| Standard settlement is T+1 | US — SEC Rule 15c6-1(a), 17 CFR 240.15c6-1 (effective 28 May 2024) | Compressed the whole post-trade timeline; any cut-off constant predating this is stale |
| DTC affirmation cut-off is 9:00 p.m. ET on trade date | US — DTCC Institutional Trade Processing | A concrete, operationally used cut-off value to configure `giveup_cutoffs` with for US equities |
| Prime brokerage accounts are treated as broker-dealer credit accounts under Regulation T Section 220.11; minimum net equity of $500,000, or $100,000 where the account is managed by a registered investment adviser, restorable within five business days | US — SEC Division of Market Regulation no-action letter to the Prime Broker Committee, 25 January 1994 | The framework the whole give-up-to-PB arrangement operates under; the PB must monitor net equity and may cease acting |
| The PB confirms via DTC ID by 12:00 noon ET on T+1 and may disaffirm ("DK") by 3:00 p.m. ET on T+1 for trades affirmed by 9:00 a.m. that day, otherwise by close of business on T+1; a disaffirmed trade remains a customer trade on the **executing** broker's books | US — SIFMA Prime Brokerage Agreement, Form 150 (implementing the 1994 no-action letter) | Transmission of a give-up is not acceptance. The payload must be reconciled against the PB's claimed list. Note this document's timeline predates T+1 and is read together with Rule 15c6-2 |
| Portfolio margin, as an alternative to strategy-based margin, is available in approved accounts under prescribed eligibility criteria | US — FINRA Rule 4210(g); strategy-based margin under 12 CFR 220 (Regulation T) | Why a "margin savings %" cannot be derived from fills: the applicable methodology and eligibility determine it |
| Cross-margin arrangements between clearing organizations combine eligible hedged positions into one portfolio for margining; participation carries account and clearing-member conditions | US — OCC cross-margin programs; CME–FICC cross-margining arrangement | Offsets require eligibility and a single margining account, not merely consolidation of execution flow |
| A give-up occurs when one broker executes and another clears; the relationship is governed by the FIA International Uniform Give-Up Agreement, executed electronically through FIA Tech EGUS | Global listed futures — FIA / FIA Tech | The futures give-up path. Allocation and claim deadlines come from the clearing house rulebook, not the equity affirmation timeline |

## Scope boundary

No FX conversion, no margin model, no broker connectivity, no position state beyond
the idempotency ledger. Base-currency valuation belongs to
`multi-broker-consolidated-position-view` and `multi-currency-pnl-and-fx-conversion`;
margin and cross-margin eligibility to `cross-margining-across-asset-classes` and
`broker-account-margin-call-handling`.
