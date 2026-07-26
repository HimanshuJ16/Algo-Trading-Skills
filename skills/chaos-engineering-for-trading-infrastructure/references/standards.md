# Standards for Trading System Resilience

| Metric | Engineering Standard |
|---|---|
| Blast Radius | Chaos experiments must never be capable of interacting with live exchange gateways or real capital. |
| Determinism | Randomness in chaos injection (e.g., random packet drops) MUST be seeded so that a failing test run can be reproduced exactly for debugging. |
| Grey Failures | Resilience testing must include "tarpit" testing—connections that accept data but respond incredibly slowly (e.g., 30-second delays) to validate non-blocking I/O architectures. |
