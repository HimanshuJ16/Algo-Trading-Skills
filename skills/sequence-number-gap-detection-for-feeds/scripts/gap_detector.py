"""
sequence-number-gap-detection-for-feeds: Monotonic sequence tracker, out-of-order buffer,
and orderbook sync guard engine.
"""
from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FeedSyncState(Enum):
    SYNCED = "SYNCED"
    DIRTY_SYNC_PENDING = "DIRTY_SYNC_PENDING"
    RECOVERING = "RECOVERING"


@dataclass
class FeedFrame:
    symbol: str
    sequence_id: int
    payload: Dict[str, Any]


@dataclass
class GapDetectionResult:
    symbol: str
    state: FeedSyncState
    is_gap_detected: bool
    missing_range: Optional[Tuple[int, int]]
    processed_frames: List[FeedFrame]
    message: str


class SequenceGapDetector:
    """
    Tracks expected sequence IDs per channel/symbol, buffers out-of-order frames,
    and manages state reconciliation upon detecting packet drops.
    """

    def __init__(self, max_buffer_size: int = 1000):
        self.max_buffer_size = max_buffer_size
        self.expected_sequences: Dict[str, int] = {}  # symbol -> next expected seq
        self.out_of_order_buffers: Dict[str, Dict[int, FeedFrame]] = {}  # symbol -> {seq -> frame}
        self.sync_states: Dict[str, FeedSyncState] = {}  # symbol -> FeedSyncState

    def ingest_frame(self, frame: FeedFrame) -> GapDetectionResult:
        """
        Ingests a feed frame, checks for sequence gaps, and returns ready-to-process frames.
        """
        symbol = frame.symbol.upper()
        seq = frame.sequence_id

        if symbol not in self.expected_sequences:
            # Initialize baseline
            self.expected_sequences[symbol] = seq + 1
            self.out_of_order_buffers[symbol] = {}
            self.sync_states[symbol] = FeedSyncState.SYNCED
            return GapDetectionResult(
                symbol=symbol,
                state=FeedSyncState.SYNCED,
                is_gap_detected=False,
                missing_range=None,
                processed_frames=[frame],
                message=f"Initialized feed baseline for {symbol} at seq {seq}.",
            )

        expected = self.expected_sequences[symbol]
        buf = self.out_of_order_buffers[symbol]

        # Case 1: In-order frame
        if seq == expected:
            processed = [frame]
            next_seq = expected + 1

            # Drain any contiguous buffered out-of-order frames
            while next_seq in buf:
                buffered_frame = buf.pop(next_seq)
                processed.append(buffered_frame)
                next_seq += 1

            self.expected_sequences[symbol] = next_seq
            self.sync_states[symbol] = FeedSyncState.SYNCED if len(buf) == 0 else FeedSyncState.DIRTY_SYNC_PENDING

            return GapDetectionResult(
                symbol=symbol,
                state=self.sync_states[symbol],
                is_gap_detected=False,
                missing_range=None,
                processed_frames=processed,
                message=f"Processed {len(processed)} in-order frames for {symbol}.",
            )

        # Case 2: Sequence Gap (Out-of-order frame arriving early)
        elif seq > expected:
            if len(buf) >= self.max_buffer_size:
                logger.error(f"Out-of-order buffer overflow for {symbol}. Resetting feed state.")
                self.sync_states[symbol] = FeedSyncState.DIRTY_SYNC_PENDING

            buf[seq] = frame
            self.sync_states[symbol] = FeedSyncState.DIRTY_SYNC_PENDING
            missing_range = (expected, seq - 1)

            logger.warning(
                f"SEQUENCE GAP DETECTED on {symbol}: Expected {expected}, got {seq}. "
                f"Missing range [{missing_range[0]}..{missing_range[1]}]."
            )

            return GapDetectionResult(
                symbol=symbol,
                state=FeedSyncState.DIRTY_SYNC_PENDING,
                is_gap_detected=True,
                missing_range=missing_range,
                processed_frames=[],
                message=f"Gap detected for {symbol}. Missing sequence range [{missing_range[0]}..{missing_range[1]}].",
            )

        # Case 3: Duplicate / Stale frame (seq < expected)
        else:
            logger.info(f"Duplicate/stale frame ignored for {symbol}: Seq {seq} < expected {expected}")
            return GapDetectionResult(
                symbol=symbol,
                state=self.sync_states.get(symbol, FeedSyncState.SYNCED),
                is_gap_detected=False,
                missing_range=None,
                processed_frames=[],
                message=f"Stale frame {seq} ignored.",
            )

    def reconcile_missing_frames(self, symbol: str, missing_frames: List[FeedFrame]) -> List[FeedFrame]:
        """
        Ingests missing frames retrieved from re-transmission endpoint to recover SYNCED state.
        """
        sym = symbol.upper()
        all_processed: List[FeedFrame] = []

        # Sort missing frames by sequence
        sorted_missing = sorted(missing_frames, key=lambda f: f.sequence_id)
        for mf in sorted_missing:
            res = self.ingest_frame(mf)
            all_processed.extend(res.processed_frames)

        logger.info(f"Reconciled {len(sorted_missing)} missing gap frames for {sym}.")
        return all_processed
