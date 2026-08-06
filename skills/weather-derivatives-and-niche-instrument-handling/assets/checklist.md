# Institutional Weather Derivatives Operations Checklist

## Contract Master & Multiplier Verification
- [ ] **Station Location ID Mapping**: Confirm NOAA station WBAN/ICAO ID (e.g., `KLGA` for LaGuardia, `KORD` for Chicago O'Hare).
- [ ] **Contract Multiplier Check**: Enforce standard CME **\$20 per index point** multiplier for futures and options.
- [ ] **Payout Cap Setup**: Verify OTC weather swap maximum payout caps ($C_{\text{cap}}$).

## Index Accumulation & Valuation
- [ ] **Daily Temperature Quality Control**: Audit daily $T_{\text{min}}$ and $T_{\text{max}}$ station records for missing data.
- [ ] **Burn Analysis Execution**: Run 20-30 year historical simulation using `run_burn_analysis()` to derive fair expected payoffs.
- [ ] **Climate Detrending**: Adjust historical station temperature series for long-term climate warming trends.

## Settlement & Risk Controls
- [ ] **Monthly Expiry Settlement**: Compute final cash settlement payoffs on the first business day following contract expiration.
- [ ] **Credit Exposure Tracking**: Monitor OTC counterparty mark-to-market exposure against ISDA credit support annex (CSA) thresholds.