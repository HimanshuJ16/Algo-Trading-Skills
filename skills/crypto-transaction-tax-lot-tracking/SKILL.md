---
name: crypto-transaction-tax-lot-tracking
description: >-
  Use when tracking US federal crypto tax lots per wallet across crypto-to-crypto swaps,
  DEX trades and gas fee dispositions, with FIFO, HIFO or LIFO matching and per-lot Form
  8949 output.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: crypto-tax, tax-lot-tracking, crypto-to-crypto-swap, hifo, fifo, gas-fee-deduction, form-8949
  brokers_frameworks: "IRS Form 8949; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in crypto algorithmic trading bots, treasury management engines, and fund accounting platforms to track crypto tax lots and calculate realized capital gains/losses for **US federal** reporting. Unlike traditional equities, **crypto-to-crypto swaps** (e.g. BTC $\to$ ETH or ETH $\to$ USDC) and **gas fee dispositions** (paying gas in ETH/SOL) are dispositions that recognize capital gain or loss. This module tracks lots per wallet/account, matches disposals under FIFO (default), HIFO or LIFO, and emits a per-lot breakdown that maps to Form 8949 rows.

## When NOT to Use

- **Any jurisdiction other than the US.** Every rule encoded here is US federal: the FIFO default, wallet-by-wallet basis scoping, the "more than one year" holding period, and netting transaction costs against proceeds. Other jurisdictions use different defaults (some mandate FIFO or average cost, some tax swaps differently). Do not reuse this classification logic elsewhere.
- **You want HIFO applied automatically to minimise tax.** HIFO and LIFO are elections of *specific identification*, and are only available if the units were identified no later than the date and time of the disposal. The engine refuses HIFO/LIFO without an `identification_reference` rather than producing a basis figure the taxpayer cannot support on audit. Optimising lot selection *after the fact* is not a feature — it is a restatement risk.
- **You need wash-sale adjustments.** This module does not apply any. See `wash-sale-rule-tracking-us` and confirm the current treatment of digital assets with a tax adviser.
- **You need fair market values.** The caller supplies USD FMV and gross proceeds; the engine never prices anything. A wrong FMV produces a confidently wrong gain.
- **You need income recognition for mining/staking rewards.** Register the reward as a lot with the basis you recognised as ordinary income; computing that income event is out of scope.
- **You need exact decimal accounting.** Quantities and amounts are floats, consistent with the rest of this repository. Dollar outputs are rounded to cents at the boundary, but a long chain of float operations can drift a cent. For filing-grade ledgers, reconcile against the exchange's own records.

## Prerequisites

- Tax lot inventory per wallet/account (`lot_id`, `asset`, `acquisition_timestamp`, `quantity`, `unit_cost_basis_usd`, `wallet_id`), where the unit basis already includes acquisition-side transaction costs.
- USD Fair Market Value ($P_{\text{USD}}$) for each disposal, supplied by the caller.
- A **disposal timestamp** for every disposition — the holding period, and therefore short-term vs long-term treatment, cannot be determined without it.
- For any HIFO/LIFO election: a contemporaneous identification record (books-and-records entry or standing order) to pass as `identification_reference`.

## Workflow

1. **Tax Lot Acquisition Registration**:
   - Ingest buys, swap-ins, mining or staking rewards and record USD cost basis ($C_{\text{usd}} = Q \times P_{\text{usd}} + \text{acquisition fee}_{\text{usd}}$).
   - Register the lot **in the wallet or account that holds it**. Basis is tracked wallet-by-wallet; a universal pool across wallets is not the applicable method for dispositions on or after 2025-01-01 (see `references/standards.md`).
2. **Crypto-to-Crypto Swap & Disposal Processing**:
   - Gross proceeds: $\text{Proceeds}_{\text{gross}} = Q_B \times P_{B, \text{usd}}$ (FMV of what is received).
   - Net proceeds: $\text{Proceeds}_{\text{net}} = \text{Proceeds}_{\text{gross}} - \text{TxCost}_{\text{usd}}$. The cost of effecting a swap is allocable to the asset **given up** — subtract it from proceeds; do **not** also add it to the basis of the asset received, or it is deducted twice.
   - Paying gas *in crypto* is its own disposition of that crypto: process it as a separate disposal at the gas token's FMV.
3. **Lot Selection — decide the method before the disposal, not after**:
   - `FIFO` (default): oldest acquisition first. This is the treatment that applies absent an adequate identification, and needs no election.
   - `HIFO` / `LIFO`: highest basis / newest acquisition first. Permitted only as specific identification, and only if the units were identified no later than the date and time of the disposal — pass that record as `identification_reference`. The engine raises without one.
   - Only lots in the named wallet, acquired on or before the disposal timestamp, are candidates. An unrecognised method raises rather than falling back to FIFO — a silent fallback changes which lots are consumed and therefore the tax owed.
4. **Matching and Realization** (applied atomically — lots are only decremented once the whole plan is known to be satisfiable, so a disposal that exceeds inventory raises without corrupting the ledger):
   - Deduct quantity across ranked lots; $\text{Realized PnL} = \text{Proceeds}_{\text{net}} - \text{Cost Basis}_{\text{lot}}$, with net proceeds allocated across matched lots pro rata by quantity.
5. **Per-Lot Form 8949 Classification**:
   - Each matched lot becomes one `CryptoLotMatch` row with its own acquisition date, disposal date, proceeds, basis and term — because a single disposal can straddle **Part I (short-term)** and **Part II (long-term)**.
   - Long-term means held **more than one year**, counted from the day after acquisition through the day of disposal. A disposal on the one-year anniversary is exactly one year and is short-term.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Crypto-to-Crypto Swaps**: treating ETH $\to$ USDC or BTC $\to$ ETH as a non-taxable transfer. Exchanging one digital asset for another recognizes capital gain or loss.
- **Un-tracked Gas Fee Dispositions**: failing to realize gain/loss when disposing of ETH to pay gas. The gas token leaves your hands at its FMV — that is a disposition, not merely an expense.
- **Assuming HIFO Is Yours to Pick at Filing Time**: HIFO is specific identification, which must be made no later than the date and time of the disposal. Reconstructing a favourable lot order months later does not meet that requirement; absent an adequate identification the units are FIFO.
- **Pooling Lots Across Wallets**: basis is determined per wallet or account. A universal pool lets a disposal on one exchange consume basis sitting in a cold wallet — a figure the taxpayer cannot substantiate.
- **Classifying the Holding Period With a 365-Day Count**: across a leap year, 366 elapsed days can still be exactly one year. Buy 2024-01-01, sell 2025-01-01: a `days_held > 365` test calls that long-term, but it is one year to the day — short-term. Use calendar anniversaries.
- **Storing `days_held` on the Lot**: a holding period depends on the disposal date, so a number frozen on the lot is stale for every subsequent disposal.
- **Collapsing a Mixed-Term Disposal Into One Row**: a disposal matching both an old and a recent lot is not "long-term" or "short-term" — it is both, and splits across Part I and Part II. Read `lot_matches`, not the aggregate.
- **Double-Deducting the Swap Fee**: subtracting the fee from proceeds *and* capitalising it into the received asset's basis.
- **Mixing Up Legal Entities**: aggregating tax lots across separate legal entities or personal vs corporate wallets.

## Verification

- Instantiate `CryptoTaxLotTrackerEngine` (defaults to FIFO). Register 2 ETH lots: LOT_1 (10 ETH, \$1,500 unit basis, 2025-01-01) and LOT_2 (5 ETH, \$3,000 unit basis, 2025-06-01). Dispose 4 ETH for 12,000 USDC on 2025-08-01 with FIFO: verify basis \$6,000 and gain \$6,000 from LOT_1.
- Repeat with `matching_method="HIFO"` and `identification_reference="..."`: verify it selects LOT_2, basis \$12,000, and with a \$50 gas fee realizes a \$50 loss (net proceeds \$11,950).
- Call HIFO **without** `identification_reference` and verify it raises rather than silently optimising.
- Dispose 100 ETH against 15 ETH of inventory; verify it raises and that `get_open_quantity("ETH")` is still 15.0 — no lots consumed.
- Register lots acquired 2023-01-01 (2 ETH @ \$1,000) and 2025-05-01 (3 ETH @ \$2,000); dispose 4 ETH for \$10,000 on 2025-06-01. Verify `is_mixed_term` is True, with a \$3,000 long-term row and a \$1,000 short-term row.
- Verify the leap-year boundary: acquired 2024-01-01, disposed 2025-01-01 $\implies$ short-term; disposed 2025-01-02 $\implies$ long-term.
- Register the same asset in two wallets and verify a disposal in one never consumes the other's lots.
- Run `python -m unittest discover -s skills/crypto-transaction-tax-lot-tracking/scripts`.

## Related Skills

- `cross-strategy-tax-lot-optimization`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `wash-sale-rule-tracking-us`
---
