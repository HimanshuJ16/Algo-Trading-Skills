# Institutional UK FCA SM&CR Governance Workflows

## Workflow 1: Pre-Production Algo Deployment Sign-Off & Reasonable Steps Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Dev as Certified Developer (F&P)
    participant Risk as SMF4 (Chief Risk Officer)
    participant Ops as SMF24 (Chief Operations Officer)
    participant Engine as SM&CR Governance Engine
    participant Reg as FCA Compliance Register

    Dev->>Engine: Submit Algo Registration (Code Version, Certified Dev IDs)
    Engine->>Engine: Verify Developer Fitness & Propriety (F&P) Status
    
    Risk->>Engine: Approve Pre-Trade Risk Controls & Limits
    Dev->>Engine: Record Kill Switch & RTS 6 Stress Test Passing Certificates
    
    Ops->>Engine: execute_deployment_sign_off(SMF24, Reasonable Steps Notes)
    Engine->>Engine: Validate Reasonable Steps Notes (min 10 chars) & SMF Authority
    
    Engine-->>Reg: Update Management Responsibilities Map (MRM) [STATUS: APPROVED]
    Engine-->>Dev: Authorize Live Production Deployment
```

---

## Workflow 2: Annual Certification & Management Responsibilities Map (MRM) Audit
```mermaid
flowchart TD
    A[Initiate Annual SM&CR Governance Review] --> B[Audit Senior Management Function Allocations]
    
    B --> C{Unassigned Trading Algorithms?}
    C -- Yes --> D[FLAG NON-COMPLIANCE: Assign SMF Holder Immediately]
    C -- No --> E[Audit Certification Register (Quant Devs & Traders)]
    
    E --> F{Any Dev Expired / Uncertified?}
    F -- Yes --> G[FLAG NON-COMPLIANCE: Revoke Production Commit Access]
    F -- No --> H[Verify Pre-Trade Risk & Kill Switch Testing Logs]
    
    H --> I[Execute generate_mrm_report()]
    I --> J[Submit Signed MRM Audit Report to FCA / PRA Regulators]
```

