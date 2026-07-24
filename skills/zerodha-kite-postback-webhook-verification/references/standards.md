# Broker & Framework Coverage — zerodha-kite-postback-webhook-verification

| Broker / Webhook Protocol | Signature Specification |
|---|---|
| Zerodha Kite Connect v3 | `SHA-256(order_id + timestamp + api_secret)` |
| Upstox Webhook API | `HMAC-SHA256(payload_body, api_secret)` |
| Fyers Postback API | Token authorization & SHA-256 payload validation |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with web application security standards (OWASP Webhook Security), cryptographic signature verification, and audit logging standards.
