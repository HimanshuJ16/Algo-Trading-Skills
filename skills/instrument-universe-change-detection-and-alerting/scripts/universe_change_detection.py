import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class InstrumentRecord:
    permanent_id: str                   # Immutable FIGI or ISIN e.g. 'BBG000MM82B1'
    ticker_symbol: str                  # e.g. 'FB' or 'META'
    asset_name: str
    exchange: str
    status: str = "ACTIVE"              # 'ACTIVE', 'DELISTED', 'HALTED'

@dataclass
class UniverseChangeAlert:
    change_type: str                    # 'ADDITION', 'DELETION', 'TICKER_RENAME', 'STATUS_CHANGE'
    permanent_id: str
    previous_ticker: str
    new_ticker: str
    recommended_action: str             # 'INITIATE_COVERAGE', 'LIQUIDATE_POSITION_AND_UNSUBSCRIBE', 'UPDATE_SYMBOL_MAPPER', 'FREEZE_TRADING_ALERTS'
    audit_notes: str

@dataclass
class UniverseChangeReport:
    total_previous_count: int
    total_current_count: int
    additions_count: int
    deletions_count: int
    renames_count: int
    status_changes_count: int
    alerts: List[UniverseChangeAlert]
    status: str                         # 'UNIVERSE_NO_CHANGES', 'UNIVERSE_CHANGES_DETECTED'
    audit_notes: str

class UniverseChangeDetectionEngine:
    """
    Reference data management engine for detecting tradable universe changes
    (additions, de-listings, ticker renames, halts) across daily snapshots using permanent identifiers (FIGI / ISIN).
    """
    def __init__(self):
        pass

    def detect_universe_changes(
        self,
        previous_snapshot: List[InstrumentRecord],
        current_snapshot: List[InstrumentRecord]
    ) -> UniverseChangeReport:
        """
        Cross-matches previous and current snapshots by permanent ID, detecting additions, deletions, renames, and status halts.
        """
        prev_map: Dict[str, InstrumentRecord] = {rec.permanent_id: rec for rec in previous_snapshot}
        curr_map: Dict[str, InstrumentRecord] = {rec.permanent_id: rec for rec in current_snapshot}

        prev_ids: Set[str] = set(prev_map.keys())
        curr_ids: Set[str] = set(curr_map.keys())

        added_ids = curr_ids - prev_ids
        deleted_ids = prev_ids - curr_ids
        common_ids = prev_ids & curr_ids

        alerts: List[UniverseChangeAlert] = []
        renames_count = 0
        status_changes_count = 0

        # 1. Process Additions
        for pid in sorted(added_ids):
            rec = curr_map[pid]
            notes = f"UNIVERSE ADDITION [{rec.ticker_symbol} / FIGI: {pid}]: Newly added to tradable universe."
            alerts.append(UniverseChangeAlert(
                change_type="ADDITION", permanent_id=pid, previous_ticker="", new_ticker=rec.ticker_symbol,
                recommended_action="INITIATE_COVERAGE", audit_notes=notes
            ))

        # 2. Process Deletions / De-listings
        for pid in sorted(deleted_ids):
            rec = prev_map[pid]
            notes = f"UNIVERSE DELETION [{rec.ticker_symbol} / FIGI: {pid}]: Removed or de-listed from universe!"
            alerts.append(UniverseChangeAlert(
                change_type="DELETION", permanent_id=pid, previous_ticker=rec.ticker_symbol, new_ticker="",
                recommended_action="LIQUIDATE_POSITION_AND_UNSUBSCRIBE", audit_notes=notes
            ))

        # 3. Process Ticker Renames & Status Halts for Common IDs
        for pid in sorted(common_ids):
            p_rec = prev_map[pid]
            c_rec = curr_map[pid]

            # Check Ticker Rename (e.g. FB -> META)
            if p_rec.ticker_symbol.upper() != c_rec.ticker_symbol.upper():
                renames_count += 1
                notes = (
                    f"TICKER RENAME [{pid}]: Symbol changed from '{p_rec.ticker_symbol}' to '{c_rec.ticker_symbol}'. "
                    f"Updating symbol mapper preserving permanent historical data joins."
                )
                alerts.append(UniverseChangeAlert(
                    change_type="TICKER_RENAME", permanent_id=pid, previous_ticker=p_rec.ticker_symbol,
                    new_ticker=c_rec.ticker_symbol, recommended_action="UPDATE_SYMBOL_MAPPER", audit_notes=notes
                ))

            # Check Status Changes (e.g. ACTIVE -> HALTED)
            if p_rec.status.upper() != c_rec.status.upper():
                status_changes_count += 1
                notes = f"STATUS CHANGE [{c_rec.ticker_symbol} / {pid}]: Status updated from '{p_rec.status}' to '{c_rec.status}'."
                alerts.append(UniverseChangeAlert(
                    change_type="STATUS_CHANGE", permanent_id=pid, previous_ticker=p_rec.ticker_symbol,
                    new_ticker=c_rec.ticker_symbol, recommended_action="FREEZE_TRADING_ALERTS", audit_notes=notes
                ))

        total_changes = len(added_ids) + len(deleted_ids) + renames_count + status_changes_count
        status = "UNIVERSE_CHANGES_DETECTED" if total_changes > 0 else "UNIVERSE_NO_CHANGES"

        notes = (
            f"UNIVERSE CHANGE REPORT: Previous = {len(previous_snapshot)}, Current = {len(current_snapshot)}. "
            f"Additions = {len(added_ids)}, Deletions = {len(deleted_ids)}, "
            f"Ticker Renames = {renames_count}, Status Changes = {status_changes_count}."
        )
        logger.info(notes)

        return UniverseChangeReport(
            total_previous_count=len(previous_snapshot),
            total_current_count=len(current_snapshot),
            additions_count=len(added_ids),
            deletions_count=len(deleted_ids),
            renames_count=renames_count,
            status_changes_count=status_changes_count,
            alerts=alerts,
            status=status,
            audit_notes=notes
        )
