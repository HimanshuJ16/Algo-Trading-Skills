# Standards — physical-vs-cash-settlement-handling

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator or
exchange mandates a close-out buffer ahead of a delivery deadline. What exists
is a **broker** house rule, which differs between brokers and between products,
and which the broker may enforce by liquidating the position without further
notification. Look yours up and record it.

| Parameter | Default | What it actually does |
|---|---|---|
| `long_close_out_buffer_days` | $2$ | Business days before **first notice day** at which an unprovisioned long is escalated to `PHYSICAL_DELIVERY_RISK_BREACH`. Mirrors IBKR's published policy for long holders. |
| `short_close_out_buffer_days` | $2$ | Business days before **last trading day** at which an unprovisioned short breaches. A short is not bound by first notice day. |
| `multiplier` | $100$ | Per contract, not per engine, and expressed in **deliverable units** (1,000 barrels for CL; 100 shares for a standard US equity option) rather than in dollars. |
| `settlement_price_is_final` | `False` | Until the exchange publishes the final settlement value, every verdict derived from the price is provisional. |
| `prior_settlement_price` | `None` | Absent it, the reported "final variation" is lifetime PnL, and the report says so via `NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY`. |

## Market-structure facts (verified against primary sources)

| Fact used | Source | Applicability |
|---|---|---|
| The **short** initiates delivery. On position day the short notifies CME Clearing of its intention to deliver and registers a shipping certificate; CME Clearing "ranks the long positions according to the amount of time they have been open and assigns the oldest long position to the short position holder that has given his intention to deliver." The long then "makes payment to CME Clearing, and CME Clearing simultaneously transfers the payment from the long to the short position holder and transfers the shipping certificate from the short to the long." | CME Group, *Understanding the Grain Delivery Process* — <https://www.cmegroup.com/education/courses/introduction-to-agriculture/grains-oilseeds/understanding-the-grain-delivery-process> | CBOT grains and oilseeds; the intention/notice/delivery sequence and the pays-vs-delivers split generalise across CME physically delivered contracts. Allocation method and timetable are contract-specific. |
| A **warehouse receipt** represents ownership of physical commodity in an exchange-approved warehouse; a **shipping certificate** is a negotiable instrument representing a commitment by an approved facility to deliver on request. These, not cash, are what a short tenders. | CME Group, *Warehouse Receipts vs. Shipping Certificates FAQ* — <https://www.cmegroup.com/education/articles-and-reports/warehouse-receipts-vs-shipping-certificates-frequently-asked-questions> | Why `deliverable_units_available`, not `account_cash_balance`, is the short-side provisioning test. |
| **NYMEX WTI Crude Oil (CL)**: 1,000 barrels, physically delivered FOB at a pipeline or storage facility in Cushing, Oklahoma. Trading terminates 3 business days before the 25th calendar day of the month preceding the contract month. Crude flows no earlier than the first and no later than the last calendar day of the delivery month. | CME Group, Light Sweet Crude Oil contract specifications and the NYMEX physically-delivered crude oil FAQ — <https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html>, <https://www.cmegroup.com/trading/energy/nymexs-physically-delivered-light-sweet-crude-oil-futures-faq.html> | A contract whose **last trading day precedes the delivery period entirely**. |
| **COMEX Gold (GC)**: first notice day is the last business day of the month prior to the delivery month; last trading day is the third-last business day of the delivery month; last notice day is the second-last business day of the delivery month. | CME Group, Gold contract specifications and delivery calendar — <https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html> | A contract whose **first notice day precedes last trading day by weeks** — a long can be assigned while the contract still trades actively. *Confidence: high but indirect — the CME specification page did not return over HTTPS during this pass and the dates were taken from consistent secondary summaries of it. Re-verify against the contract's delivery calendar before relying on the exact day counts.* |
| IBKR does not permit making or taking delivery on certain physically delivered futures. Its close-out deadline for **long** holders is the end of the second business day prior to the exchange-specified **first notice day**; for **short** holders, the end of trading on the second business day prior to the exchange-specified **last trading day**. Past it, IBKR may liquidate the position without additional prior notification. | Interactive Brokers, *Futures Close-Out Policy* — <https://www.interactivebrokers.com/en/trading/futures-close-out.php>, <https://www.interactivebrokers.ca/en/trading/marginRequirements/physicalDeliveryLiquidationRules.php> | One broker's house rule, and the source of the engine's 2-day defaults. It is **not** a regulatory standard. *Confidence: high on the long/short split, medium on the exact figure — the pages returned HTTP 403 to direct retrieval during this pass. Confirm against your own broker's current published policy.* |
| The final settlement price of CME equity index futures is a **Special Opening Quotation** based on the opening prices of the index component stocks on the third Friday, "regardless of when those stocks open on expiration day"; if a stock does not open, its last sale price is used. | CME Group, *Final Settlement Procedures* (equity index) — <https://www.cmegroup.com/trading/equity-index/settlement.html> | Why a cash-settled position marked at the last regular-session trade is marked at the wrong number, and why `settlement_price_is_final` exists. |
| The US standard settlement cycle for the stock leg is **T+1**, effective 28 May 2024 under SEC Rule 15c6-1(a) as amended; OCC implemented the change on the same date and no longer accommodates late exercise notices. | SEC Rule 15c6-1(a); OCC Information Memo #54580, 14 May 2024 — <https://infomemo.theocc.com/infomemos?number=54580> | Sets when cash for an exercised physically settled equity option is actually due. |

### What this skill deliberately does not claim

- **No mandatory liquidation deadline is asserted.** Earlier revisions recorded
  "MANDATORY liquidation/roll $\ge 3$ days prior to FND" as an engineering
  standard. No regulator or exchange publishes that requirement, and the one
  published deadline located here — a broker's — is 2 business days and is
  keyed to a *different date* for each side. The buffer is now a configurable
  policy value with the provenance stated, not a constant.
- **Invoice amounts are principal only.** Grade and location differentials,
  storage, demurrage and load-out charges are contract-specific and are not
  modelled. A delivery invoice from this engine is a floor, not a total.
- **Assignment probability is not modelled.** CME Clearing assigns the oldest
  open long. Position age is not an input here, and no likelihood is emitted.

## Engineering standards enforced by this skill

| Metric | Engineering Standard |
|---|---|
| Input integrity | Non-finite prices, a non-positive multiplier or strike, negative deliverable units, fractional business-day counts, an unrecognised `settlement_type` or `instrument_kind`, and a physically settled option with no `strike_price` MUST raise. A settlement control MUST NOT return "safe" on data it could not evaluate. |
| Negative prices | Settlement, entry and prior-settlement prices MUST be accepted at or below zero. NYMEX WTI settled at -\$37.63 on 20 April 2020 because longs could not take delivery at Cushing; a validator demanding positive prices refuses the event this skill exists to screen for. A negative invoice means the long is *paid* to take delivery, so `has_delivery_facility` becomes the only binding test. |
| Flag naming | A cash-settled funding shortfall MUST NOT raise a flag named for a delivery invoice. The cash branch raises `INSUFFICIENT_CASH_FOR_SETTLEMENT_DEBIT`; the physical long branch raises `INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE`. |
| Unknown settlement type | MUST raise, never default. Defaulting to physical fabricates a delivery obligation; defaulting to cash hides a real one. |
| Missing delivery clock | A physically settled position whose **binding** day count is absent MUST raise. Reporting compliance against an unknown deadline is the failure mode this skill exists to prevent. The non-binding clock is optional. |
| Direction asymmetry | The obligation, the binding deadline and the resource tested MUST all be selected by the sign of `position_qty`. A long is tested for cash; a short for the deliverable. |
| Delivery price basis | A futures delivery MUST be invoiced at the final settlement price; a physically settled option exercise MUST be invoiced at the **strike**. The basis used MUST be reported (`delivery_price_basis`). |
| Cashflow separation | The final variation cashflow and lifetime PnL MUST be reported as separate fields, and the report MUST flag when they are the same number only because no prior settlement price was supplied. |
| Flat positions | MUST report `FLAT_NO_OBLIGATION` and never a delivery breach. |
| Past-deadline handling | MUST be a distinct status recommending escalation, not a close-or-roll directive that no longer removes the obligation. |
| Book-level funding | Aggregate delivery invoices MUST be tested against the account balance; per-position checks alone are individually satisfiable and collectively false. |
