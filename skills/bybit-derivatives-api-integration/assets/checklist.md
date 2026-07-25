# Pre-Flight Checklist

- [ ] Is the server's time synchronized via an NTP daemon (e.g., `chronyd` or `ntpd`)?
- [ ] Are the API Keys strictly IP-whitelisted to the production servers?
- [ ] Has the HMAC signature logic been tested against both GET (query string) and POST (JSON body) formats?
- [ ] Is rate-limit tracking implemented to automatically backoff if `X-Bapi-Limit-Status` drops below 10?
