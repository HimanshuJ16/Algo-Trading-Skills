# Broker Integration Standards — broker-api-idempotent-cancel-requests

| Response Code / Condition | Status Enum | Description |
|---|---|---|
| HTTP 200 / 202 / 204 | `CANCELLED` | Order successfully cancelled on exchange |
| HTTP 400 "already filled" | `FILLED_BEFORE_CANCEL` | Cancel-vs-Fill race condition |
| HTTP 404 / 400 "not found" | `ALREADY_CANCELLED` | Order was previously cancelled or expired |
| HTTP 5xx / Connection Error | `FAILED` / Retry | Gateway error requiring exponential backoff retry. Fails after max retries. |

## Category

`broker-integration` — see top-level `mappings/` directory.

## Institutional Standards (FIX Context)
- Analogous to issuing `OrderCancelRequest` (35=F) with a new `ClOrdID` while referencing `OrigClOrdID`.
- System must gracefully handle `OrderCancelReject` (35=9) when the reason is "Too Late to Cancel".
- Must reconcile local order state exclusively through incoming `ExecutionReport` (35=8) updates rather than presuming outcome based purely on cancel request dispatch.
