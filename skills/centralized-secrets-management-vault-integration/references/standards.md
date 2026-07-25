# Standards for Secrets Management

| Metric | Engineering Standard |
|---|---|
| No Hardcoding | Source code must NEVER contain API keys, even for testnet/paper environments. |
| In-Memory Cache | Secrets must be cached in memory (RAM). They must not be written to local disk or cache files. |
| Least Privilege Policy | A bot trading on Binance MUST NOT have read access to the Vault path containing Kraken keys. |
| Masking Logs | Custom logging formatters MUST be configured to mask any string matching a known secret format before outputting to stdout/files. |
