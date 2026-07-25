# Workflows for Algo Trading Disclosure

## Pre-Trade Compliance Pipeline

1. **Order Generation**: The algorithmic strategy generates an intent to trade and attaches its globally unique `algo_id` to the order metadata.
2. **Compliance Gateway Interception**: Before the order is routed to the FIX gateway, the `AlgoDisclosureComplianceEngine` intercepts it.
3. **Disclosure Validation**: The engine checks the global compliance registry. It verifies that the `algo_id` has been formally documented, tested in the sandbox, and submitted to the exchange regulatory authority.
4. **Execution or Block**:
   - **Pass**: Order proceeds to the exchange with the `algo_id` populated in the specific FIX tag (e.g., FIX Tag 25013 in MiFID II contexts).
   - **Fail**: Order is hard-rejected, and the compliance officer is alerted of an unauthorized strategy attempting to trade.