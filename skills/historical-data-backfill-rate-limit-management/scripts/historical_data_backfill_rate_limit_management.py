import time
import math
import random
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class BackfillChunk:
    chunk_id: str
    start_date_iso: str
    end_date_iso: str
    status: str = "PENDING"             # 'PENDING', 'COMPLETED', 'FAILED'
    retries_count: int = 0
    records_ingested: int = 0

@dataclass
class BackfillExecutionReport:
    symbol: str
    total_chunks: int
    completed_chunks_count: int
    failed_chunks_count: int
    total_records_ingested: int
    total_rate_limit_throttles: int
    execution_time_sec: float
    status: str                         # 'BACKFILL_SUCCESS', 'BACKFILL_PARTIAL', 'BACKFILL_FAILED'
    audit_notes: str

class HistoricalBackfillManagerEngine:
    """
    Quantitative data engineering pipeline engine for managing historical tick/bar backfills,
    implementing Token Bucket rate limiting, handling HTTP 429 with exponential backoff + jitter, and checkpointing paged chunks.
    """
    def __init__(
        self,
        requests_per_minute: int = 60,
        max_burst_capacity: int = 10,
        max_retries: int = 3,
        base_retry_delay_sec: float = 1.0,
        max_retry_delay_sec: float = 16.0
    ):
        self.requests_per_minute = requests_per_minute
        self.max_burst_capacity = max_burst_capacity
        self.max_retries = max_retries
        self.base_retry_delay_sec = base_retry_delay_sec
        self.max_retry_delay_sec = max_retry_delay_sec

        self.token_fill_rate_hz = requests_per_minute / 60.0
        self.current_tokens = float(max_burst_capacity)
        self.last_token_update_time = time.time()

    def _refill_tokens(self):
        now = time.time()
        elapsed = now - self.last_token_update_time
        added_tokens = elapsed * self.token_fill_rate_hz
        self.current_tokens = min(float(self.max_burst_capacity), self.current_tokens + added_tokens)
        self.last_token_update_time = now

    def acquire_token(self) -> float:
        """
        Acquires token from bucket. If bucket empty, returns required wait time in seconds.
        """
        self._refill_tokens()
        if self.current_tokens >= 1.0:
            self.current_tokens -= 1.0
            return 0.0
        else:
            needed = 1.0 - self.current_tokens
            wait_time = needed / self.token_fill_rate_hz
            return round(wait_time, 3)

    def calculate_backoff_delay(self, attempt: int, retry_after_header: Optional[float] = None) -> float:
        """
        Calculates Exponential Backoff with Jitter for HTTP 429 / 503 error recovery.
        """
        if retry_after_header is not None and retry_after_header > 0:
            return retry_after_header

        backoff = self.base_retry_delay_sec * (2 ** attempt)
        jitter = random.uniform(0.1, 0.5)
        total_delay = min(self.max_retry_delay_sec, backoff + jitter)
        return round(total_delay, 3)

    def execute_backfill(
        self,
        symbol: str,
        chunks: List[BackfillChunk],
        simulated_fetch_func                       # Signature: func(chunk) -> (success: bool, is_429: bool, records: int)
    ) -> BackfillExecutionReport:
        """
        Orchestrates date-chunked backfill pipeline with Token Bucket rate pacing and HTTP 429 resilience.
        """
        if not chunks:
            raise ValueError("Chunks list cannot be empty.")

        start_time = time.time()
        completed = 0
        failed = 0
        total_records = 0
        total_throttles = 0

        for chunk in chunks:
            attempt = 0
            chunk_success = False

            while attempt <= self.max_retries and not chunk_success:
                # 1. Enforce Token Bucket Rate Limit
                wait_sec = self.acquire_token()
                if wait_sec > 0:
                    time.sleep(min(0.01, wait_sec)) # Pacing delay

                # 2. Execute Data Fetch
                success, is_429, records = simulated_fetch_func(chunk)

                if success:
                    chunk.status = "COMPLETED"
                    chunk.records_ingested = records
                    completed += 1
                    total_records += records
                    chunk_success = True
                elif is_429:
                    total_throttles += 1
                    attempt += 1
                    chunk.retries_count = attempt
                    if attempt <= self.max_retries:
                        delay = self.calculate_backoff_delay(attempt)
                        logger.warning(
                            f"HTTP 429 RATE LIMIT EXCEEDED [{symbol} - Chunk {chunk.chunk_id}]: "
                            f"Attempt {attempt}/{self.max_retries}. Backing off for {delay:.2f}s."
                        )
                        time.sleep(min(0.01, delay)) # Backoff delay simulation
                else:
                    # Non-retryable error
                    attempt += 1
                    chunk.retries_count = attempt

            if not chunk_success:
                chunk.status = "FAILED"
                failed += 1

        exec_time = round(time.time() - start_time, 3)

        if failed == 0:
            status = "BACKFILL_SUCCESS"
        elif completed > 0:
            status = "BACKFILL_PARTIAL"
        else:
            status = "BACKFILL_FAILED"

        notes = (
            f"HISTORICAL BACKFILL COMPLETE [{symbol}]: Processed {len(chunks)} chunks. "
            f"Success = {completed}, Failed = {failed}, Total Records = {total_records:,}. "
            f"Rate Limit Throttles (429) = {total_throttles}. Exec Time = {exec_time}s."
        )
        logger.info(notes)

        return BackfillExecutionReport(
            symbol=symbol,
            total_chunks=len(chunks),
            completed_chunks_count=completed,
            failed_chunks_count=failed,
            total_records_ingested=total_records,
            total_rate_limit_throttles=total_throttles,
            execution_time_sec=exec_time,
            status=status,
            audit_notes=notes
        )
