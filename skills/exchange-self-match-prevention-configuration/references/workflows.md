# Workflows for Exchange Self-Match Prevention (SMP)

1. **SMP Registration Verification**:
   - Confirm firm SMP ID is registered with exchange administration.
2. **Order Tag Header Injection**:
   - Inject FIX Tag 7928 (SMP ID) and Tag 8000 (Instruction).
3. **Pre-Trade Collision Check**:
   - Scan resting order book for matching SMP IDs on opposite side.
4. **Collision Action Enforcement**:
   - Execute cancel-resting, cancel-aggressive, or cancel-both behavior.
