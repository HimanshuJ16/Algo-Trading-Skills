# Institutional VAT/GST Tax Assessment Workflows

## Workflow 1: Invoice Tax Classification & Reverse Charge Decision Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant AP as Accounts Payable Ledger
    participant Engine as VAT/GST Tax Engine
    participant Rules as Jurisdiction Rules Engine
    participant TaxDB as VAT Return Ledger

    AP->>Engine: Submit Invoice (Vendor, Entity Jurisdiction, Category, Net Amount)
    Engine->>Engine: Validate enums, finite positive amount, mapped standard rate

    alt Category in (EXCHANGE_EXECUTION_FEE, CLEARING_FEE, BROKERAGE_COMMISSION)
        Engine->>TaxDB: Record EXEMPT (0% VAT, no input tax recovery)
        Note over Engine,TaxDB: No reverse charge even on an import - an exempt<br/>supply keeps its liability under VATA 1994 s.8
    else Vendor Jurisdiction != Entity Jurisdiction AND Entity != US
        Engine->>Rules: Does the entity's regime impose a reverse charge?
        alt Entity in (SG, AU) AND recovery ratio = 100%
            Engine->>TaxDB: Record OUT_OF_SCOPE + warning (full input tax credit)
        else UK / EU, or SG / AU without full credit
            Engine->>Rules: RCM VAT = Net x Entity's domestic standard rate
            Engine->>Engine: Output VAT (Box 1) = RCM VAT
            Engine->>Engine: Recoverable (Box 4) = RCM VAT x recovery ratio
            Engine->>TaxDB: Record REVERSE_CHARGE (both legs)
        end
    else Domestic Standard-Rated Service (membership, co-location, software)
        Engine->>Engine: Input VAT Paid = Net x Rate
        Engine->>Engine: Apply partial exemption recovery % (recoverable vs expense)
        Engine->>TaxDB: Record STANDARD_RATED (Input VAT Paid & Expense)
    end

    Engine-->>AP: Return VATTaxAssessment (+ warnings to resolve manually)
```

**Decision points that are not automatic.** The engine emits warnings rather
than silently deciding:

- An `EXCHANGE_EXECUTION_FEE` invoice that also carries membership, port,
  connectivity or technology lines must be **split**, with the standard-rated
  element re-booked as `EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE`
  (HMRC VAT Notice 701/49 para 6.9).
- A cross-border `COLOCATION_DATA_FEED` invoice is reverse-charged on the B2B
  general rule, but an **exclusive-use cage or suite** may instead be a supply
  connected with immovable property, taxable where the data centre sits
  (CJEU C-215/19 *A Oy*) — which requires local registration, not a reverse charge.
- The Singapore/Australia full-credit carve-out uses a 100% recovery ratio as a
  proxy for the statutory entitlement test. Confirm entitlement directly.

---

## Workflow 2: Partial Exemption & Period-End Return Filing
```mermaid
flowchart TD
    A[Initiate Period-End VAT/GST Return] --> B[Calculate Turnover: Taxable vs Exempt Supplies]
    B --> B2[Move specified supplies - exempt Grp 5 services to non-UK customers -<br/>into the TAXABLE numerator per SI 1999/3121]

    B2 --> C[Select statutory rounding:<br/>UK/EU UP_WHOLE_PERCENT, UK reg 101 5 UP_TWO_DECIMALS, SG/AU NONE]
    C --> D["set_partial_exemption_ratio(taxable_supplies_usd, exempt_supplies_usd, rounding)"]

    D --> E["generate_vat_return_summary(invoices)"]
    E --> F[Read totals: output VAT, reverse charge VAT,<br/>input VAT paid, recoverable, unrecoverable]

    F --> G[Map to the return: UK Box 1 output VAT incl. reverse charge,<br/>Box 4 recoverable input VAT / EU return / SG GST F5 / AU BAS]
    G --> G2[Add output VAT on the entity's own sales -<br/>the engine covers the purchase ledger only]
    G2 --> H[Submit return; post unrecoverable input VAT to PnL]
    H --> I[Retain per-invoice assessments from summary.assessments<br/>for the statutory retention period]
```

**Ordering matters.** Set the recovery ratio *before* generating the summary:
`generate_vat_return_summary` applies whatever ratio the engine currently holds,
and re-running it after a ratio change produces different figures for the same
invoices.
