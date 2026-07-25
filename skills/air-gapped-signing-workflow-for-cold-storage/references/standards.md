# Standards for Air-Gapped Signing Workflow

| Standard | Description |
|---|---|
| **No Network Adapters** | The offline signer hardware must physically lack Wi-Fi, Bluetooth, and Cellular modems. |
| **Clear Signing Mandate** | The offline signer must parse the transaction and display human-readable intent (Dest/Amount). Blindly signing hashes is forbidden. |
| **Transfer Medium** | QR Codes or SD Cards are preferred. USB cables are prohibited due to firmware/bridge exploits. |

## Category
`crypto-custody-security`