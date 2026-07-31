# Standards for HSM Signing Key Management

| Metric | Engineering Standard |
|---|---|
| Hardware Security Standard | HSM MUST be certified FIPS 140-2 Level 3 or Level 4. |
| Key Non-Exportability | Private keys MUST NEVER be exportable from hardware boundaries. |
| API Standard | Inter-service hardware communication MUST use PKCS#11 or vendor CloudHSM SDKs. |