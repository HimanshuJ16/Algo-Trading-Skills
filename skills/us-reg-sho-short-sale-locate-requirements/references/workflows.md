# SEC Regulation SHO Compliance Workflows

## Workflow 1: Pre-trade marking, locate and price test gate

```mermaid
sequenceDiagram
    autonumber
    participant Trader as Trading System / Algo
    participant Engine as Reg SHO Pre-Trade Gate
    participant LocateDB as Locate Registry
    participant Exchange as US Trading Venue

    Trader->>Engine: OrderIntent (order_id, symbol, marking, qty, price, NBB, locate_id, exempt reason)

    alt order_id already approved and reserved
        Engine-->>Trader: Return original decision, reserve nothing (retry-safe)
    else same order_id, different terms
        Engine-->>Trader: REJECT (duplicate order_id with different terms)
    else new or previously rejected order_id
        Engine->>Engine: Validate structure (symbol, marking, qty > 0, price finite)
        alt Marking == LONG
            Engine-->>Exchange: Route marked LONG (no locate required)
        else Marking == SHORT_EXEMPT without a 201(c)/(d) basis
            Engine-->>Trader: REJECT (Rule 200(g)(2): basis not named)
        else Marking == SHORT or SHORT_EXEMPT
            Engine->>LocateDB: Verify locate (exists, symbol match, unexpired, capacity)
            alt Locate invalid
                Engine-->>Trader: REJECT (Rule 203(b)(1))
            else Locate valid
                alt Rule 201 in force for this covered security
                    alt No valid current national best bid
                        Engine-->>Trader: REJECT (price test not evaluable - fail closed)
                    else Marking == SHORT and price <= NBB
                        Engine-->>Trader: REJECT (Rule 201(b)(1)(i))
                    else Marking == SHORT_EXEMPT under 201(c) and price <= NBB
                        Engine-->>Trader: REJECT (Rule 201(c): claim not borne out)
                    else Price test satisfied or a 201(d) basis applies
                        Engine->>LocateDB: Reserve qty against order_id
                        Engine-->>Exchange: Route marked SHORT / SHORT_EXEMPT
                    end
                else Rule 201 not in force
                    Engine->>LocateDB: Reserve qty against order_id
                    Engine-->>Exchange: Route marked SHORT
                end
            end
        end
    end
```

Every branch above appends a `RegSHOValidationResult` to `engine.audit_log`, including the
rejections.

---

## Workflow 2: Rule 201 restriction lifecycle

The compliance trigger is the listing market's determination under 242.201(b)(3), disseminated
under 242.603(b). The local decline calculation exists only to detect that the SIP feed has
gone stale or silent.

```mermaid
flowchart TD
    A[SIP Reg SHO price test indicator] --> B{Indicator ON for symbol?}
    B -- Yes --> C[trigger_rule_201_ssr, source=SIP_PRICE_TEST_INDICATOR]
    B -- No --> D[No restriction recorded]

    E[Local monitor: last trade vs prior close] --> F{evaluate_local_trigger true?}
    F -- Yes --> G{SIP indicator also ON?}
    F -- No --> H[No action]
    G -- Yes --> I[Consistent - no action]
    G -- No --> J[ESCALATE: possible stale or missing SIP feed]
    J --> K[Do NOT relax the gate on a local signal alone]

    C --> L[Price test enforced: SHORT must be priced above NBB]
    L --> M{Indicator turns OFF?}
    M -- Yes --> N[deactivate_rule_201_ssr]
    M -- No --> L
```

`effective_through` is left unset by default. Rule 201(b)(1)(ii) runs the restriction for the
remainder of the trigger day *and the following day*, which needs a trading calendar this
module does not own. A guessed calendar that lifts the restriction early permits a prohibited
short sale; one that lifts it late only costs fills. Set `effective_through` explicitly when
an authoritative end time is available.

---

## Workflow 3: Locate reservation lifecycle

```mermaid
stateDiagram-v2
    [*] --> Granted: grant_locate (duplicate locate_id refused)
    Granted --> Reserved: validate_order_intent approves a short sale
    Reserved --> Released: release_locate_reservation (cancel / venue reject / session drop)
    Reserved --> Consumed: order fills
    Released --> Granted: capacity returned to the pool
    Granted --> Expired: past expires_at (firm policy TTL)
    Expired --> [*]
```

Notes on the transitions:

- **Reserve on approval, not on fill.** Between approval and fill the capacity must not be
  offered to another order.
- **Release is mandatory for anything that is not a fill.** A pool that only ever decreases
  silently starves the desk for the rest of the locate's life.
- **A released order ID cannot re-reserve.** Re-submitting it returns the original approval.
  Reusing locate capacity for a genuinely new short sale is only permissible under the narrow
  conditions in SEC Reg SHO FAQ 4.4 — quantity no greater than the original locate, locate good
  for the whole trading day — and never for threshold or hard-to-borrow securities. That is a
  firm policy decision with its own supervisory record, made outside this gate.
- **`RegSHOError` is for this lifecycle only.** Releasing an unknown order, releasing twice, or
  re-granting an existing `locate_id` raises. The order path never raises: it returns a
  non-compliant result so the rejection is logged and auditable.

---

## Workflow 4: Failure-mode drills to run before go-live

| Scenario | Expected gate behaviour |
| :--- | :--- |
| NBBO feed returns `0.0` or drops out while a restriction is in force | Reject the short sale; do not treat an absent bid as an unrestricted one |
| Price or NBB arrives as `NaN` / `Inf` | Reject; a NaN comparison silently satisfies the price test |
| Pre-trade check times out and the caller retries | Original decision returned, locate reserved exactly once |
| Same `order_id` resubmitted with a larger quantity after approval | Rejected as a duplicate with different terms |
| Rejected order resubmitted after its locate is granted | Evaluated afresh and approved; rejections reserve nothing and are not cached |
| `order_id` blank, whitespace, or not a string | Rejected; a reservation cannot be keyed or audited without one |
| Venue rejects an approved short sale | `release_locate_reservation` restores capacity |
| Locate feed supplies naive (non-UTC-aware) expiry timestamps | Interpreted as UTC; no exception on the order path |
| Locate granted twice under the same ID | `RegSHOError`; consumed capacity preserved |
| SSR flag set for `tsla` and order arrives for `TSLA` | Restriction applies (symbol comparison is case-normalised) |
| Order quantity of `0` or a negative number | Rejected; the pool is never credited |
