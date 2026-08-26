# Standards for Hot Wallet Key Rotation

## What is actually mandated — and what is not

**No standard reviewed here mandates a 90-day hot wallet key rotation, a 100,000-signature
ceiling, or a $10M signed-volume ceiling.** Version 1.0.0 of this skill asserted all three
as requirements ("MUST be rotated at least every 90 days", "MUST be rotated after 100,000
signatures"). Those claims were unsourced and are withdrawn.

| Source | What it actually says about rotation cadence |
|---|---|
| NIST SP 800-57 Pt 1 Rev. 5 | Private signature key: "a maximum cryptoperiod of about **one to three years** is recommended." Figures are "only rough order-of-magnitude guidelines." |
| CCSS v9.0 | **No** rotation interval, maximum key age, or signature-count requirement. |
| PCI DSS v4.0 Req. 3.7.4 | Cryptoperiod is "as defined by the associated application vendor or **key owner**" — the org sets it. |
| AWS Config `ACCESS_KEYS_ROTATED` | `maxAccessKeyAge` **default 90** days, configurable. Applies to IAM access keys. |

The 90-day figure in wide circulation is most plausibly traceable to the AWS Config default
above, and to the older password-expiry convention — not to any key-management standard.

## Engineering standards enforced by this skill

| Metric | Engineering standard | Basis |
|---|---|---|
| Max key age | Default 90 days for an online key. Configurable. | Engineering default. Shorter than NIST's 1–3 years, justified by SP 800-57 §5.3.1 factors 2, 3 and 5 (software embodiment, exposed environment, transaction volume). |
| Max signatures | Default 100,000. Configurable. | Engineering default. Operational blast-radius cap only — see the nonce-bias note below. |
| Max signed volume | Default $10M. Configurable. | Engineering default. No external basis. |
| Grace period | Default 24 hours dual-key overlap. Configurable. | Engineering default. Floor is the settlement/finality horizon of the venues in use, not the calendar. |
| Destruction | A private signature key MUST be destroyed at the end of its cryptoperiod. | NIST SP 800-57 §5.3.6(1)(b): "A private signature key **shall** be destroyed at the end of its cryptoperiod." |
| Sweep-before-shred | An `ONCHAIN_SIGNING` key MUST NOT reach `REVOKED_SHREDDED` while `residual_balance_usd > 0`. | Domain invariant: a blockchain key is irrevocable, so destruction with a live balance is unrecoverable loss. Consistent with CCSS key-compromise requirements framed around sending funds to newly-generated wallets. |
| Compromise handling | A compromised key MUST NOT receive a grace period. | Engineering standard. A drain window assumes only authorised work remains in flight; that assumption fails under active attack. |
| Timestamp domain | Creation/use timestamps MUST be POSIX epoch **seconds**, and a future-dated creation beyond clock-skew tolerance MUST fail loudly. | Fail-open defect: clamping a future date to age 0 reports an arbitrarily old key as healthy indefinitely. |

## Verified source material

### Cryptoperiods for signature keys (primary source)

NIST Special Publication 800-57 Part 1 Revision 5, *Recommendation for Key Management:
Part 1 – General* (May 2020).

- §5.3.6(1)(b), Private signature key: "Given the use of an approved algorithm and key size
  as well as an expectation that the security of the key-storage and use environment will
  increase as the sensitivity and/or criticality of the processes for which the key provides
  integrity protection increases, **a maximum cryptoperiod of about one to three years is
  recommended. A private signature key shall be destroyed at the end of its cryptoperiod.**"
- Table 1, *Suggested cryptoperiods for key types*, row 1: "Private Signature Key — **1 to 3
  years**" (originator-usage period).
- §5.3.6 preamble: "the cryptoperiods suggested are **only rough order-of-magnitude
  guidelines**; longer or shorter cryptoperiods may be warranted depending on the application
  and environment in which the keys will be used."
- §5.3.1, *Factors Affecting Cryptoperiods*, includes: "(2) The embodiment of the mechanisms
  (e.g., a FIPS 140 Level 4 implementation or a software implementation on a personal
  computer); (3) The operating environment ...; (5) **The volume of data flow or the number
  of transactions**; ... (7) Limitations required for algorithm usage (e.g., the maximum
  number of invocations to avoid nonce reuse)."
- §5.3.2: "short cryptoperiods **may be counter-productive**, particularly where
  denial-of-service is the paramount concern and there is a significant potential for error
  in the re-keying or key-derivation process."

URL: https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf

Factors 2, 3 and 5 are what justify running an online hot wallet key materially shorter
than the 1–3 year baseline. Factor 7 is the only place NIST ties a count to a key's life,
and it concerns nonce reuse in the algorithm — not an operational transaction budget.

### Crypto-specific custody standard

CryptoCurrency Security Standard (CCSS) v9.0, CryptoCurrency Certification Consortium (C4).
Aspects: 1.01 Key Material Generation, 1.02 Wallet Generation, 1.03 Key Material Storage,
1.04 Key Material Access, 1.05 Key Material Usage, 1.06 Data Sanitization Documentation,
2.01 Security Tests/Audits, 2.02 Log and Monitor, 2.03 Governance and Risk, 2.04 Key
Compromise Documentation.

CCSS v9.0 contains **no** key rotation interval, maximum key age, or signature-count
requirement. Its key-compromise requirements sit in **2.04**, requiring a documented Key
Compromise Policy covering "each specific classification of key material used throughout
the CCSS Trusted Environment; a detailed plan of dealing with its compromise". Earlier CCSS
guidance framed compromise response around regenerating keys and wallets and **sending funds
to the newly-generated wallets** — the fund-sweep step this engine gates on.

URL: https://cryptoconsortium.org/cryptocurrency-security-standard-documentation/ccss-details-v9/

*Confidence note: the aspect list and the 2.04 wording above come from the official C4
documentation page as retrieved during this audit. CCSS is versioned and requirement numbers
move between revisions — cite the version you audited against.*

### Where 90 days comes from

AWS Config managed rule `ACCESS_KEYS_ROTATED`: "Checks if active IAM access keys are rotated
(changed) within the number of days specified in `maxAccessKeyAge`. The rule is NON_COMPLIANT
if access keys are not rotated within the specified time period. **The default value is 90
days.**" Parameter `maxAccessKeyAge`, Type: int, Default: 90.

URL: https://docs.aws.amazon.com/config/latest/developerguide/access-keys-rotated.html

This is a configurable default for cloud IAM access keys. It is not a wallet key requirement,
and AWS presents it as best-practice guidance rather than a hard limit.

### Payment-industry treatment of cryptoperiods

PCI DSS v4.0 Requirement 3.7.4 requires key-management policies covering "cryptographic key
changes for keys that have reached the end of their cryptoperiod, as defined by the
associated application vendor or key owner, and based on industry best practices and
guidelines, including a defined time period for each key type in use." Requirement 3.7.5
covers retirement, replacement or destruction at end of cryptoperiod.

Even the most prescriptive widely-cited payment standard therefore delegates the *length* of
the cryptoperiod to the key owner. PCI DSS scope is cardholder data; it is cited here as
evidence about how cryptoperiods are set, not as a rule applying to digital asset custody.

### Why a signature ceiling is not a cryptographic control

The failure mode a count-based trigger is often assumed to prevent — key recovery from
accumulated signatures — is driven by nonce quality, not volume:

- With biased or repeated ECDSA nonces, the private key is recoverable via lattice reduction
  (Hidden Number Problem / LLL) from a very small number of signatures. Reported figures are
  on the order of 2–3 signatures for heavily biased nonces and a few hundred for smaller
  biases on a 256-bit curve — orders of magnitude below any 100,000 threshold. Two signatures
  sharing an identical nonce leak the key algebraically.
  Source: Breitner & Heninger, "Biased Nonce Sense: Lattice Attacks against Weak ECDSA
  Signatures in Cryptocurrencies", IACR ePrint 2019/023 — https://eprint.iacr.org/2019/023.pdf
- With deterministic nonces there is no accumulation risk to bound: RFC 6979 derives the
  ECDSA nonce from the key and message hash, and Ed25519 (RFC 8032) is deterministic by
  construction.

A signature ceiling is therefore an **exposure cap** — bounding how much a single compromised
key could have authorised — and should be documented as such. It does not substitute for a
sound RNG or a deterministic signature scheme.

*Note: a hard, standards-defined signature-count limit does exist for **stateful hash-based**
signatures (LMS/XMSS, NIST SP 800-208), where each one-time key may be used exactly once.
That is a different key type from the ECDSA/Ed25519 keys this skill covers, and is out of
scope here.*

### Settlement horizons that set the grace-period floor

Ethereum proof-of-stake: "Time in proof-of-stake Ethereum is divided into slots (12 seconds)
and epochs (32 slots)", with finalisation occurring across two consecutive epochs — roughly
13 minutes. A transaction may remain unmined in the mempool considerably longer, and that,
not finality alone, bounds how long work authorised under an old key can still land.

URL: https://ethereum.org/developers/docs/consensus-mechanisms/pos/

Bitcoin confirmation is probabilistic rather than final; conventional practice waits for
several blocks. Exchange settlement and reconciliation windows are venue-specific and are
frequently the longest of the three. Size `grace_period_hours` above the slowest path in use.

## Limitations of this reference

Jurisdiction-specific custody rules (NYDFS 23 NYCRR Part 200/500, MiCA, MAS PSN02, VARA) were
not surveyed for this skill and may impose their own key-management obligations on a licensed
entity. Multi-signature and MPC resharing procedures, HSM-internal key lifecycle, certificate
lifetimes, and post-quantum migration timelines are all out of scope. Nothing here is legal
or compliance advice; a regulated custodian should confirm its obligations against its own
licence conditions.
