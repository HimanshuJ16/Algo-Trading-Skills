# Workflows — IBKR Global Multi-Exchange Routing

The whole procedure has one shape: **screen locally, resolve remotely, submit on `conId`.**
Steps 1–7 are the screen. Step 8 is the gate. Skipping step 8 turns every local rule into a
guess.

## 1. Ingest the payload

Build `IbkrContractSpec` (`symbol`, `sec_type`, `currency`, `exchange`, optional
`primary_exchange`, `routing_mode`) and wrap it in `IbkrOrderPayload` with `action`,
`order_type`, `quantity`, `lmt_price`. Both dataclasses are frozen; the audit never mutates
the payload it was given.

Quantity accepts `int`, `float`, `Decimal` or a numeric string, because TWS API v10 types
`totalQuantity` as `Decimal`. An `int`-only field cannot express a fractional-share or forex
size at all.

## 2. Validate the security type

`secType` must be one of the values enumerated in the TWS API Contract reference. The 1.x
engine accepted only `{STK, OPT, FUT, CASH, IND}` and rejected valid futures options,
warrants, bonds, commodities, funds and combos. Rejection status:
`REJECTED_INVALID_SEC_TYPE`.

## 3. Resolve the destination and check it against the stated intent

`Contract.exchange` must be present — `SMART` or a direct venue code.

`routing_mode` is a **local label**, not a wire field, and it exists to catch a config whose
stated intent disagrees with the destination that will actually be sent:

| `routing_mode` | Required `exchange` | On mismatch |
|---|---|---|
| `SMART_BEST_EXECUTION` | `SMART` | `REJECTED_ROUTING_MODE_CONFLICT` |
| `SMART_MAX_REBATE` | `SMART` | `REJECTED_ROUTING_MODE_CONFLICT` |
| `DIRECT_EXCHANGE` | any venue code other than `SMART` | `REJECTED_ROUTING_MODE_CONFLICT` |

`SMART_MAX_REBATE` additionally warns. There is no order-level "maximise rebate" field in
the TWS API: rebate-seeking routing of non-marketable orders is elected at the account/TWS
level under the Cost Plus commission structure. Setting the label alone changes nothing.

## 4. Apply `primaryExchange` hygiene

`primaryExchange` names the contract's **native listing venue** and disambiguates
smart-routed stock contracts. It is not a destination.

- `primary_exchange == 'SMART'` → rejected. A routing destination is never a listing venue.
- Contains a period → trimmed to the part before it, with a warning (`ENEXT` for `ENEXT.BE`).
- Set on a non-stock contract → warning; it carries no meaning there.
- Missing on a smart-routed stock → **warning, not rejection**. IBKR calls it "good practice
  to include for all stocks" but its own `USStockAtSmart` sample omits it entirely.

## 5. Validate the symbol — without rewriting it

Symbol rules are scoped by **security type and venue**, never by currency alone. Keying off
`currency == 'HKD'` drags HKEX derivatives into an equity-code rule and rejects `HSI` on
`HKFE`.

| Case | Rule | Failure |
|---|---|---|
| `CASH` (forex) | 3-letter base currency, different from the quote currency | `REJECTED_INVALID_SYMBOL_FORMAT` |
| `STK`/`WAR`/`FUND` on `SEHK` | 1–5 numeric digits, returned unchanged | `REJECTED_INVALID_SYMBOL_FORMAT` |
| `STK`/`WAR`/`FUND` on `SEHKNTL`/`SEHKSZSE` | 6-digit mainland code | `REJECTED_INVALID_SYMBOL_FORMAT` |
| Everything else | non-empty | `REJECTED_INVALID_SYMBOL_FORMAT` |

**The symbol is never padded, trimmed or reformatted.** IBKR's shipped SEHK sample is
`symbol = "1"` for the security listed under HKEX code 00001. A zero-padded input such as
`00700` passes with a warning — the audit flags the ambiguity rather than picking a side,
because only `reqContractDetails` knows which string IBKR holds.

## 6. Check currency against the listing venue

The venue used for the check is `exchange`, or `primary_exchange` when `exchange == 'SMART'`.
This is what makes the common path — a smart-routed order — actually checkable; validating
only direct venue codes leaves `currency='EUR'` with `primaryExchange='NASDAQ'` passing
silently.

| Situation | Behaviour |
|---|---|
| `secType == 'CASH'` | Skipped with a warning. `currency` is the quote currency of a pair, so no region rule applies. |
| Venue known, currency in its documented set | Pass. `currency_check_performed=True`. |
| Venue known, currency outside its set | `REJECTED_CURRENCY_MISMATCH`. |
| Venue unknown | Warning. `currency_check_performed=False`. Never a rejection — no local table covers 170+ markets. |
| `SMART` with no `primary_exchange` | Warning: the listing venue is unknown locally, so nothing was checked. |

Currency sets are per venue, not per region, because region rules break on real instruments:
CNH dual counters on `SEHK`, CNH Stock Connect lines on `SEHKNTL`/`SEHKSZSE`, CHF SMI
products on Eurex, USD/EUR depositary-receipt lines on `LSE`.

## 7. Validate the order fields

| Check | Failure |
|---|---|
| `action` in BUY/SELL/SSHORT/SLONG (last two warn as institutional-only) | `REJECTED_INVALID_ORDER_PARAMS` |
| `order_type` non-empty (unrecognised types warn, not reject) | `REJECTED_INVALID_ORDER_PARAMS` |
| `quantity` numeric, finite, strictly positive (fractional warns) | `REJECTED_INVALID_ORDER_PARAMS` |
| `lmt_price` present, numeric, finite and positive for `LMT`, `STP LMT`, `LIT`, `LOC`, `REL`, `TRAIL LIMIT` | `REJECTED_INVALID_ORDER_PARAMS` |
| `lmt_price` set on an order type that ignores it | warning |

Direction is carried by `action`, never by a negative size — a negative quantity is a bug in
the caller, not a sell.

## 8. Resolve with `reqContractDetails` and submit on `conId`

This step is not optional, and `report.requires_contract_details_check` is always `True` to
keep it visible.

1. Call `reqContractDetails` with the validated contract.
2. **Exactly one** `ContractDetails` should come back. More than one means the contract is
   still ambiguous — add or correct `primaryExchange`, or narrow another field. Do not pick
   one arbitrarily.
3. Confirm your `exchange` appears in `validExchanges` ("Valid exchange fields when placing
   an order for this contract").
4. If you routed `SMART`, confirm `aggGroup != -1`; contracts that cannot be smart-routed are
   marked with `-1`.
5. Confirm the currency IBKR reports matches the one you sent.
6. Submit using the returned `conId`, which is "the unique IB contract identifier" and
   removes symbol ambiguity entirely.

If step 1 returns error 200 ("No security definition has been found for the request…"), the
contract does not exist as specified. Fix the parameters — do not retry the same payload, and
do not fall back to a different venue automatically.

## Report contract

`IbkrRoutingReport` is frozen and carries, beyond the echoed contract fields:

| Field | Meaning |
|---|---|
| `status` | `IBKR_ROUTING_VALIDATED` or one of the `REJECTED_*` codes above. |
| `warnings` | Advisory findings. **Read these on a pass** — this is where "currency unchecked" and "not a wire field" live. |
| `resolved_venue` | The venue the currency check resolved to, or `""`. |
| `currency_check_performed` | Whether a currency check actually ran. Distinguishes "matched" from "not checked". |
| `requires_contract_details_check` | Always `True`. |

`IBKR_ROUTING_VALIDATED` means *no known-bad parameter was found*. It is never a statement
that the contract exists or that the destination is permitted for it.
