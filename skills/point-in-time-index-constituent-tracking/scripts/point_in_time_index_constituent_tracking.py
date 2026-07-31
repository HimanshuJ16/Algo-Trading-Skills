import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class PointInTimeIndexConstituentTrackingConfig:
    enabled: bool = True
    index_name: str = "SP500"

@dataclass
class IndexConstituentEvent:
    index_name: str
    symbol: str
    event_type: str                     # 'ADDITION', 'DELETION'
    effective_date: str                 # e.g. '2020-12-21'
    weight: float = 0.0

@dataclass
class PITIndexQuery:
    index_name: str
    as_of_date: str                      # e.g. '2020-06-01'

@dataclass
class PITIndexReport:
    index_name: str
    as_of_date: str
    active_constituents: List[str]
    total_active_count: int
    survivorship_bias_ghost_count: int  # Delisted/removed symbols present in PIT but missing today
    status: str                          # 'UNIVERSE_RESOLVED_PIT', 'INDEX_NOT_FOUND'
    audit_notes: str

class PointInTimeIndexConstituentTrackingEngine:
    """
    Point-in-Time (PIT) index constituent tracking engine maintaining historical addition/deletion
    membership windows, eliminating survivorship bias in equity backtests.
    """
    def __init__(self, config: Optional[PointInTimeIndexConstituentTrackingConfig] = None):
        self.config = config or PointInTimeIndexConstituentTrackingConfig()
        self.events: List[IndexConstituentEvent] = []

    def process(self, data: str) -> str:
        """
        Legacy process method for backward compatibility.
        """
        if not self.config.enabled:
            return ""
        return data.upper() if data else ""

    def insert_events(self, events: List[IndexConstituentEvent]) -> None:
        """Ingests historical index constituent addition and deletion events."""
        self.events.extend(events)

    def query_pit_universe(
        self, query: PITIndexQuery, current_static_universe: Optional[Set[str]] = None
    ) -> PITIndexReport:
        """
        Resolves point-in-time index constituent membership on query.as_of_date.
        """
        if not self.config.enabled:
            return PITIndexReport(
                index_name=query.index_name,
                as_of_date=query.as_of_date,
                active_constituents=[],
                total_active_count=0,
                survivorship_bias_ghost_count=0,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        idx_name_upper = query.index_name.upper()

        # Group events by symbol for target index up to query as_of_date
        symbol_events: Dict[str, List[IndexConstituentEvent]] = {}
        for ev in self.events:
            if ev.index_name.upper() == idx_name_upper and ev.effective_date <= query.as_of_date:
                sym = ev.symbol.upper()
                if sym not in symbol_events:
                    symbol_events[sym] = []
                symbol_events[sym].append(ev)

        active_symbols: List[str] = []

        for sym, ev_list in symbol_events.items():
            # Sort chronologically
            sorted_evs = sorted(ev_list, key=lambda e: e.effective_date)
            last_event = sorted_evs[-1]
            if last_event.event_type.upper() == "ADDITION":
                active_symbols.append(sym)

        active_symbols.sort()

        # Audit Survivorship Bias (Ghost count = PIT symbols not present in current static universe)
        ghost_count = 0
        if current_static_universe is not None:
            current_upper = {s.upper() for s in current_static_universe}
            ghost_count = sum(1 for s in active_symbols if s not in current_upper)

        notes = (
            f"PIT INDEX UNIVERSE RESOLVED [{idx_name_upper} as of {query.as_of_date}]: "
            f"Total Active = {len(active_symbols)} symbols. "
            f"Survivorship Bias Ghost Symbols (Delisted/Removed) = {ghost_count}."
        )
        logger.info(notes)

        return PITIndexReport(
            index_name=idx_name_upper,
            as_of_date=query.as_of_date,
            active_constituents=active_symbols,
            total_active_count=len(active_symbols),
            survivorship_bias_ghost_count=ghost_count,
            status="UNIVERSE_RESOLVED_PIT",
            audit_notes=notes
        )
