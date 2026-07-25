# Borsa Istanbul Integration Standards

- **Protocol Specification**: Strictly adhere to FIX 5.0 SP2 as customized by BISTECH.
- **Latency Management**: For HFT, bypassing standard FIX in favor of OUCH for order entry and ITCH for market data is recommended. This module simulates the FIX standard for broader institutional access.
- **State Management**: Persist sequence numbers and order states across restarts to survive intraday crashes.
- **Symbol Convention**: Use BIST standard symbol suffixes (e.g., `.E` for equities).
- **Error Handling**: Do not blindly retry rejected orders. Log the `Text` field of the rejection and alert trading operations.
