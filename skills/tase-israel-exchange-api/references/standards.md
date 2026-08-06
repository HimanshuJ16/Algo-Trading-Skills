# Tel Aviv Stock Exchange (TASE) Institutional Integration Standards

## 1. Protocol Specifications
- **Order Routing & Execution**: Standardized on FIX 4.4 / FIX 5.0 SP2 customized for TASE.
- **Market Data Feeds**: Ultra-low latency ITCH binary feeds for Market-by-Order (MBO) depth and trade reports; FAST/FIX feeds for consolidated top-of-book and index quotes.
- **Reference & Corporate Data**: TASE Data Hub REST API (OData/JSON) and MAYA Corporate Action Portal for ISIN mapping, corporate events, dividend declarations, and daily settlement prices.

## 2. Currency & Price Denomination Rules
| Security Class | Price Denomination | Base Unit | Conversion Rule | Example |
| :--- | :--- | :--- | :--- | :--- |
| **Equities / Shares** | Agorot (Cents) | 1/100 NIS | 100 Agorot = 1 ILS/NIS | Teva @ 3,500 Agorot = 35.00 ILS |
| **Mutual Funds / ETFs** | Agorot (Cents) | 1/100 NIS | 100 Agorot = 1 ILS/NIS | Harel ETF @ 1,250 Agorot = 12.50 ILS |
| **Corporate & Gov Bonds** | Percentage of Par | % | Price quoted as % (e.g. 102.5) | Gov Bond 0328 @ 102.5% |
| **Makam (T-Bills)** | Percentage of Par | % | Price quoted as % of par | Makam 512 @ 98.75% |
| **Index Options & Futures** | NIS / Points | 1 ILS | Quoted directly in NIS | TA-35 Call 2000 @ 450 ILS |

> [!IMPORTANT]
> Order entry algorithms targeting equities **must** convert internal decimal ILS model prices to integer/decimal Agorot before constructing `NewOrderSingle` (FIX Tag 44). Failure to do so will result in 100x price discrepancy rejections or unintended executions.

## 3. Trading Calendar & Session Schedule (Israel Standard Time - UTC+2 / UTC+3 DST)
- **Weekly Schedule**: **Sunday through Thursday**. Friday and Saturday are non-trading weekend days.
- **Sunday Sessions**:
  - Pre-Open Phase: 08:30 – 09:50 IST
  - Opening Auction (Random Uncrossing): 09:50 – 10:00 IST
  - Continuous Trading Phase: 10:00 – 15:50 IST
  - Closing Auction (Random Uncrossing): 15:50 – 16:00 IST
- **Monday – Thursday Sessions**:
  - Pre-Open Phase: 08:30 – 09:50 IST
  - Opening Auction (Random Uncrossing): 09:50 – 10:00 IST
  - Continuous Trading Phase: 10:00 – 17:15 IST
  - Closing Auction (Random Uncrossing): 17:15 – 17:25 IST

## 4. Regulatory & Pre-Trade Risk Controls (ISA Regulations)
- **Order-to-Trade Ratio (OTR)**: Israel Securities Authority (ISA) enforces strict OTR caps. Algo trading modules must throttle excessive quote updates to avoid penalty fees.
- **Self-Match Prevention (SMP)**: Trading engines must include `TraderID` / `Account` tags to prevent cross-account internal matching.
- **Dynamic Price Collar**: Orders deviating by more than the threshold (typically ±10% to ±15% from the dynamic reference price) are rejected by TASE circuit breakers.

