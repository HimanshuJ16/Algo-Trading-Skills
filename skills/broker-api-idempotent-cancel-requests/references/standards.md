# Broker Integration Standards — broker-api-idempotent-cancel-requests

| Response Code / Condition | Status Enum | Description |
|---|---|---|
| HTTP 200 / 202 / 204 | `CANCELLED` | Order successfully cancelled on exchange |
| HTTP 400 "already filled" | `FILLED_BEFORE_CANCEL` | Cancel-vs-Fill race condition |
| HTTP 404 / 400 "not found" | `ALREADY_CANCELLED` | Order was previously cancelled or expired |
| HTTP 5xx / Connection Error | `FAILED` | Gateway error requiring backoff retry |

## Category

`broker-integration` — see top-level `mappings/` directory.
