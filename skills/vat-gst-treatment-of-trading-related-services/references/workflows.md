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
    
    alt Category in (EXCHANGE_FEES, CLEARING_FEES, BROKERAGE)
        Engine->>TaxDB: Record EXEMPT (0% VAT, No Input Tax Recovery)
    else Vendor Jurisdiction != Entity Jurisdiction AND Entity != US
        Engine->>Rules: Calculate Domestic Standard Rate RCM VAT
        Engine->>Engine: Apply Partial Exemption Recovery % (Recoverable vs Expense)
        Engine->>TaxDB: Record REVERSE_CHARGE (Self-Assess Output & Input VAT)
    else Domestic Standard-Rated Service (Co-location, Software)
        Engine->>Engine: Calculate Input VAT Paid = Net * Rate
        Engine->>Engine: Apply Partial Exemption Recovery % (Recoverable vs Expense)
        Engine->>TaxDB: Record STANDARD_RATED (Input VAT Paid & Expense)
    end

    Engine-->>AP: Return VATTaxAssessment Result
```

---

## Workflow 2: Partial Exemption Special Method (PESM) & Return Filing
```mermaid
flowchart TD
    A[Initiate Period-End VAT/GST Return] --> B[Calculate Turnover: Taxable vs Exempt Supplies]
    
    B --> C[Compute Pro-Rata Recovery Ratio %: Taxable / Total * 100]
    C --> D[Invoke set_partial_exemption_ratio(Ratio)]
    
    D --> E[Batch Process Invoices via generate_vat_return_summary()]
    E --> F[Sum Total Input VAT Paid, Reverse Charge VAT & Recoverable VAT]
    
    F --> G[Generate Official VAT Return Form (UK Box 1-9 / EU Return / SG GST F5)]
    G --> H[Submit Return & Post Unrecoverable Input VAT Expense to PnL]
```