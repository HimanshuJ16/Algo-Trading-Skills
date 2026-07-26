# Pre-Flight Checklist

- [ ] Is Tag 1028 (ManualOrderIndicator) explicitly set to `N` for all automated strategies?
- [ ] Are Tag 7928 (SMP ID) and Tag 8000 (SMP Instruction) included on every order message?
- [ ] Is Tag 50 (Operator ID) present and formatted according to CME Rule 576?
- [ ] Is sequence number gap detection implemented and tested with `ResendRequest` (`35=2`)?
