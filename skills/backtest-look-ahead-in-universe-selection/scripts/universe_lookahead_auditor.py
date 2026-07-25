from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass
class ConstituentRecord:
    symbol: str
    added_date: datetime
    removed_date: Optional[datetime]
    data_publication_date: datetime

@dataclass
class AuditResult:
    is_clean: bool
    lookahead_violations: List[str]
    survivorship_warnings: List[str]

class UniverseLookaheadAuditor:
    """
    Audits universe constituent data to prevent Lookahead Bias (using future data)
    and Survivorship Bias (implicitly dropping delisted assets).
    """
    def __init__(self):
        pass

    def audit_universe_snapshot(
        self, 
        snapshot_date: datetime, 
        constituents: List[ConstituentRecord]
    ) -> AuditResult:
        lookahead = []
        survivorship = []
        
        has_delisted = any(c.removed_date is not None for c in constituents)
        if not has_delisted and len(constituents) > 50:
            survivorship.append(
                "WARNING: Large universe with zero delisted/removed assets detected. "
                "High probability of Survivorship Bias (using current active constituents only)."
            )

        for c in constituents:
            # 1. Lookahead Bias: The data used to declare them a constituent was published AFTER the snapshot.
            if c.data_publication_date > snapshot_date:
                lookahead.append(
                    f"[{c.symbol}] Lookahead Leak: Data published on {c.data_publication_date.date()} "
                    f"used for {snapshot_date.date()} snapshot."
                )
            
            # 2. Point-in-Time Logic: Ensure they were actually in the index at the snapshot time.
            if c.added_date > snapshot_date:
                lookahead.append(
                    f"[{c.symbol}] Future Addition: Asset was added on {c.added_date.date()} "
                    f"but included in {snapshot_date.date()} snapshot."
                )
                
            if c.removed_date and c.removed_date <= snapshot_date:
                lookahead.append(
                    f"[{c.symbol}] Zombie Asset: Asset was removed on {c.removed_date.date()} "
                    f"but still included in {snapshot_date.date()} snapshot."
                )

        return AuditResult(
            is_clean=(len(lookahead) == 0 and len(survivorship) == 0),
            lookahead_violations=lookahead,
            survivorship_warnings=survivorship
        )
