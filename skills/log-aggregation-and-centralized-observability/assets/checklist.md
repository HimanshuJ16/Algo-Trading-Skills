# Pre-Flight Checklist

- [ ] Are logs formatted in structured OpenTelemetry-compliant JSON?
- [ ] Is sensitive key redaction (`api_key`, `secret`, `private_key`) verified?
- [ ] Is correlation ID included across all microservice log events?
- [ ] Is error velocity spike detection enabled ($> 10\text{ errors/window}$)?
