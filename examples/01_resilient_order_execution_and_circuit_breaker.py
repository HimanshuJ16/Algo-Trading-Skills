#!/usr/bin/env python3
"""
Example 01: Resilient Order Execution & Circuit Breaker Workflow

Runs the real helper modules from three skills — nothing here re-implements
them:

  1. token-lifecycle-live-probing              (`token_probe.LiveTokenProbeManager`)
     Probe the cached broker token with a side-effect-free call before trading;
     re-authenticate only on a broker-stated auth failure.
  2. order-placement-idempotency               (`order_ledger.IdempotentOrderRouter`)
     Claim an idempotency key in a durable ledger *before* the network call, so
     a lost response becomes UNKNOWN and is reconciled against the broker's
     order book instead of blindly re-sent.
  3. kill-switch-and-drawdown-circuit-breakers (`circuit_breaker.KillSwitchCircuitBreaker`)
     An independent risk module with veto power over the order path, which
     halts on a peak-equity drawdown breach and keeps vetoing afterwards.

Run from the repository root:

    python examples/01_resilient_order_execution_and_circuit_breaker.py
"""
import logging
import os
import random
import sys
import tempfile
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _slug in (
    "order-placement-idempotency",
    "kill-switch-and-drawdown-circuit-breakers",
    "token-lifecycle-live-probing",
):
    sys.path.insert(0, os.path.join(REPO_ROOT, "skills", _slug, "scripts"))

from circuit_breaker import KillSwitchCircuitBreaker  # noqa: E402
from order_ledger import IdempotentOrderRouter, OrderLedger  # noqa: E402
from token_probe import LiveTokenProbeManager  # noqa: E402

# Seeded so this walkthrough prints the same thing on every run; the probe
# helper draws on `random` for its retry jitter.
random.seed(42)

# The helper modules log through the standard library. Surface their warnings,
# prefixed so they are visibly theirs and not this script's narration.
logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")

STRATEGY_ID = "example01-momentum"
START_EQUITY = 100_000.0
LOSS_PER_FILL = 3_000.0


class FakeBroker:
    """A stand-in broker that fails the way real ones do.

    It accepts an order, then loses the HTTP response — the order is live at
    the venue while the client believes nothing happened. That is the exact
    ambiguity `order-placement-idempotency` exists to resolve, and the reason
    the order book below echoes the client key back.
    """

    def __init__(self, live_token: str, drop_response_for: Tuple[str, ...] = ()):
        self.live_token = live_token
        self.drop_response_for = set(drop_response_for)
        self.book: List[Dict[str, Any]] = []
        self.sends = 0
        self.logins = 0

    # -- token endpoints ------------------------------------------------
    def probe(self, token: str) -> Tuple[int, bool, Dict[str, Any]]:
        """A cheap GET. Returns (status_code, is_timeout, body)."""
        if token == self.live_token:
            return 200, False, {"status": "ok"}
        return 401, False, {"error": "session expired; please relogin"}

    def reauth(self) -> str:
        self.logins += 1
        return self.live_token

    # -- order endpoints ------------------------------------------------
    def send(self, key, symbol, side, quantity, price, strategy_id) -> Dict[str, Any]:
        self.sends += 1
        entry = {
            "client_order_id": key,          # the broker echoes the client tag
            "order_id": "BRK-%04d" % self.sends,
            "status": "OPEN",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
        }
        self.book.append(entry)
        if symbol in self.drop_response_for:
            raise TimeoutError(
                "connection reset after the order reached the matching engine"
            )
        return {"status": "success", "order_id": entry["order_id"]}

    def order_book(self) -> List[Dict[str, Any]]:
        return list(self.book)


def main() -> None:
    print("=== Walkthrough 01: Order Execution, Idempotency & Circuit Breakers ===\n")

    broker = FakeBroker(live_token="tok-live-002", drop_response_for=("AAPL",))

    # --- Step 1: prove the token is live before anything is sent -----------
    probe_manager = LiveTokenProbeManager(
        alert_fn=lambda msg: print("  [ALERT] %s" % msg),
        sleep_fn=lambda _seconds: None,   # no real waiting in an example
        rng=random.random,                # seeded above
    )
    token, refreshed = probe_manager.verify_and_refresh_token(
        broker_name="example-broker",
        cached_token="tok-stale-001",     # this one will probe INVALID (401)
        probe_fn=broker.probe,
        reauth_fn=broker.reauth,
    )
    print("Token verified live: %s (re-authenticated: %s, logins spent: %d)\n"
          % (token, refreshed, broker.logins))

    # --- Step 2 and 3: idempotent placement behind a risk veto -------------
    breaker = KillSwitchCircuitBreaker(
        max_position=1_000.0,
        max_daily_loss=10_000.0,
        max_drawdown_pct=0.05,            # a fraction, not a percentage
        alert_fn=lambda msg: print("  [ALERT] %s" % msg),
        flatten_fn=lambda: print("  [FLATTEN] Force-flattening open positions."),
    )

    orders = [
        {"symbol": "AAPL", "side": "BUY", "quantity": 100.0, "price": 150.0},
        {"symbol": "MSFT", "side": "BUY", "quantity": 50.0, "price": 300.0},
        {"symbol": "NVDA", "side": "BUY", "quantity": 20.0, "price": 900.0},
        {"symbol": "TSLA", "side": "BUY", "quantity": 10.0, "price": 250.0},
    ]

    positions: Dict[str, float] = {}
    equity = START_EQUITY

    # Establish the peak the drawdown is measured from before trading starts;
    # otherwise the first equity the breaker ever sees becomes the peak and the
    # first loss is invisible to it.
    breaker.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=equity)

    # The ledger is durable by design, so it needs a real file. The example
    # keeps it in a temporary directory that is removed on the way out.
    with tempfile.TemporaryDirectory(prefix="example01-") as tmpdir:
        ledger = OrderLedger(os.path.join(tmpdir, "order_intents.sqlite"))
        router = IdempotentOrderRouter(
            ledger,
            alert_fn=lambda msg: print("  [ALERT] %s" % msg),
            broker_echoes_key=True,       # so "absent from the book" is evidence
        )
        try:
            for seq, item in enumerate(orders):
                symbol = item["symbol"]
                delta = item["quantity"] if item["side"] == "BUY" else -item["quantity"]

                # The risk module vetoes first; the strategy never decides this.
                approved, reason = breaker.check_proposed_order(
                    proposed_position_delta=delta,
                    current_position_size=positions.get(symbol, 0.0),
                    symbol=symbol,
                )
                if not approved:
                    print("Order for %s VETOED by the risk module." % symbol)
                    print("  %s" % reason)
                    break

                print("Dispatching %s %s %g @ %g ..."
                      % (item["side"], symbol, item["quantity"], item["price"]))
                ok, status, broker_order_id = router.place_order(
                    strategy_id=STRATEGY_ID,
                    symbol=symbol,
                    side=item["side"],
                    quantity=item["quantity"],
                    price=item["price"],
                    signal_ts="2026-09-04T10:%02d:00Z" % seq,
                    broker_send_fn=broker.send,
                    broker_order_book_fn=broker.order_book,
                )
                print("  [%s] status=%s broker_order_id=%s"
                      % ("OK" if ok else "NOT OK", status, broker_order_id))
                if status.startswith("RECONCILED_PLACED"):
                    print("  The response was lost, but the order was found in the "
                          "book under its client key: no duplicate was sent.")
                if not ok:
                    # UNRESOLVED is not a rejection: never re-issue under a new key.
                    continue

                positions[symbol] = positions.get(symbol, 0.0) + delta

                # Post-trade risk. Each fill is assumed to move equity against us.
                equity -= LOSS_PER_FILL
                halted, breaker_status = breaker.check_pnl_and_drawdown(
                    daily_pnl=equity - START_EQUITY,
                    current_equity=equity,
                    active_positions=positions,
                )
                drawdown = (START_EQUITY - equity) / START_EQUITY
                print("  Equity %.0f, drawdown %.2f%% (against the %.0f peak the "
                      "breaker was seeded with) -> %s"
                      % (equity, 100.0 * drawdown, START_EQUITY, breaker_status))
                if halted:
                    print("  The breaker is latched. The next order is vetoed, not "
                          "merely logged: re-enable requires a named human.")

            unresolved = ledger.unresolved()
            print("\nBroker book holds %d order(s) after %d send(s); %d ledger "
                  "intent(s) remain unresolved."
                  % (len(broker.book), broker.sends, len(unresolved)))
        finally:
            ledger.close()

    print("\n=== Walkthrough 01 Completed Cleanly ===")


if __name__ == "__main__":
    main()
