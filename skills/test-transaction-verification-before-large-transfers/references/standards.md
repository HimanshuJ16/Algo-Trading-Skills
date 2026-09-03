# Test Transaction Verification — Standards and Sourced Parameters

## 1. What actually mandates this control

There is **no regulation and no standard that mandates a dust test transaction**.
The technique is an industry practice. What a standard *does* require is the
control class it belongs to — verifying the destination and amount out of band
before key material is used.

| Source | Reference | What it says | Applicability |
| :--- | :--- | :--- | :--- |
| CCSS v9 (C4) | `1.05.8` Spend Verification, sub-requirement `1.05.8.1` | "Verification of fund destinations and amounts is performed via Approved Communication Channels prior to the use of key material." | **Level II and above.** Not a Level I requirement. It mandates out-of-band verification, **not** the dust-test technique specifically — a test transfer is one way to satisfy it, an authenticated callback confirming the address is another. |
| Fireblocks (vendor guidance) | Digital Asset Security Series — test transfers, whitelisting, hardware wallets | Describes the operational runbook: share the deposit address, send a small amount, **the operations team contacts the counterparty to confirm the test transfer has been received**, and only then complete the full transaction. Puts the round trip at ~15–30 minutes. | Vendor best-practice guidance, not a rule. The counterparty-confirmation step is the load-bearing one. |

**Documented limitations, from the same Fireblocks source** — carry these into any
risk write-up rather than claiming the control eliminates address risk:

- Malware exists that "allows the initial test transfer to work, *then* replaces
  the deposit address with their own."
- The confirmation channel itself is attackable via "deposit address spoofing or
  man-in-the-middle attacks."
- An approved address entered against the **wrong blockchain** still causes
  permanent loss.

This is why `TransferVerificationEngine` binds the test transaction to the
request's recipient *and chain*, and re-checks that binding, the whitelist, and
the window at the final authorisation gate rather than trusting the earlier pass.

## 2. Confirmation depth is venue policy, not a protocol constant

Do not treat the numbers below as a standard. Published requirements for the same
asset differ materially between venues, and a value copied from the wrong venue is
either an unnecessary delay or an under-protected release. **Source
`min_confirmations` from the custodian or venue you are actually crediting at.**

Observed spread, as of 2026-09:

| Asset | Kraken | Coinbase |
| :--- | :--- | :--- |
| BTC | 4 confirmations (~40 min, stated) | 2 confirmations |
| ETH | 20 confirmations | 14 confirmations |

### Probabilistic depth vs. deterministic finality

These are different guarantees and the same integer means different things:

| Chain | Nature of settlement | Notes |
| :--- | :--- | :--- |
| **Bitcoin** | Probabilistic. Depth only ever reduces re-org probability; it never reaches zero. | Blocks target ~10 min, but inter-block time is exponentially distributed — a single block can take 30–60 minutes. Do not convert a confirmation count into a promised wall-clock time. |
| **Ethereum (post-Merge)** | Probabilistic *below* finality; **deterministic at finality**. | A block is finalised after two epochs — 64 slots at 12 s, ≈ **12.8 minutes**. Reverting a finalised block requires ≥ 1/3 of validators to be slashed. The commonly used "12 blocks" (~2.5 min) is a pre-Merge probabilistic heuristic and is **not** finality; most venues still use a depth heuristic rather than waiting for finality. Choose deliberately. |
| **Solana** | Deterministic once rooted. | A block is rooted/finalised once it has accumulated votes across **32 consecutive slots** (maximum lockout). Query at the `finalized` commitment; `confirmed` is weaker. |
| **XRP Ledger** | Deterministic on inclusion. | A transaction with a `tesSUCCESS` or `tec` result in a **validated ledger** is irrevocable. One validated ledger is sufficient; ledgers close every few seconds. |
| **TON** | Deterministic on masterchain inclusion. | Finality on **one masterchain block**, sub-second. There is no meaningful multi-block confirmation count; treat `min_confirmations = 1` against masterchain inclusion. |

## 3. Dust test amount

Two constraints pull in opposite directions, and the resolution is per-asset, not
a single USD rule.

- **Floor — the transfer must actually relay.** Bitcoin Core's default
  `dustRelayFee` is 3,000 sat/kvB (3 sat/vB), which puts the dust threshold at
  **546 sat for P2PKH**, **294 sat for P2WPKH**, and **330 sat for P2WSH and
  P2TR**. An output below the threshold for its type will not relay. Other chains
  have their own minimum-transfer and account-reserve rules (XRPL's base reserve,
  for example) that a dust amount must clear.
- **Ceiling — the test should be cheap enough that losing it does not matter.**
  Keep the notional small relative to the transfer being gated. Note that a fixed
  native-token amount is **not** a fixed USD amount: 0.0001 BTC is roughly $10 at
  a $100,000 BTC price and roughly $2 at $20,000. If your policy states a USD
  ceiling, derive the per-asset amount from the current price rather than pinning
  a native-token constant and assuming it stays under the ceiling.

`AssetConfig.test_amount` rejects zero and negative values: a zero-value transfer
confirms on-chain while moving nothing, leaving the counterparty with nothing to
acknowledge.

## 4. Address handling

- **Whitelisting.** Recipient addresses should originate from an HSM/MPC address
  book with its own approval quorum — Fireblocks, for instance, requires Admin
  Quorum approval for each whitelisting request. Whitelisting is the control that
  survives key compromise; an attacker holding signing capability still cannot
  reach an address that was never approved.
- **Canonicalisation is encoding-specific.** EVM hex is case-insensitive (ERC-55
  capitalisation is purely a checksum) and bech32/bech32m forbids mixed case, so
  both fold safely to lowercase. **Base58Check (Bitcoin legacy, XRP), Solana
  base58 and TON base64 are case-sensitive** and must be compared byte-exact.
  `canonicalize_address` implements exactly this split.
- **Checksum validation is out of scope for this module.** It matches addresses
  against a whitelist; it does not verify EIP-55, Base58Check, or bech32
  checksums. Validate those at address-book entry, where a bad address can be
  rejected before anyone approves it.

## 5. Destination tags and memos

Shared custodian deposit addresses route funds to the right customer by tag/memo.

- **XRP Ledger.** A destination tag is a 32-bit unsigned integer identifying the
  beneficiary. An account may set the `RequireDest` (`asfRequireDest`) flag, after
  which the ledger rejects incoming payments with no tag — but the flag is
  **opt-in**. Without it, an untagged payment succeeds on-chain and arrives
  uncredited, which is the failure mode worth gating against.
- **Also tag/memo-bearing:** XLM (memo), TON (comment), EOS (memo).
- **BNB is no longer in this category.** The memo-bearing BNB Beacon Chain
  (BEP-2) was shut down at the Fusion hardfork on **2024-11-19**; BEP-20 on BNB
  Smart Chain is EVM and carries no memo. Guidance that still lists BNB as
  requiring a memo is out of date.

## Sources

- CryptoCurrency Certification Consortium, CCSS v9 requirements matrix — https://cryptoconsortium.org/ccss-table-v9/
- Fireblocks, "Mitigating Crypto Deposit Risk With Test Transfers, Address Whitelists, and Hardware Wallets" — https://www.fireblocks.com/blog/digital-asset-security-series-mitigating-deposit-address-risk-with-test-transfers-whitelisting-and-hardware-wallets
- Fireblocks Developer Docs, whitelisted addresses — https://developers.fireblocks.com/docs/whitelist-addresses
- Kraken, cryptocurrency deposit processing times and confirmation requirements — https://support.kraken.com/articles/203325283-cryptocurrency-deposit-processing-times
- Coinbase, "Announcing new confirmation requirements" — https://www.coinbase.com/blog/announcing-new-confirmation-requirements
- Bitcoin Core, `src/policy/policy.cpp` (dust threshold and `dustRelayFee`) — https://github.com/bitcoin/bitcoin/blob/master/src/policy/policy.cpp
- XRP Ledger docs, finality of results — https://xrpl.org/docs/concepts/transactions/finality-of-results
- XRP Ledger docs, source and destination tags — https://xrpl.org/docs/concepts/transactions/source-and-destination-tags
- Solana docs, terminology (root, finality, maximum lockout) — https://solana.com/docs/references/terminology
- TON docs, payment processing overview (masterchain finality) — https://docs.ton.org/applications/payments/overview
- BNB Chain, final sunset plan of BNB Beacon Chain — https://www.bnbchain.org/en/blog/final-sunset-plan-of-bnb-beacon-chain
