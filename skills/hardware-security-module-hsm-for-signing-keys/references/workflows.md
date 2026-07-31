# Workflows for HSM Signing Key Management

1. **HSM Slot & Session Initialization**:
   - Open PKCS#11 session with HSM token and authenticate pin.
2. **Payload Ingestion & Hashing**:
   - Compute SHA256 / Keccak256 hash of transaction data.
3. **Hardware Enclave Signing**:
   - Execute signature generation inside FIPS 140-2 Level 3 hardware enclave.
4. **Audit Logging**:
   - Record signature audit log with key alias, hash, and caller identity.