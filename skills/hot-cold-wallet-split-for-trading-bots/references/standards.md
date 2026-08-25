# Standards for Hot/Cold Wallet Allocation

## 1. Mandated hot-wallet ceilings (client assets only)

These are binding obligations on **licensed platforms holding client virtual
assets**. They do **not** apply to a proprietary trading treasury. Where one
binds you, set `regulatory_max_hot_ratio` so the ceiling is enforced ahead of
the engine's own operating band.

| Jurisdiction | Instrument | Max hot | Status |
|---|---|---|---|
| Hong Kong | SFC *Guidelines for Virtual Asset Trading Platform Operators* — ≥98% of client virtual assets in cold storage, hot and other storage not to exceed 2% | 2% | In force since 2023-06-01. The SFC's ASPIRe roadmap (2025) signals a move toward outcome-based custody standards; re-verify before relying on the fixed ratio. |
| Japan | Payment Services Act / FSA — ≥95% of customer crypto assets held in offline wallets | 5% | In force. |
| South Korea | Virtual Asset User Protection Act — ≥80% of the economic value of customer virtual assets in cold wallets | 20% | In force since 2024-07-19. Measured monthly against a trailing-year average KRW value, not on an instantaneous snapshot. |

**United States:** no numeric split is prescribed. NYDFS 23 NYCRR Part 200 and
its custodial-structure guidance (industry letters of 2023-01-23 and
2025-09-30) impose custody, segregation, and insolvency-protection duties
without specifying a hot/cold percentage. Any percentage used in a US context
is an internal engineering policy, not a regulatory floor or ceiling.

## 2. Engine defaults — engineering policy, not standards

| Parameter | Default | Basis |
|---|---|---|
| `target_hot_ratio` | 0.15 | Engineering default. **No regulatory basis.** Calibrate against actual working-capital needs. |
| `max_hot_ratio_threshold` | 0.25 | Engineering default. **No regulatory basis.** |
| `min_hot_ratio_threshold` | 0.05 | Engineering default. **No regulatory basis.** |

The band is validated as `min < target < max`. A target at or above the sweep
trigger would propose a rebalance that leaves the treasury still in breach, and
a floor at or above a mandated ceiling would breach that ceiling on every
refill; both are rejected at construction.

## 3. API key permissions

A trading key must hold **no fund-moving permission**. Withdrawal permission is
necessary but not sufficient to check — Binance's *Get API Key Permission*
endpoint (`GET /sapi/v1/account/apiRestrictions`) returns these fund-relevant
booleans:

| Field | Why it matters |
|---|---|
| `enableWithdrawals` | Sends funds to an external address. |
| `enableInternalTransfer` | Moves funds between the operator's own Binance account types. |
| `permitsUniversalTransfer` | Moves funds across Binance products. |
| `ipRestrict` | Network scoping. Binance states an API key cannot initiate withdrawal requests without IP whitelisting, so an unrestricted key is a hardening gap rather than an immediate withdrawal path. |

Field names are Binance-specific; other venues expose equivalent scopes under
different names. Read the permission set from the exchange rather than assuming
it from how the key was created — scope can be changed after issuance.

## Sources

- SFC, *Consultation Conclusions on the Proposed Regulatory Requirements for Virtual Asset Trading Platform Operators* (23CP1) — https://apps.sfc.hk/edistributionWeb/api/consultation/conclusion?lang=EN&refNo=23CP1
- SFC, *Circular to licensed virtual asset trading platform operators on custody of virtual assets* (25EC44, 15 August 2025) — https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/intermediaries/supervision/doc?refNo=25EC44&lang=EN
- NYDFS, *Guidance on Custodial Structures for Customer Protection in the Event of Insolvency* (23 January 2023) — https://www.dfs.ny.gov/industry_guidance/industry_letters/il20230123_guidance_custodial_structures
- NYDFS, *Updated Guidance on Custodial Structures for Customer Protection in the Event of Insolvency* (30 September 2025) — https://www.dfs.ny.gov/industry-guidance/industry-letters/il20250930-updated-guidance-custodial-structures
- Korea FSC, *New Enforcement Decree on the Protection of Virtual Asset Users* — https://www.fsc.go.kr/eng/pr010101/82534
- Binance, *Get API Key Permission* (developer documentation) — https://developers.binance.com/docs/wallet/account/api-key-permission
- Binance, *How to Use an API Key Securely: 5 Tips From Binance* — https://www.binance.com/en/blog/security/how-to-use-an-api-key-securely-5-tips-from-binance-8638066848800196896

The Japan 95% cold-storage figure is widely reported for the Payment Services
Act regime but was verified here against secondary sources only; confirm against
the FSA's own guidelines before relying on it for a licensing decision.
