# Withdrawal Velocity & Anomaly Detection — Procedures

## Workflow 1: Pre-disbursement evaluation pipeline

The ordering matters. The latch is checked before the cap, the record is bound
before its age is measured, and the profile is validated before it is trusted.

```mermaid
sequenceDiagram
    autonumber
    participant Client as Withdrawal Gateway
    participant Engine as WithdrawalVelocityEngine
    participant Clock as Trusted Clock
    participant Signer as Hot Wallet Signer

    Client->>Engine: evaluate_withdrawal_request(req, record, profile, evaluation_timestamp)
    Engine->>Clock: read trusted now (never req.timestamp)
    Engine->>Engine: 0. Replay cache hit on request_id?

    alt Known request_id
        Engine-->>Client: Original decision replayed (idempotent)
    else New request
        Engine->>Engine: Validate amounts finite and positive; skew-check req.timestamp

        alt Breaker already latched
            Engine-->>Client: REJECTED_FREEZE (awaiting manual reset)
        else Breaker armed
            Engine->>Engine: 1. Global hot wallet 1h velocity + amount > global cap?

            alt Global cap breached
                Engine->>Engine: LATCH freeze, record reason and time
                Engine-->>Client: REJECTED_FREEZE (SOC page, queue halted)
            else Within global cap
                Engine->>Engine: 2. Account rolling 1h / 24h velocity
                Engine->>Engine: 3. Z-score, or flag INSUFFICIENT_PROFILE_HISTORY
                Engine->>Engine: 4. Bind record to (account, address), then check age

                alt Any risk flag
                    Engine->>Engine: Park request awaiting review
                    Engine-->>Client: TIMELOCK_HOLD + flags
                else Clean
                    Engine->>Engine: Append to ledger stamped with trusted clock
                    Engine-->>Signer: APPROVED
                end
            end
        end
    end
```

## Workflow 2: Disposition of a held withdrawal

A hold is not a decision — it is a deferral, and both exits must be taken
explicitly. A hold left in `_held_requests` forever is a stuck customer
withdrawal; a hold released without `release_held_withdrawal` is an unmetered
hole in every rolling cap.

```mermaid
flowchart TD
    A[TIMELOCK_HOLD issued with risk flags] --> B{Which flags?}

    B -- WHITELIST_RECORD_MISMATCH --> C[Treat as integration fault or tampering]
    C --> D[Audit the allowlist lookup path before anything else]
    D --> E[Do NOT release on the strength of elapsed time alone]

    B -- NEW_ADDRESS_HOLD only --> F[Wait out the cooling period, notify the account owner out of band]
    B -- EXCEEDS_HOURLY / DAILY --> G[Confirm the flow is expected for this account]
    B -- ANOMALY_SIZE_ZSCORE --> H[Step-up authentication with the account owner]
    B -- INSUFFICIENT_PROFILE_HISTORY --> I[No baseline exists; verify by another means]

    F --> J{Review outcome}
    G --> J
    H --> J
    I --> J
    E --> J

    J -- Approve --> K["release_held_withdrawal(request_id, authorized_by)"]
    K --> L[Amount enters the velocity ledger at the release time]
    L --> M[Disburse]

    J -- Reject --> N["cancel_held_withdrawal(request_id)"]
    N --> O[Request can never be released; investigate as an incident]
```

## Workflow 3: Global hot wallet freeze and recovery

```mermaid
flowchart TD
    A[Global 1h velocity would breach the cap] --> B[Breaker LATCHES: hot_wallet_frozen = True]
    B --> C[Every subsequent request returns REJECTED_FREEZE, regardless of size]
    C --> D[Pause the automated signer service]
    D --> E[Page the SOC; escalate inside and outside office hours]

    E --> F[Isolate suspect accounts and API keys]
    F --> G[Reconcile on-chain movements against the ledger]

    G --> H{Incident understood and contained?}

    H -- No --> I[Maintain the freeze; sweep remaining float to cold storage]
    I --> G

    H -- Yes --> J["reset_hot_wallet_freeze(authorized_by) under multi-party approval"]
    J --> K[Reset is logged with the authoriser and the prior freeze reason]
    K --> L[Resume the automated disbursement queue]
    L --> M[Recalibrate caps if the breach was legitimate flow, not an attack]
```

## Procedure: calibrating the caps

Do not start from the defaults. They are placeholders (see
`references/standards.md` §2).

1. **Measure before you limit.** Pull 90 days of withdrawal history and compute,
   per account tier, the rolling 1h and 24h USD totals. Plot the distribution.
2. **Set the account caps above routine flow, below ruin.** A cap below the 99th
   percentile of legitimate behaviour generates holds your review team cannot
   clear; a cap above the account's plausible balance protects nothing.
3. **Set the global cap from loss tolerance, not from the sum of account caps.**
   Ask: how much may leave the hot wallet in one hour before that is an
   unrecoverable incident? The sum of per-account caps is usually far larger than
   this — that gap is precisely why the global breaker exists.
4. **Size the hold period against your review SLA.** A 24h hold with a 72h review
   queue is a 72h hold that lies to the customer.
5. **Re-derive the profiles on a lag.** Compute `mu`/`sigma` from a window that
   ends before the current evaluation window, and exclude previously flagged
   amounts, so an attacker's own activity cannot drag the baseline toward itself.
6. **Rehearse the freeze.** Trip the breaker in a staging environment and time
   the path to `reset_hot_wallet_freeze`. A breaker nobody has ever reset is an
   untested outage.

## Procedure: operating the engine safely

- **Persist the ledger.** The in-memory ledger zeroes every rolling window on
  restart. Back it with durable storage and reload the last 24 hours at startup,
  or a crash loop becomes a withdrawal window.
- **Serialise evaluate-then-submit.** The engine is not thread-safe. Two
  concurrent evaluations both read a pre-update ledger and can each approve an
  amount that only one of them had capacity for.
- **Pass `evaluation_timestamp` explicitly** in tests, replays, and audits.
  Defaulting to wall-clock time makes decisions unreproducible.
- **Alert on `decision.warnings`, not just on flags.** Persistent clock skew and
  repeated `INSUFFICIENT_PROFILE_HISTORY` are integration problems that quietly
  degrade the anomaly layer.
- **Treat `anomaly_zscore is None` as "did not run".** It is not a pass, and a
  dashboard that renders it as 0.0 is reporting a check that never happened.
