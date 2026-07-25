"""
broker-api-idempotent-cancel-requests: Idempotent cancel request manager handling
Cancel-vs-Fill race conditions, connection retries, and duplicate cancel storms.
"""
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class CancelStatus(Enum):
    CANCELLED = "CANCELLED"
    FILLED_BEFORE_CANCEL = "FILLED_BEFORE_CANCEL"
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    PENDING_CANCEL = "PENDING_CANCEL"
    FAILED = "FAILED"
    REJECTED = "REJECTED"

@dataclass
class CancelResult:
    client_cancel_id: str
    order_id: str
    status: CancelStatus
    is_idempotent_retry: bool
    message: str

class IdempotentCancelManager:
    """
    Thread-safe manager for idempotent order cancel requests.
    Prevents duplicate dispatches, manages retry logic with backoff,
    and classifies Cancel-vs-Fill race condition HTTP responses.
    """

    def __init__(
        self,
        http_cancel_fn: Callable[[str, str], Tuple[int, Dict[str, Any]]],
        max_cache_size: int = 10000,
        max_retries: int = 3,
        base_backoff_ms: int = 100,
    ):
        self._http_cancel_fn = http_cancel_fn
        self._max_cache_size = max_cache_size
        self._max_retries = max_retries
        self._base_backoff_ms = base_backoff_ms
        
        self._lock = threading.Lock()
        self._cancel_history: OrderedDict[str, CancelResult] = OrderedDict()
        self._seq_counter = 0

    def generate_client_cancel_id(self, order_id: str) -> str:
        with self._lock:
            self._seq_counter += 1
            return f"CANCEL_{order_id}_{self._seq_counter}_{int(time.time()*1000)}"

    def _cache_result(self, cid: str, result: CancelResult) -> None:
        with self._lock:
            self._cancel_history[cid] = result
            if len(self._cancel_history) > self._max_cache_size:
                self._cancel_history.popitem(last=False)

    def cancel_order_idempotent(self, order_id: str, client_cancel_id: Optional[str] = None) -> CancelResult:
        """
        Submits an idempotent cancel request for order_id. If client_cancel_id has been
        processed previously, returns cached result instantly.
        """
        cid = client_cancel_id or self.generate_client_cancel_id(order_id)
        
        with self._lock:
            if cid in self._cancel_history:
                cached = self._cancel_history[cid]
                logger.info(f"Idempotent Cancel Cache Hit for {cid}: Returning status {cached.status.value}")
                return CancelResult(
                    client_cancel_id=cid,
                    order_id=order_id,
                    status=cached.status,
                    is_idempotent_retry=True,
                    message=f"Idempotent retry: {cached.message}",
                )
        
        status_code = 0
        response_data = {}
        err_msg = ""
        
        for attempt in range(self._max_retries + 1):
            try:
                status_code, response_data = self._http_cancel_fn(order_id, cid)
                # Break if not a 5xx error (server error/gateway timeout)
                if status_code < 500:
                    break
            except Exception as e:
                err_msg = str(e)
                status_code = 0
            
            if attempt < self._max_retries:
                time.sleep((self._base_backoff_ms * (2 ** attempt)) / 1000.0)
                
        detail = str(response_data.get("detail", response_data.get("error", err_msg))).lower()
        
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
            msg = f"Cancel failed (HTTP {status_code}): {detail}"
            logger.error(msg)
            
        result = CancelResult(
            client_cancel_id=cid,
            order_id=order_id,
            status=res_status,
            is_idempotent_retry=False,
            message=msg,
        )
        self._cache_result(cid, result)
        return result
