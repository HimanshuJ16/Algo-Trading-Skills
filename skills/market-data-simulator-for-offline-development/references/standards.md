# Standards for Market Data Simulation

| Metric | Engineering Standard |
|---|---|
| Price Process | Synthetic prices MUST be generated using log-normal Geometric Brownian Motion (GBM). |
| Seed Reproducibility | Simulations MUST accept a random seed for 100% reproducible test streams. |
| Spread Dynamics | Bid price MUST be strictly less than Ask price ($P_{\text{bid}} < P_{\text{ask}}$). |
