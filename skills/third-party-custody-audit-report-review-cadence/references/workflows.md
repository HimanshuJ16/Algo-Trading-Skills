# Institutional Third-Party Custody Audit Workflows

## Workflow 1: Annual Custody Audit Review Lifecycle
```mermaid
sequenceDiagram
    autonumber
    participant Risk as Operational Risk Manager
    participant Engine as Custody Audit Review Engine
    participant Portal as Custodian Compliance Portal
    participant Board as Risk Committee / Board

    Risk->>Engine: get_overdue_vendors()
    Engine-->>Risk: List of Due/Overdue Audits
    
    Risk->>Portal: Request SOC 1 & SOC 2 Type II Reports
    Portal-->>Risk: Provide SOC Reports & Management Assertions
    
    Risk->>Engine: submit_audit_report(report)
    
    alt SOC Report Coverage Lags Fiscal Year-End
        Risk->>Portal: Request Bridge / Gap Letter
        Portal-->>Risk: Issued Gap Letter
        Risk->>Engine: submit_gap_letter(gap_letter)
    end

    Risk->>Engine: update_cuec_checks(vendor_id, cuec_list)
    Engine->>Engine: evaluate_vendor_compliance(vendor_id)
    Engine-->>Risk: ReviewResult (RiskRating, ComplianceStatus)

    alt Risk Rating == CRITICAL / HIGH
        Risk->>Board: Escalate Custody Risk & Freeze Capital Allocation
    else Risk Rating == LOW / MEDIUM
        Risk->>Engine: Log Compliance Approval & Set Next Due Date
    end
```

---

## Workflow 2: Auditor Opinion & Deficiency Risk Pipeline
```mermaid
flowchart TD
    A[Audit Report Ingested] --> B{Report Type}
    B -->|SOC 1 / SOC 2 Type II| C{Auditor Opinion}
    
    C -->|Qualified / Adverse / Disclaimer| D[Status: ESCALATED, Risk: CRITICAL]
    C -->|Unqualified - Clean| E{Control Deficiencies Reported?}
    
    E -- > 0 Deficiencies --> F[Status: COMPLIANT, Risk: HIGH]
    E -- 0 Deficiencies --> G{Coverage End Age > 365 Days?}
    
    G -- Yes --> H{Valid Gap Letter on File?}
    H -- No --> I[Status: OVERDUE, Risk: HIGH]
    H -- Yes --> J{CUECs 100% Implemented?}
    
    G -- No --> J
    
    J -- No --> K[Status: COMPLIANT, Risk: MEDIUM]
    J -- Yes --> L[Status: COMPLIANT, Risk: LOW]
```