# Best Execution Record-Keeping Standards

## 1. Regulatory Context
- **MiFID II (Europe)**: Requires investment firms to take all sufficient steps to obtain the best possible result for their clients. Explicit record-keeping of algorithms used, timestamps down to microsecond/nanosecond levels, and venue execution details are mandatory.
- **SEC Rules 605/606 (US)**: Requires disclosure of order routing practices and execution quality metrics.
- **FINRA Rule 5310**: Best execution and interpositioning requirements.

## 2. Key Metrics & Benchmarks
- **Implementation Shortfall (IS)** / Arrival Price.
- **Volume Weighted Average Price (VWAP)**.
- **Time Weighted Average Price (TWAP)**.
- **Participation Rate (POV)**.

## 3. Immutability & Audit
- All records must be stored immutably (WORM storage).
- Hashing (SHA-256 or similar) of trade records upon completion provides tamper-evident logs.
- Timestamps must sync to UTC and meet clock synchronization protocols (e.g., PTP, NTP).