# Institutional US IRS Wash Sale Tax Workflows

## Workflow 1: Realized Loss Identification & 61-Day Window Matching Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant AP as Trade Execution Ledger
    participant Engine as US Wash Sale Engine
    participant TaxDB as Tax Lot & 1099-B Ledger

    AP->>Engine: Submit Trade Execution (Symbol, Side, Date, Price, Quantity)
    Engine->>Engine: Execute FIFO Tax Lot Matching on Sells
    
    alt Realized Gain (Price > Cost Basis)
        Engine->>TaxDB: Record Recognized Capital Gain
    else Realized Loss (Price < Cost Basis)
        Engine->>Engine: Scan Buy Trades in [Loss Date - 30, Loss Date + 30]
        
        alt Replacement Buy Found within 61-Day Window
            Engine->>Engine: Calculate Matched Qty & Disallowed Loss USD
            Engine->>Engine: Adjust Replacement Share Basis = Buy Price + Loss Per Share
            Engine->>TaxDB: Record WashSaleMatch (Disallowed Loss & Adjusted Basis)
        else No Replacement Buy in Window
            Engine->>TaxDB: Record Recognized Capital Loss
        end
    end
```

---

## Workflow 2: Form 1099-B Year-End Tax Summary Generation
```mermaid
flowchart TD
    A[Initiate Year-End US Tax Reporting] --> B[Fetch All Symbol Trade Executions for Tax Year]
    
    B --> C[Execute evaluate_wash_sales_for_symbol() per Symbol]
    C --> D[Sum Gross Realized PnL & Total Disallowed Wash Losses]
    
    D --> E[Calculate Net Allowed Taxable PnL = Gross PnL + Disallowed Losses]
    E --> F[Generate IRS Form 1099-B Disclosure Records]
    
    F --> G[Populate Box 1d Proceeds, Box 1e Cost Basis, Box 1g Wash Sale Disallowed]
    G --> H[Export Audit Ledger for IRS Tax Submission]
```