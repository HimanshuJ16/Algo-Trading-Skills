# Workflows for Air-Gapped Signing in Cold Storage

## Institutional Treasury Transfer Pipeline

1. **Create Intent**: The `OnlineCoordinator` generates a validated, versioned `UnsignedPayload`, assigns a bounded nonce, computes its canonical representation and retains the issued payload identity.
2. **Export**: The online system transfers the canonical payload through a QR code or clean, inspected SD card. Record media custody and prohibit USB, Bluetooth, Wi-Fi, and cellular bridges.
3. **Inspect Offline**: Authorized personnel verify the medium and load it only on the offline signer. The signer rejects malformed, unsupported, or unexpected payloads.
4. **Clear Sign**: The offline vault displays the exact destination, amount, network, and nonce. Personnel compare the display against the approved instruction and explicitly approve or reject it.
5. **Sign**: The isolated signer creates an envelope bound to the canonical payload and its hash. The reference implementation uses an educational deterministic signature seam; production systems must use audited chain-native cryptography.
6. **Return**: Export the signed envelope via the controlled medium, inspect it, and return it to the online coordinator.
7. **Verify**: The coordinator parses the returned payload, recomputes the hash, confirms it was issued by this coordinator, checks the signer key identifier, and independently verifies the signature. Any mismatch is rejected and escalated.
8. **Broadcast and Reconcile**: Call the production RPC/broadcast adapter only after verification. Record the payload identity and prevent duplicate submission. If the RPC result is ambiguous, stop retries and reconcile against authoritative chain state.

## Failure and Recovery Boundaries

- Malformed media or unknown payload: quarantine the medium, reject the payload, and issue a new intent after investigation.
- Destination, amount, network, nonce, hash, or signer mismatch: do not sign or broadcast; require dual-control review.
- Lost or damaged media: invalidate the intent according to the durable coordinator record and create a replacement nonce.
- Ambiguous broadcast result: treat the state as unknown, query chain/RPC evidence, and never assume failure solely because a response was lost.
- Suspected key or device compromise: stop signing, preserve evidence, invoke incident response, and rotate/quarantine keys under the custody policy.
