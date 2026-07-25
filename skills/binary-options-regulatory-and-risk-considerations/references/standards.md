# Quantitative Risk Standards for Binary Options

## 1. Compliance Standards
- **Jurisdiction Checks**: Hard-coded mappings of client categorization (Retail vs. Professional) to approved jurisdictions.
- **Venue Vetting**: Only whitelist strictly regulated exchanges (e.g., Nadex, Cantor Exchange). Over-the-counter (OTC) flow must be heavily collateralized and vetted by Legal.

## 2. Risk Modeling
- **Delta/Gamma Caps**: Strict limits on Greeks near maturity. Binary options exhibit Dirac delta-like behavior for gamma at expiry.
- **Maximum Notional**: Enforced per-trade limits based on firm-wide risk appetite.
- **VaR / Stress Testing**: Gap scenarios must assume maximum loss (payout minus premium or full premium) due to discontinuous payoffs.