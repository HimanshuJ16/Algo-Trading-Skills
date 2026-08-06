# Institutional Crypto Custody Transfer Verification Standards

## 1. Dust Test Transaction Thresholds & Policies
| Asset | Chain | Min Confirmations | Dust Test Amount | Mandatory Destination Tag |
| :--- | :--- | :--- | :--- | :--- |
| **BTC** | Bitcoin Mainnet | 2 Blocks (~20 mins) | 0.0001 BTC | No |
| **ETH** | Ethereum Mainnet | 12 Blocks (~3 mins) | 0.001 ETH | No |
| **USDT / USDC** | Ethereum (ERC-20) | 12 Blocks (~3 mins) | 1.0 USDT/USDC | No |
| **SOL** | Solana Mainnet | 32 Slots (Finalized) | 0.01 SOL | No |
| **XRP** | Ripple Ledger | 1 Ledger (~4 secs) | 1.0 XRP | **Yes** (Destination Tag) |
| **TON** | TON Mainnet | 10 Blocks (~50 secs) | 0.1 TON | **Yes** (Comment/Memo) |

## 2. Threshold Trigger Logic
- **Default Policy Threshold**: Any transaction with value `≥ $50,000 USD` (or equivalent native token value) **must** undergo test transaction verification prior to primary transfer authorization.
- **Micro-Transfer Size**: Dust test amount must be non-zero and above dust relay fees, but strictly minimal (< $5.00 USD value).
- **Time-Decay Expiry**: Authorization granted following a successful test transaction expires after **30 minutes**. If the primary transfer is not submitted within 30 minutes, a fresh test transaction must be executed.

## 3. Security & Address Validation Requirements
- **Strict Address Whitelisting**: All recipient addresses must be pre-approved in an HSM/MPC whitelisting directory. Unwhitelisted addresses are immediately rejected before test transaction generation.
- **Checksum Verification**: Address strings must pass cryptographic checksum verification (EIP-55 for Ethereum, Base58Check for Bitcoin, Bech32 for Cosmos/Bitcoin).
- **Destination Tag / Memo Integrity**: For shared deposit addresses (exchanges/custodians), missing destination tags MUST trigger pre-flight validation errors to prevent lost funds.