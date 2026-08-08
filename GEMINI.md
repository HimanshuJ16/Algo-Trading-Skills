# Gemini CLI Instructions — Algo-Trading-Skills

This project is a comprehensive library of **504 algorithmic trading skills** mapped across 16 financial engineering domains and 5 global regulatory frameworks.

## 🌟 Operating Guidelines for Gemini CLI

1. **Context & Discovery**:
   - Always check `index.json` or `skills/*/SKILL.md` frontmatter to locate relevant trading infrastructure playbooks.
   - Use progressive disclosure: read `SKILL.md` frontmatter first, then load the full file body as needed.

2. **Code Generation & Architecture Rules**:
   - Adhere to pre-trade risk control boundaries (SEC Rule 15c3-5, EU MiFID II RTS 6).
   - Ensure WebSocket reconnect logic handles missed sequence numbers without submitting duplicate orders.
   - Never compute backtest features using price data from the current bar's close before the bar period completes.
   - Decouple strategy logic from hard kill switches and drawdown circuit breakers.

3. **Verification**:
   - Run `python tools/validate_skills.py` to confirm workspace skill integrity.
   - Run `python tools/run_all_tests.py` to execute unit tests.
