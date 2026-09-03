# Large Transfer Verification Workflows

## Workflow 1: Pre-flight, test transfer, and authorisation

Note the two facts the sequence turns on. The dust transaction is **read back
from the chain** and bound to the request before it counts, and the counterparty
must **attest arrival out of band** — on-chain depth alone confirms a wrong
address just as reliably as a right one.

```mermaid
sequenceDiagram
    autonumber
    participant Ops as Treasury Operations / Bot
    participant Engine as Verification Engine
    participant Whitelist as HSM / MPC Address Book
    participant Chain as Blockchain Node / RPC
    participant CP as Counterparty (Approved Channel)
    participant Custody as Custody Vault / MPC

    Ops->>Engine: initiate_transfer_request(req)
    Note over Engine: Reject non-finite/negative notional<br/>before any threshold comparison
    Engine->>Whitelist: canonicalised recipient lookup
    alt Not whitelisted
        Engine-->>Ops: WhitelistError
    else Whitelisted
        alt Value < threshold
            Engine-->>Ops: NOT_REQUIRED (direct execution)
        else Value >= threshold
            Engine-->>Ops: TEST_PENDING

            Ops->>Chain: broadcast dust test transfer
            Ops->>Chain: read back tx by hash
            Chain-->>Ops: recipient, chain, amount
            Ops->>Engine: record_test_transaction(id, hash, observed_*)
            alt observed recipient / chain / amount disagree with request
                Engine-->>Ops: TestTransactionMismatchError (escalate, do not retry)
            end

            loop Depth monitoring
                Ops->>Chain: query confirmation depth
                Ops->>Engine: update_test_confirmations(id, n)
                Note over Engine: confirmed_at latched at FIRST crossing.<br/>Depth regression (re-org) clears the latch.
            end

            Ops->>CP: confirm dust arrival via Approved Channel
            CP-->>Ops: attestation
            Ops->>Engine: acknowledge_test_receipt(id, attested_by, channel)

            Ops->>Engine: verify_and_authorize_large_transfer(id)
            Note over Engine: Re-checks binding, whitelist NOW,<br/>depth NOW, receipt, and window
            alt Any check fails
                Engine-->>Ops: Pending / Expired / Whitelist / Mismatch error
            else All pass
                Engine-->>Ops: APPROVED (single-use, request consumed)
                Ops->>Custody: release primary transfer payload
            end
        end
    end
```

## Workflow 2: Destination memo / tag enforcement

```mermaid
flowchart TD
    A[Transfer request initiated] --> V{Notional finite and >= 0?}
    V -- No --> R0[REJECT: VerificationError<br/>NaN never reaches the comparison]
    V -- Yes --> B{Asset class}

    B -->|ETH / BTC / SOL / BNB BEP-20| C[Check whitelist status]
    B -->|XRP / XLM / TON / EOS| D{Destination tag / memo present?}

    D -- Absent or whitespace-only --> E[REJECT: missing destination memo/tag]
    D -- Present --> C

    C -- Unwhitelisted --> F[REJECT: recipient not whitelisted]
    C -- Whitelisted --> G{USD notional}

    G -- Below threshold --> H[Authorise primary transfer]
    G -- At or above threshold --> I[Mandate dust test transfer]
```

BNB sits on the left branch: the memo-bearing BEP-2 Beacon Chain was shut down on
2024-11-19, and BEP-20 on BNB Smart Chain uses no memo.

## Workflow 3: Re-org and expiry state machine

The expiry window exists because a verified destination decays — the counterparty
may rotate keys, or an attacker may swap the address after a successful test.
Two rules keep it meaningful.

```mermaid
stateDiagram-v2
    [*] --> TEST_PENDING: record_test_transaction (bound to request)
    TEST_PENDING --> TEST_CONFIRMED: depth >= min_confirmations<br/>(confirmed_at LATCHED here, once)
    TEST_CONFIRMED --> TEST_PENDING: depth < min_confirmations<br/>(re-org: latch CLEARED)
    TEST_CONFIRMED --> RECEIPT_ACKNOWLEDGED: acknowledge_test_receipt
    RECEIPT_ACKNOWLEDGED --> TEST_PENDING: re-org
    RECEIPT_ACKNOWLEDGED --> EXPIRED: now - confirmed_at > window
    RECEIPT_ACKNOWLEDGED --> APPROVED: verify_and_authorize_large_transfer
    APPROVED --> [*]: request consumed, single-use
    EXPIRED --> [*]: new test transfer required
```

1. **The latch is set once.** Subsequent `update_test_confirmations` calls do not
   refresh `confirmed_at`. If they did, the confirmation poller would hold the
   window open indefinitely and the time-decay control would never fire.
2. **A depth regression clears the latch.** The window then restarts from the
   re-confirmation, not from a depth that no longer exists on the canonical chain.

## Operational notes

- **Do not catch `TestTransactionMismatchError` and retry.** It means the dust
  landed somewhere other than where the primary transfer is going. Retrying sends
  more money to the same wrong place. Escalate to a human.
- **`observed_*` must come from the chain, not from your own request object.**
  Echoing back the intended address makes the binding check a tautology and
  restores exactly the hole this control exists to close.
- **Authorisation is single-use.** A caller that retries
  `verify_and_authorize_large_transfer` after a network error gets an exception,
  not a second approval. Persist the first result rather than re-requesting it.
- **The engine is not thread-safe.** Its state is plain dicts and sets with no
  locking. Confine one engine instance to one thread, or serialise access
  externally — two threads racing the same `request_id` through the
  consume-on-approve path is precisely the duplicate-release scenario the
  single-use rule is meant to prevent.
