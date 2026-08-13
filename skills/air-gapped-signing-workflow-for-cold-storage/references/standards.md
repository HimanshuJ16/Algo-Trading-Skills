# Standards for Air-Gapped Signing Workflow

| Standard | Description |
|---|---|
| **Physical Isolation** | The offline signer must physically lack Wi-Fi, Bluetooth, and cellular modems. Disable or remove network-capable peripherals. |
| **Clear Signing Mandate** | The signer must parse and display the exact human-readable destination, amount, network, and nonce before approval. Blindly signing hashes is forbidden. |
| **Controlled Transfer Media** | Use QR codes or clean, inspected SD cards with chain-of-custody controls. USB, Bluetooth, Wi-Fi, and cellular bridges are prohibited. |
| **Canonical Payloads** | Use versioned, deterministic serialization and a stable payload hash. Reject malformed, unknown-field, or mismatched payloads. |
| **Independent Verification** | The online coordinator must verify payload binding, signer identity, and the signature independently before invoking a broadcast adapter. |
| **Replay Protection** | Track issued and consumed payload identities durably in production; reject duplicate broadcasts and reconcile ambiguous RPC outcomes. |
| **Production Cryptography** | The reference module's deterministic HMAC-style primitive is educational only. Production custody requires audited chain-native cryptography and hardware-wallet/HSM key isolation. |
| **Separation of Duties** | Separate intent creation, physical media handling, offline approval, and broadcast/reconciliation responsibilities. |

## Category
`crypto-custody-security`
