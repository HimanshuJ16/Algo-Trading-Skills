# Pre-Flight Checklist

- [ ] Is FIPS 140-2 Level 3/4 HSM hardware partition initialized?
- [ ] Are private keys flagged non-exportable (`CKA_EXTRACTABLE=False`)?
- [ ] Are signature operations executed strictly inside hardware enclaves?
- [ ] Is every signature request logged in a tamper-evident audit trail?