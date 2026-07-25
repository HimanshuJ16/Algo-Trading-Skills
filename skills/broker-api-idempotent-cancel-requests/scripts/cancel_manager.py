"""
broker-api-idempotent-cancel-requests: Idempotent cancel request manager handling
Cancel-vs-Fill race conditions and duplicate cancel retries.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class CancelStatus(Enum):
    CANCELLED = "CANCELLED"
    FILLED_BEFORE_CANCEL = "FILLED_BEFORE_CANCEL"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    FAILED = "FAILED"


@dataclass
class CancelResult:
    client_cancel_id: str
    order_id: str
    status: CancelStatus
    is_idempotent_retry: bool
    message: str


class IdempotentCancelManager:
    """
    Manages idempotent order cancel requests, preventing duplicate dispatches
    and classifying Cancel-vs-Fill race condition HTTP responses.
    """

    def __init__(
        self,
        http_cancel_fn: Optional[Callable[[str, str], Tuple[int, Dict[str, Any]]]] = None,
    ):
        self._http_cancel_fn = http_cancel_fn
        self.cancel_history: Dict[str, CancelResult] = {}  # client_cancel_id -> CancelResult
        self._seq_counter = 0

    def generate_client_cancel_id(self, order_id: str) -> str:
        self._seq_counter += 1
        return f"CANCEL_{order_id}_{self._seq_counter}"

    def cancel_order_idempotent(self, order_id: str, client_cancel_id: Optional[str] = None) -> CancelResult:
        """
        Submits an idempotent cancel request for order_id. If client_cancel_id has been
        processed previously, returns cached result instantly.
        """
        cid = client_cancel_id or self.generate_client_cancel_id(order_id)

        # 1. Idempotency Cache Check
        if cid in self.cancel_history:
            cached = self.cancel_history[cid]
            logger.info(f"Idempotent Cancel Cache Hit for {cid}: Returning status {cached.status.value}")
            return CancelResult(
                client_cancel_id=cid,
                order_id=order_id,
                status=cached.status,
                is_idempotent_retry=True,
                message=f"Idempotent retry: {cached.message}",
            )

        # 2. Dispatch Cancel Request
        if self._http_cancel_fn:
            status_code, response_data = self._http_cancel_fn(order_id, cid)
        else:
            raise RuntimeError("HTTP cancel transport function not configured.")

        # 3. Classify Race Conditions & Response Codes
        detail = str(response_data.get("detail", "")).lower()

        if status_code in (200, 202, 204):
            res_status = CancelStatus.CANCELLED
            msg = f"Order {order_id} successfully cancelled."
        elif status_code == 400 and ("filled" in detail or "already executed" in detail):
            res_status = CancelStatus.FILLED_BEFORE_CANCEL
            msg = f"Cancel-vs-Fill Race: Order {order_id} filled on exchange before cancel arrived."
            logger.warning(msg)
        elif status_code in (404, 400) and ("not found" in detail or "already cancelled" in detail):
            res_status = CancelStatus.ALREADY_CANCELLED
            msg = f"Order {order_id} already cancelled on broker."
            logger.info(msg)
        else:
            res_status = CancelStatus.FAILED
            msg = f"Cancel failed (HTTP {status_code}): {response_data}"
            logger.error(msg)

        result = CancelResult(
            client_cancel_id=cid,
            order_id=order_id,
            status=res_status,
            is_idempotent_retry=False,
            message=msg,
        )

        self.cancel_history[cid] = result
        return result
