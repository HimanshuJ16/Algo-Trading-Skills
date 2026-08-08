#!/usr/bin/env python3
"""
Example 01: Resilient Order Execution & Circuit Breaker Workflow

Chains 3 Skills:
  1. token-lifecycle-live-probing (Verify API token validity before order dispatch)
  2. order-placement-idempotency (Prevent duplicate order fills on network timeout)
  3. kill-switch-and-drawdown-circuit-breakers (Halt trading immediately on peak drawdown breach)
"""
import uuid
import time
from typing import Dict, Any, Optional


class TokenProbe:
    """Skill: token-lifecycle-live-probing"""

    def __init__(self, expires_in_sec: float = 3600):
        self.created_at = time.time()
        self.expires_in = expires_in_sec

    def is_valid(self) -> bool:
        return self.check_token()

    def check_token(self) -> bool:
        remaining = self.expires_in - (time.time() - self.created_at)
        return remaining > 30.0  # Buffer window before expiry


class OrderLedger:
    """Skill: order-placement-idempotency"""

    def __init__(self):
        self._ledger: Dict[str, Dict[str, Any]] = {}
        self._exchange_book: Dict[str, Dict[str, Any]] = {}

    def submit_order(self, client_order_id: str, symbol: str, qty: int, price: float, simulate_timeout: bool = False) -> Dict[str, Any]:
        if client_order_id in self._ledger:
            return {"status": "DUPLICATE_REJECTED", "client_order_id": client_order_id}

        self._ledger[client_order_id] = {
            "symbol": symbol, "qty": qty, "price": price, "status": "PENDING"
        }

        if simulate_timeout:
            # Simulate order landing on exchange before HTTP response times out
            self._exchange_book[client_order_id] = {
                "symbol": symbol, "qty": qty, "price": price, "status": "FILLED"
            }
            raise TimeoutError("Network connection reset during order request")

        self._ledger[client_order_id]["status"] = "FILLED"
        self._exchange_book[client_order_id] = self._ledger[client_order_id]
        return self._ledger[client_order_id]

    def reconcile_ambiguous_order(self, client_order_id: str) -> str:
        """Reconcile status with exchange before retrying."""
        if client_order_id in self._exchange_book:
            status = self._exchange_book[client_order_id]["status"]
            self._ledger[client_order_id]["status"] = status
            return status
        return "NOT_FOUND"


class CircuitBreaker:
    """Skill: kill-switch-and-drawdown-circuit-breakers"""

    def __init__(self, max_drawdown_pct: float = 0.05):
        self.max_drawdown_pct = max_drawdown_pct
        self.peak_equity = 100000.0
        self.current_equity = 100000.0
        self.tripped = False

    def update_equity(self, new_equity: float) -> bool:
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
        self.current_equity = new_equity

        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        if drawdown >= self.max_drawdown_pct:
            self.tripped = True
            print(f"  [KILL SWITCH TRIPPED] Drawdown breached: {drawdown * 100:.2f}% >= {self.max_drawdown_pct * 100:.2f}%")
        return self.tripped


def main():
    print("=== Walkthrough 01: Order Execution, Idempotency & Circuit Breakers ===\n")

    token_probe = TokenProbe(expires_in_sec=100)
    ledger = OrderLedger()
    cb = CircuitBreaker(max_drawdown_pct=0.05)  # 5% max drawdown limit

    orders_to_place = [
        {"symbol": "AAPL", "qty": 100, "price": 150.0, "simulate_timeout": True},
        {"symbol": "MSFT", "qty": 50, "price": 300.0, "simulate_timeout": False},
    ]

    for item in orders_to_place:
        # Step 1: Probe token validity
        if not token_probe.check_token():
            print("  [ERROR] Broker token expired or near expiry. Re-authenticating...")
            break

        # Step 2: Generate idempotent Client Order ID
        client_order_id = f"ORD-{uuid.uuid4().hex[:8]}"
        print(f"Dispatching order {client_order_id} for {item['qty']} {item['symbol']} @ ${item['price']}...")

        try:
            res = ledger.submit_order(client_order_id, item['symbol'], item['qty'], item['price'], simulate_timeout=item['simulate_timeout'])
            print(f"  [SUCCESS] Order filled: {res}")
        except TimeoutError:
            print(f"  [WARNING] Network timeout on {client_order_id}. Reconciling against exchange before retrying...")
            status = ledger.reconcile_ambiguous_order(client_order_id)
            print(f"  [RECONCILED] Order state confirmed on exchange: {status}. Skipping duplicate resubmission.")

        # Step 3: Check post-trade portfolio risk & drawdown
        simulated_equity = cb.current_equity - 3000.0  # Simulate $3,000 loss
        if cb.update_equity(simulated_equity):
            print("  [HALT] Halting execution pipeline. Circuit breaker engaged.")
            break

    print("\n=== Walkthrough 01 Completed Cleanly ===")


if __name__ == "__main__":
    main()
