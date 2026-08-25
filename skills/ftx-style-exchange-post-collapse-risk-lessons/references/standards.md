# Standards for Post-Collapse Exchange Counterparty Risk

## Status of the thresholds in this skill

**The default thresholds are internal risk policy, not regulation.** No regulator
reviewed for this skill sets a 100% Proof of Reserves floor, a 5% native-token
collateral cap, or a 20% single-venue NAV cap, and no regulator reviewed here
requires a centralised venue to publish a Proof of Reserves at all. They are
defaults chosen to sit well inside the concentrations that failed in 2022, and
they are constructor arguments precisely because they are yours to set. Treat any
claim that a venue's PoR publication is "regulated" or "audited" as requiring
separate evidence.

## What the FTX record actually establishes

| Fact | Source |
|---|---|
| Roughly USD 5.8bn of the USD 14.6bn of assets on Alameda Research's balance sheet were tied to FTX's own FTT token, on then-current valuations. | Ian Allison, CoinDesk, 2022-11-02. The report is what triggered the run. |
| FTX customer assets were "routinely accepted and held by Alameda" and commingled with Alameda's funds; Alameda held an undisclosed, effectively unlimited line of credit funded by FTX customers and was exempted from FTX risk controls. | SEC v. Samuel Bankman-Fried, complaint filed 2022-12-13 (SDNY); parallel CFTC complaint of the same date. |
| Customer funds diverted to Alameda, including funds used to repay Alameda's lenders, were collateralised in part by Alameda's FTT holdings. | SEC complaint, 2022-12-13. |
| "Never in my career have I seen such a complete failure of corporate controls and such a complete absence of trustworthy financial information as occurred here." | John J. Ray III, first-day declaration, In re FTX Trading Ltd., Bankr. D. Del., filed 2022-11-17. |

These are the four dimensions the engine scores against: native-token
collateral concentration, absent segregation, unverified reserves, and the
absence of any independent look at the books.

## What Proof of Reserves does and does not establish

**PCAOB, Office of the Investor Advocate — Investor Advisory, 2023-03-08,
"Exercise Caution with Third-Party Verification / Proof of Reserve Reports":**

- "PoR engagements are not audits and, consequently, the related reports do not
  provide any meaningful assurance to investors or the public."
- The reports "purport to provide an asset verification for an asset type at a
  particular moment in time, subject to significant limitations."
- The procedures "likely do not address the crypto entity's liabilities, the
  rights and obligations of the digital asset holders."
- They are not performed under PCAOB auditing standards and the engagements are
  not subject to PCAOB inspection.

**Attestation state is not durable.** Mazars published a PoR statement for
Binance on 2022-12-07, then within days paused proof-of-reserves work for all
crypto clients — citing concerns about how the reports were being understood —
and removed the published reports from its website. `has_independent_attestation`
must describe the position today, not the fact that a report once existed.

This is why the engine scores the *age* of a coverage ratio as its own
dimension, and why an undated ratio is a finding rather than a pass.

## Regulatory touchpoints — jurisdiction matters

**Regulation (EU) 2023/1114 (MiCA).** Title V applies to crypto-asset service
providers from 2024-12-30.

| Provision | Requirement | Bearing on this skill |
|---|---|---|
| Article 70(1) | CASPs holding clients' crypto-assets, or the means of access to them, must make adequate arrangements to safeguard clients' ownership rights, "in particular in the event of insolvency", and to prevent the use of clients' crypto-assets for their own account. | This is the rule the FTX/Alameda commingling would have breached. It is a duty on the CASP, not something this engine measures. |
| Article 70(2)–(3) | Client funds other than e-money tokens must be placed with a credit institution or central bank by the end of the following business day, in an account separately identifiable from the CASP's own. | Segregation is a legal obligation for in-scope CASPs, which is what off-exchange settlement reproduces contractually for out-of-scope venues. |
| Article 75(2), (5) | The custodian must keep a register of positions in each client's name and provide a statement of position at least quarterly. | Supports dating a reserve claim rather than accepting a standing one. |
| Article 75(7) | Crypto-assets held in custody must be legally segregated from the CASP's estate so that "creditors of the crypto-asset service provider have no recourse to crypto-assets held in custody", in particular on insolvency, and must also be operationally segregated. | The nearest thing to a regulatory basis for the bankruptcy-remoteness that OES arrangements aim at — **for CASPs authorised in the EU only**. |

**Scope caveat.** MiCA binds CASPs authorised in the EU. A venue serving you
through an offshore entity is outside that perimeter, and a venue's authorisation
status is a jurisdictional fact to establish separately. This engine does not
model jurisdiction and none of its outputs should be read as a MiCA compliance
determination.

**United States.** There is no equivalent federal segregation regime for spot
crypto venues to cite here. Absence of a rule is not evidence of a safe venue.

## Off-exchange settlement — what the vendors claim

OES arrangements hold trading collateral away from the venue while mirroring it
as credit for trading. Two are named in this skill's metadata; both descriptions
below are **the providers' own claims**, not independent verification, and the
enforceability of any of it turns on the tri-party agreement and the governing
insolvency law.

| Provider | Mechanism as described by the provider |
|---|---|
| **Copper ClearLoop** | Collateral is held in Copper custody and delegated to a connected venue; settlement occurs on Copper's platform rather than on-chain. Copper describes the network as helping institutions *reduce* counterparty exposure — not eliminate it — and lists connected venues including OKX, Coinbase, Bybit, Deribit, Kraken, Bitfinex, Bitget and Gate. |
| **Fireblocks Off Exchange** | Collateral sits in a Collateral Vault Account, an on-chain MPC wallet the trader and the exchange mutually control; an instant credit is issued to the exchange and on-chain settlement rebalances the vault against exchange credit as positions move. |

**Residual exposure OES does not remove**, and which this engine deliberately
scores as a partial (not full) mitigation:

- Unrealised PnL owed by the venue is still a claim on the venue.
- Obligations accrue inside the settlement window; a venue failure mid-window
  leaves an exposure to reconcile.
- The settlement operator and custodian become critical infrastructure, with
  their own operational and insolvency risk.
- The tri-party agreement must actually be enforceable in the relevant
  insolvency forum. That is a legal opinion, not a configuration flag.

## Sources

- Ian Allison, "Divisions in Sam Bankman-Fried's Crypto Empire Blur on His
  Trading Titan Alameda's Balance Sheet", CoinDesk, 2022-11-02 —
  <https://www.coindesk.com/business/2022/11/02/divisions-in-sam-bankman-frieds-crypto-empire-blur-on-his-trading-titan-alamedas-balance-sheet>
- SEC, "SEC Charges Samuel Bankman-Fried with Defrauding Investors in Crypto
  Asset Trading Platform FTX", press release 2022-219, 2022-12-13 —
  <https://www.sec.gov/newsroom/press-releases/2022-219>
- CFTC, "CFTC Charges Sam Bankman-Fried, FTX Trading and Alameda with Fraud and
  Material Misrepresentations", release 8638-22, 2022-12-13 —
  <https://www.cftc.gov/PressRoom/PressReleases/8638-22>
- John J. Ray III, first-day declaration, In re FTX Trading Ltd., Bankr. D. Del.,
  2022-11-17 (widely reported; see CNBC, 2022-11-17) —
  <https://www.cnbc.com/2022/11/17/ftx-ceo-shreds-bankman-fried-never-seen-such-a-failure-of-controls-.html>
- PCAOB Office of the Investor Advocate, Investor Advisory, 2023-03-08 —
  <https://pcaobus.org/resources/information-for-investors/investor-advisories/investor-advisory-exercise-caution-with-third-party-verification-proof-of-reserve-reports>
- "Binance Proof-of-Reserves Auditor Mazars Pauses All Work for Crypto Clients",
  CoinDesk, 2022-12-16 —
  <https://www.coindesk.com/business/2022/12/16/binance-proof-of-reserves-auditor-mazars-pauses-all-work-for-crypto-clients>
- Regulation (EU) 2023/1114 (MiCA), Articles 70, 75 and 149 —
  <https://eur-lex.europa.eu/eli/reg/2023/1114/oj/eng>
- Copper, "ClearLoop: Collateral Mobility Network" —
  <https://copper.co/en/products/clearloop>
- Fireblocks, "Off Exchange" — <https://www.fireblocks.com/platforms/off-exchange>
