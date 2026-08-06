# Institutional Crypto Transfer Verification Workflows

## Workflow 1: Large Transfer Pre-Flight & Test Transaction Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Ops as Treasury Operations / Bot
    participant Engine as Verification Engine
    participant Whitelist as HSM Whitelist Directory
    participant Chain as Blockchain Node / RPC
    participant Custody as Custody Vault / MPC

    Ops->>Engine: initiate_transfer_request(req)
    Engine->>Whitelist: Check recipient address
    alt Address Not Whitelisted
        Whitelist-->>Engine: Not Found
        Engine-->>Ops: REJECTED (WhitelistError)
    else Address Whitelisted
        Whitelist-->>Engine: Approved
    end

    alt Value < $50,000 USD
        Engine-->>Ops: NOT_REQUIRED (Direct Execution Approved)
    else Value ≥ $50,000 USD
        Engine-->>Ops: TEST_PENDING (Mandatory Dust Test Tx Required)
        Ops->>Chain: Broadcast Dust Test Tx
        Ops->>Engine: record_test_transaction(req_id, tx_hash)
        
        loop Block Confirmation Monitoring
            Engine->>Chain: Query Block Depth
            Chain-->>Engine: Confirmations (n/N)
            Engine->>Engine: update_test_confirmations(req_id, n)
        end
        
        Engine-->>Ops: TEST_CONFIRMED
        
        Ops->>Engine: verify_and_authorize_large_transfer(req_id)
        alt Confirmations < Required OR Time > 30 mins
            Engine-->>Ops: REJECTED / EXPIRED
        else Confirmed & Active Window
            Engine-->>Ops: APPROVED
            Ops->>Custody: Release Primary Large Transfer Payload
        end
    end
```

---

## Workflow 2: Destination Memo / Tag Enforcement Workflow
```mermaid
flowchart TD
    A[Transfer Request Initiated] --> B{Asset Class}
    B -->|ETH / BTC / SOL| C[Check Whitelist Status]
    B -->|XRP / TON / XLM / EOS| D{Destination Tag Specified?}
    
    D -- No Tag --> E[REJECT: Missing Destination Memo/Tag]
    D -- Tag Present --> C
    
    C -- Unwhitelisted --> F[REJECT: Recipient Address Not Whitelisted]
    C -- Whitelisted --> G{USD Transfer Value}
    
    G -- Below Threshold --> H[Authorize Primary Transfer]
    G -- Exceeds Threshold --> I[Mandate Dust Test Transaction]
```