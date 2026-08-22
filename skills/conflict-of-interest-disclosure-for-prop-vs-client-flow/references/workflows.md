# Workflows for Prop vs. Client Conflict Auditing

Scope: US equities (NMS stocks and OTC Equity Securities), FINRA members. See
`standards.md` for the rule citations behind each step.

## 1. Order input and reference data

- Tag capacity on every order: principal (prop) vs. agency (client), carried outbound on
  FIX `OrderCapacity(528)`.
- Attach to each prop order: `info_barrier_id`, `security_type` (`NMS_STOCK` /
  `OTC_EQUITY`), `trading_unit_type` (`MARKET_MAKING` / `NON_MARKET_MAKING`) and
  `barriers_effective` from the firm's attested barrier inventory.
- Attach to each held client order: `info_barrier_id`, `is_institutional_account`
  (Rule 4512(c)), `negative_consent_disclosed` (written disclosure given at account opening
  and annually) and `opted_in_5320`.

## 2. Fail-closed input validation

Reject, do not skip: unrecognised side, non-finite or non-positive price, non-positive
quantity, unparseable client order. An audit that cannot be completed is a violation
result (`INVALID_ORDER_PARAMETERS`), never an approval.

## 3. Conflict search

Select every unexecuted client order with the same `symbol` and the same normalised side.
Opposite-side and other-symbol orders are out of scope. **Evaluate all of them** — do not
stop at the first.

## 4. Price test (Rule 5320(a), widened by 5320.06)

Let `I` be the Rule 5320.06 minimum price improvement increment for the client limit price,
security type and inside spread.

- `side == BUY`: conflict if `P_prop < P_client_limit + I`
- `side == SELL`: conflict if `P_prop > P_client_limit − I`

The inner comparison (`P_prop <= limit` for BUY, `P_prop >= limit` for SELL) is the set of
prices that *would satisfy* the customer order; the increment extends it to catch de minimis
price improvement. Compare in `Decimal`.

## 5. Exception audit, per conflicting client order

1. **Odd lot (.05)**: client quantity < one round lot → excepted.
2. **No-knowledge (.02)**: barriers effective **and** `prop.info_barrier_id !=
   client.info_barrier_id` **and** not (OTC Equity Security handled by the market-making
   desk) → excepted.
3. **Large order / institutional on negative consent (.01)**: `negative_consent_disclosed`
   **and not** `opted_in_5320`, **and** either the account is institutional under
   Rule 4512(c), **or** `quantity >= 10,000` **and** `quantity × limit >= $100,000`
   → excepted.

Riskless principal (.03) and ISO (.04) are out of scope for the engine; handle them upstream
if the firm relies on them.

## 6. Enforcement

- If any conflicting client order survives the exception audit: `REJECT_PROP_ORDER`, or
  execute the customer order up to its size at the same or better price contemporaneously
  as Rule 5320(a) requires.
- Persist the full audit result — every conflict and every exception applied — for
  supervisory review under Rule 5320.07.
