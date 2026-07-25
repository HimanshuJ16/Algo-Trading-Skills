# Workflows for Air-Gapped Signing in Cold Storage

## Institutional Treasury Transfer Pipeline

1. **Transaction Initialization**: The `OnlineCoordinator` (e.g., automated trading bot requiring capital rebalancing) generates an `UnsignedPayload`.
2. **Air-Gap Export**: The online server displays the payload as a QR code or writes it to a clean SD card.
3. **Physical Verification**: Authorized personnel take the SD card to the secure offline vault facility.
4. **Clear Signing**: The `OfflineAirGappedSigner` decodes the payload. The trusted hardware screen displays exactly how much crypto is moving and to where.
5. **Cryptographic Signing**: The personnel approve the transaction on the offline hardware. It signs the payload using the master private key.
6. **Air-Gap Import**: The signed payload is exported back via QR/SD card and scanned by the `OnlineCoordinator`.
7. **Broadcast**: The coordinator pushes the signed transaction to the RPC node.