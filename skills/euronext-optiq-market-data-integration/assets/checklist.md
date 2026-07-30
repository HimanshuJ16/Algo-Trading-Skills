# Pre-Flight Checklist

- [ ] Are Optiq SBE binary message templates (MarketUpdate 1001, Trade 1004, SymbolStatus 1005) decoded correctly?
- [ ] Is L2 limit order book depth reconstructed accurately?
- [ ] Are price decimal scaling factors applied to raw binary integers?
- [ ] Are SymbolStatus state transitions (`HALTED` / `CALL_AUCTION`) handled to freeze quoting?
