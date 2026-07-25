# Workflows for Bybit V5 Integration

1. **API Key Generation**: Generate a strict, IP-whitelisted API key in the Bybit console. Ensure it is configured for Unified Trading or Contract trading, depending on account type.
2. **Environment Selection**: Test all logic against `api-testnet.bybit.com` before pointing to `api.bybit.com`.
3. **Payload Construction**: 
   - Extract current timestamp in milliseconds.
   - For POST (orders), serialize payload to JSON without spaces (e.g., `json.dumps(payload, separators=(',', ':'))`).
4. **Signing**: Hash the payload string with the API Secret using HMAC-SHA256.
5. **Header Injection**: Inject `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-SIGN`, and `X-BAPI-RECV-WINDOW` into the HTTP request.
