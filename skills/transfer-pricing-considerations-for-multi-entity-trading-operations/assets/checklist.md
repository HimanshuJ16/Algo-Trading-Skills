# Institutional Transfer Pricing Operations & BEPS Compliance Checklist

## Legal Entity & Intercompany Agreement Setup
- [ ] **Intercompany Service Agreements (SLAs)**: Confirm active written SLAs between parent, IP owner, investment manager, and execution hub entities.
- [ ] **Entity Tax Classification & Registration**: Register all group operating entities (`LegalEntity`) with jurisdiction tax rates and functional roles.
- [ ] **Methodology Selection**: Select appropriate OECD transfer pricing method (Cost-Plus, CUP, TNMM, Profit Split) per service line.

## Intercompany Fee Calculation & Benchmarking
- [ ] **Cost-Plus Markup Calibration**: Verify operating expense markups (5%–15%) against independent benchmarking studies.
- [ ] **CUP Execution Rate Alignment**: Benchmark intercompany execution routing fees against third-party exchange/broker fee cards.
- [ ] **Berry Ratio Verification**: Compute `calculate_berry_ratio()` on service entities to confirm target range ($1.05 \le \text{Berry Ratio} \le 1.25$).

## DEMPE Profit Split & Audit Documentation
- [ ] **DEMPE Function Audit**: Perform annual DEMPE scoring across IP development, enhancement, maintenance, protection, and exploitation.
- [ ] **Residual Profit Allocation**: Execute `calculate_profit_split()` to distribute global trading PnL based on relative DEMPE contributions.
- [ ] **BEPS Audit Defense Archival**: Generate and archive OECD Master File, Local File, and Country-by-Country (CbC) reports.