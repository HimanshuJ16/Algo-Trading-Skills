# Institutional Withdrawal Velocity & Security Workflows

## Workflow 1: Pre-Disbursement Velocity & Anomaly Evaluation Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant User as Client / API Withdrawal Gateway
    participant Engine as Withdrawal Velocity Engine
    participant WhitelistDB as Address Whitelist Database
    participant HSM as Hot Wallet HSM / Signer

    User->>Engine: Submit Withdrawal Request (AccountId, Asset, AmountUSD, Address, Time)
    Engine->>Engine: 1. Check Global Hot Wallet 1h Velocity Limit ($2M)
    
    alt Global Limit Exceeded
        Engine-->>User: REJECTED_FREEZE (Hot Wallet Frozen for SOC Review)
    else Global Limit OK
        Engine->>Engine: 2. Calculate Rolling 1h & 24h Account Velocity
        Engine->>WhitelistDB: Query Whitelist Record (IsWhitelisted, AddedTimestamp)
        WhitelistDB-->>Engine: Return Whitelist Record
        Engine->>Engine: 3. Compute User 90-Day Anomaly Z-Score
        
        alt Risk Flags Present (Velocity Exceeded, Z >= 3.0, or Address < 24h)
            Engine-->>User: TIMELOCK_HOLD (Enforce 24h Escrow & Step-Up 2FA)
        else All Velocity & Risk Checks Compliant
            Engine->>HSM: Route Approved Transaction to Hot Wallet Signer
            HSM-->>User: APPROVED (Broadcast to Blockchain)
        end
    end
```

---

## Workflow 2: Global Hot Wallet Circuit Breaker Incident Pipeline
```mermaid
flowchart TD
    A[Global Hot Wallet 1h Velocity Breaches Threshold] --> B[Trigger REJECTED_FREEZE Decision]
    
    B --> C[Immediately Pause Automated Hot Wallet Signer Service]
    C --> D[Dispatch PagerDuty Incident Alert to Security Operations Center]
    
    D --> E[Isolate Compromised Account / API Keys]
    E --> F[Conduct Hot Wallet Ledger & On-Chain Audit]
    
    F --> G{Security Incident Resolved?}
    
    G -- Yes --> H[Manual Multi-Sig Reset of Hot Wallet Circuit Breaker]
    G -- No --> I[Maintain Hot Wallet Freeze & Evacuate Funds to Cold Storage]
    
    H --> J[Resume Automated Disbursement Queue]
```