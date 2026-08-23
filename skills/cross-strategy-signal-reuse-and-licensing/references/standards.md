# Standards for Cross-Strategy Signal Reuse and Licensing

## Engineering standards enforced by `scripts/`

| Metric | Engineering Standard |
|---|---|
| AUM Capacity Cap | Total ACTIVE subscribed AUM across strategy pods MUST NOT exceed `max_aum_capacity_usd`. Denied requests are not recorded; revoked subscriptions release their AUM. |
| Fail-Closed Input Validation | NaN, infinite, and negative monetary amounts MUST be rejected at the boundary. `nan > cap` is False, so an unchecked NaN AUM would be granted and would then disable the cap permanently. |
| Unique Entitlement Identity | A `subscription_id` MUST NOT be silently overwritten; re-registering a `signal_id` MUST require an explicit `replace=True`. |
| Entitlement Permissioning | Signal data feeds MUST block un-licensed strategy pod IDs, and revoked subscriptions MUST NOT be billed. |
| Fee Schedule | `Fee = base_fee + pnl_share_pct * max(0, realized_pnl - loss_carryforward)`, with the residual carryforward returned for the next period. |
| Pricing Basis Disclosure | The engine MUST NOT assert arm's-length compliance. It records only whether a benchmarking reference was attached (`arm_length_documented`) plus the `pricing_basis` note. |

## Transfer pricing sources (verified)

All references below are to the **OECD Transfer Pricing Guidelines for Multinational
Enterprises and Tax Administrations, January 2022 edition** ("TPG 2022"), which is
the operative version. Applicability is jurisdiction-dependent: the TPG are
guidance to tax administrations and taxpayers, given legal effect only through each
country's domestic transfer pricing rules, which vary in scope, thresholds,
deadlines and penalties. Nothing here is tax advice.

| Claim | Source | Relevance |
|---|---|---|
| Low value-adding intra-group services are supportive, **not part of the core business**, and neither require nor create unique and valuable intangibles. | TPG 2022 para 7.45 | A proprietary alpha signal fails this definition on all three limbs. |
| The simplified approach explicitly does **not** cover *services constituting the core business of the MNE group* or *research and development services*. | TPG 2022 para 7.47 | Signal research is R&D output and core business for a quant firm — the simplified approach is unavailable. |
| The simplified-approach mark-up "shall be equal to 5% of the relevant cost". | TPG 2022 para 7.61 | This 5% safe harbour applies **only inside** the simplified approach, so it must not be used to price a signal licence. |
| Cost pools must exclude shareholder activities benefiting only the performing company. | TPG 2022 para 7.56 | Group-level research that benefits only the owner entity is not chargeable out. |
| Intangibles guidance and the DEMPE framework (development, enhancement, maintenance, protection, exploitation) determine which entity is entitled to the return from an intangible. | TPG 2022 Chapter VI | Determines whether the research hub or the consuming desk is entitled to the signal's residual return. |
| Three-tiered documentation: master file, local file, and country-by-country report (introduced by BEPS Action 13). | TPG 2022 Chapter V | The intercompany agreement and fee records this engine produces feed the local file. |

Primary text: <https://www.oecd.org/en/publications/oecd-transfer-pricing-guidelines-for-multinational-enterprises-and-tax-administrations-2022_0e655865-en.html>

**Currency note.** The OECD released a public consultation document proposing
revisions to Chapter VII (intra-group services) on 1 June 2026, with comments due
22 July 2026 and a public consultation meeting scheduled for November 2026. That
document states it does not represent a consensus view of the Committee on Fiscal
Affairs. TPG 2022 remains operative; re-check Chapter VII paragraph numbering
before relying on 7.45 / 7.47 / 7.61 after any revision is adopted.

## External data licensing (out of scope for this engine)

Where a reused signal is derived from third-party or exchange data, redistribution
between group entities and business units, derived-data creation, and non-display
use are separately licensed under the relevant vendor or venue market data policy,
and are commonly subject to audit. Terms differ materially per venue — read the
specific agreement rather than generalising. See
`market-data-entitlement-and-licensing-per-venue` and
`data-vendor-contractual-usage-restriction-tracking`.
