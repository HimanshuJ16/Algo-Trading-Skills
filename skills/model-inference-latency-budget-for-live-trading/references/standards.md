# Standards for Model Inference Latency Budgeting

| Metric | Engineering Standard |
|---|---|
| SLA Metric Standard | Model inference SLA MUST be evaluated against $P_{99}$ latency, NOT averages. |
| Fallback Protocol | Automated model fallback MUST trigger when $P_{99}$ exceeds maximum budget limit. |
| Pre-Market Warmup | GPU/CPU engines MUST run pre-market dummy inferences to eliminate cold-start spikes. |
