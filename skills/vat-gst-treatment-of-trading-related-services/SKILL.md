---
name: vat-gst-treatment-of-trading-related-services
description: >-
  Use when classifying a trading entity's vendor invoices for VAT or GST across UK, EU,
  Singapore and Australia: exempt financial supply, standard-rated, cross-border reverse
  charge or out of scope, and the input tax that is actually recoverable.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: vat, gst, reverse-charge-mechanism, rcm, partial-exemption, input-tax-recovery, place-of-supply, trading-expenses, tax-accounting
  brokers_frameworks: "VATA 1994 (ss.7A, 8, 26, Sch 9 Grp 5); VAT Regulations 1995 (SI 1995/2518) reg 101; VAT (Input Tax) (Specified Supplies) Order 1999 (SI 1999/3121); EU VAT Directive 2006/112/EC (Arts 44, 47, 135, 174-175, 196); HMRC VAT Notice 701/49; IRAS GST Reverse Charge Regime; A New Tax System (GST) Act 1999 Div 84 (Australia); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

# VAT/GST Treatment of Trading-Related Services

Determines the indirect tax treatment of the services a trading entity buys —
exchange and clearing fees, brokerage, exchange connectivity, co-location,
market data, software licences, management recharges — and works out how much of
the VAT/GST on them the entity can actually recover.

Two things drive the answer: what the service *is* (an exempt financial supply,
or a taxable one) and where the supplier and the recipient *belong* (domestic,
or an import triggering a reverse charge). On top of that sits the partial
exemption ratio, because a trading entity's largely exempt income means most of
its input VAT is a real cost, not a timing difference.

## When to Use

Use this skill when processing vendor invoices, preparing a periodic VAT/GST
return, or auditing the indirect tax leakage in a trading entity's cost base
across **UK HMRC**, **EU member states**, **Singapore IRAS** and **Australia ATO**.

The engine:

- Classifies an invoice as `EXEMPT`, `STANDARD_RATED`, `REVERSE_CHARGE` or
  `OUT_OF_SCOPE` (`assess_invoice_tax`).
- Self-assesses **both** reverse-charge legs — output VAT for UK return Box 1 and
  the ratio-restricted input tax claim for Box 4.
- Computes the **pro-rata recovery ratio** with the statutory rounding-up rule
  applied where it exists (`set_partial_exemption_ratio`).
- Quantifies **unrecoverable input VAT** as an expense hitting trading PnL.
- Aggregates a **period return summary**, keeping every per-invoice
  determination in `summary.assessments` for the audit trail
  (`generate_vat_return_summary`).

Every assessment carries a `warnings` tuple naming the determinations the engine
could not make from the category alone.

## When NOT to Use

- **As the place-of-supply analysis itself.** The engine assumes the modelled
  taxable services follow the B2B general rule (VATA 1994 s.7A; Directive
  Art 44). It flags, but cannot resolve, the immovable-property exception for
  exclusive-use co-location (CJEU C-215/19 *A Oy*).
- **To split a bundled exchange invoice.** Exchange execution may be exempt
  while membership, port, connectivity and technology charges on the same
  invoice are standard-rated (HMRC VAT Notice 701/49 para 6.9). The split is a
  human determination; the engine warns and gives you a category for the
  standard-rated half.
- **Across currencies.** No FX conversion is performed. Amounts must be
  pre-converted to a single currency at the correct statutory rate and date, and
  the return is filed in the tax jurisdiction's own currency.
- **For credit notes, refunds or negative amounts.** Rejected by design rather
  than silently signed through the recovery arithmetic.
- **For special methods (PESM), annual adjustments, the capital goods scheme,
  the UK reg 106 de minimis test, VAT groups, or the Australian reduced input
  tax credit regime.** None are modelled.
- **As the Australian apportionment method.** The ATO prescribes no turnover
  pro-rata; GSTR 2006/3 requires a "fair and reasonable" method, with direct
  estimation preferred.
- **As tax advice or a filed return.** It produces indicative figures for a tax
  function to review, not a submission.

## Prerequisites

- Python 3.7+, standard library only.
- An accounts payable ledger providing, per invoice: `invoice_id`,
  `vendor_name`, `vendor_jurisdiction`, `entity_jurisdiction`,
  `service_category` and a VAT-exclusive `net_amount_usd`, all amounts already
  in one currency.
- The entity's taxable and exempt turnover for the period, with UK "specified
  supplies" (exempt Grp 5 services to non-UK customers, SI 1999/3121) counted as
  **taxable**.
- A view on whether the entity is entitled to a full input tax credit — this
  determines whether Singapore's and Australia's reverse charge regimes apply
  at all.

## Workflow

1. **Set the recovery ratio first.** Call
   `set_partial_exemption_ratio(taxable_supplies_usd, exempt_supplies_usd, rounding)`
   — or pass a known percentage to the constructor. Choose `rounding` by
   jurisdiction: `UP_WHOLE_PERCENT` for UK and EU entities (reg 101(4);
   Art 175(1)), `UP_TWO_DECIMALS` where UK reg 101(5) applies (residual input
   tax over £400,000/month on average), `NONE` for Singapore and Australia. The
   default is `NONE`, so a UK/EU entity that leaves it unset under-recovers.
2. **Ingest invoices** as `TradingExpenseInvoice` records. Split bundled
   exchange invoices before ingestion, booking the standard-rated lines as
   `EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE`.
3. **Assess each invoice** with `assess_invoice_tax(invoice)`. The order of
   determination is: exempt financial supply first (an exempt import carries no
   reverse charge), then the cross-border test, then domestic treatment.
4. **Resolve the warnings.** Each entry marks something the category could not
   settle — a bundled exchange invoice, an exclusive-use co-location contract, a
   Singapore/Australia entitlement question, US sales tax. Clear them before the
   return is filed; do not treat a warned determination as final.
5. **Generate the return summary** with `generate_vat_return_summary(invoices)`.
   Map `total_output_vat_usd` to UK Box 1 and `total_recoverable_input_vat_usd`
   to Box 4, then **add output VAT on the entity's own sales** — the engine
   covers the purchase ledger only. Post
   `total_unrecoverable_vat_expense_usd` to PnL and retain
   `summary.assessments`.

## Common Pitfalls

- **Treating every exchange invoice as exempt.** HMRC VAT Notice 701/49 para 6.9:
  "Basic admission or membership charges are taxable at the standard rate…
  The liability of other charges depends on exactly what is being done by the
  exchange for the charge." Booking the whole invoice as exempt loses the input
  tax recovery on the standard-rated element.
- **Reverse-charging an exclusive-use co-location cage.** CJEU C-215/19 *A Oy*
  turned on the customer having **no** exclusive right of use of a defined
  space. A dedicated cage or suite can flip the supply into the
  immovable-property rule, taxable where the data centre sits — meaning a local
  VAT registration obligation, not a reverse charge, and a mis-filed return in
  two jurisdictions if you get it wrong.
- **Assuming the reverse charge is universal.** Singapore's imported-services
  regime and Australia's GST Act Div 84 both apply only where the recipient is
  *not* entitled to a full input tax credit. A fully-recovering SG or AU entity
  is outside them; a UK or EU entity is inside regardless of recovery position.
- **Declaring only the net reverse-charge cost.** The self-assessed output VAT
  belongs in Box 1 and the restricted claim in Box 4. Netting them off
  understates declared output tax even though the cash effect is identical.
- **Skipping the statutory rounding-up.** A raw 20.4% ratio is 21% under UK
  reg 101(4) and EU Art 175(1). Using the raw figure systematically
  under-recovers input VAT.
- **Leaving specified supplies out of the numerator.** Exempt Grp 5 supplies to
  non-UK customers carry recovery under SI 1999/3121; parking them in the exempt
  bucket understates the ratio.
- **Assuming 100% recovery on co-location and market data.** A trading entity
  with largely exempt income recovers only its pro-rata share; the rest is a
  permanent PnL cost, not a receivable.
- **Filing in the wrong currency.** VAT/GST is declared in the tax
  jurisdiction's currency. This engine does no FX conversion — a ledger in USD
  assessed at 20% does not produce a filable UK figure.
- **Changing the ratio mid-batch.** `generate_vat_return_summary` applies
  whatever ratio the engine currently holds, so re-running it after a ratio
  change silently produces different numbers for the same invoices.

## Verification

Run the unit test suite. It covers exempt financial services, the exchange
membership split, domestic standard-rated supplies, cross-border reverse charge
including the self-assessed output VAT leg and recipient-rate selection, the
Singapore/Australia full-credit carve-out, statutory rounding (whole percent,
two decimals, the 100% cap and the exact-percentage guard), fail-closed rate
lookup for unmapped or non-enum jurisdictions, ratio validation, and return
summary aggregation with its audit trail:

```bash
python -m unittest discover -s skills/vat-gst-treatment-of-trading-related-services/scripts
```

Repository-wide checks:

```bash
python tools/validate_skills.py
python tools/run_all_tests.py
```

## Related Skills

- `transfer-pricing-for-multi-entity-trading-operations`
- `multi-jurisdiction-tax-residency-implications`
- `record-keeping-requirements-for-tax-audit-defense`
- `market-data-entitlement-and-licensing-per-venue`
- `exchange-fee-tier-and-rebate-structure-analysis`
