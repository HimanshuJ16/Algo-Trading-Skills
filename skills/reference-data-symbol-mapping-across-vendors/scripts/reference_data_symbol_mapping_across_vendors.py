import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str

@dataclass
class SymbolMappingConfig:
    case_sensitive: bool = False
    allow_ambiguous: bool = False

@dataclass
class VendorSymbolEntry:
    canonical_symbol: str
    vendor_name: str
    vendor_symbol: str
    identifier_type: str                 # 'TICKER', 'RIC', 'ISIN', 'CUSIP', 'SEDOL', 'BBG'

@dataclass
class SymbolMappingCoverageReport:
    total_canonical_symbols: int
    total_mappings: int
    vendors_covered: List[str]
    unmapped_canonical: List[str]
    ambiguous_mappings: List[str]
    status: str                          # 'FULL_COVERAGE', 'PARTIAL_COVERAGE'
    audit_notes: str

class Engine:
    """Legacy engine retained for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def process(self, data):
        return data

class SymbolMappingEngine:
    """
    Cross-vendor symbol mapping engine resolving instrument identifier discrepancies
    across data vendors into a unified canonical symbol.
    """
    def __init__(self, config: Optional[SymbolMappingConfig] = None):
        self.config = config or SymbolMappingConfig()
        # forward: (vendor_name, vendor_symbol) -> canonical_symbol
        self._forward: Dict[Tuple[str, str], str] = {}
        # reverse: (canonical_symbol, vendor_name) -> vendor_symbol
        self._reverse: Dict[Tuple[str, str], str] = {}
        self._canonical_symbols: set = set()
        self._vendors: set = set()
        self._entries: List[VendorSymbolEntry] = []

    def _normalize(self, s: str) -> str:
        return s if self.config.case_sensitive else s.upper()

    def register_mapping(self, entry: VendorSymbolEntry) -> None:
        """Registers a vendor-specific symbol mapping to a canonical symbol."""
        canonical = self._normalize(entry.canonical_symbol)
        vendor = self._normalize(entry.vendor_name)
        vsym = self._normalize(entry.vendor_symbol)

        fwd_key = (vendor, vsym)
        existing = self._forward.get(fwd_key)
        if existing is not None and existing != canonical and not self.config.allow_ambiguous:
            logger.warning(
                f"Ambiguous mapping: ({entry.vendor_name}, {entry.vendor_symbol}) "
                f"already maps to '{existing}', attempted re-map to '{canonical}'."
            )

        self._forward[fwd_key] = canonical
        self._reverse[(canonical, vendor)] = vsym
        self._canonical_symbols.add(canonical)
        self._vendors.add(vendor)
        self._entries.append(entry)

    def forward_lookup(self, vendor_name: str, vendor_symbol: str) -> Optional[str]:
        """Resolves vendor-specific symbol to canonical symbol."""
        key = (self._normalize(vendor_name), self._normalize(vendor_symbol))
        result = self._forward.get(key)
        if result is None:
            logger.debug(f"Forward lookup miss: {vendor_name}/{vendor_symbol}")
        return result

    def reverse_lookup(self, canonical_symbol: str, vendor_name: str) -> Optional[str]:
        """Resolves canonical symbol to vendor-specific identifier."""
        key = (self._normalize(canonical_symbol), self._normalize(vendor_name))
        result = self._reverse.get(key)
        if result is None:
            logger.debug(f"Reverse lookup miss: {canonical_symbol}/{vendor_name}")
        return result

    def get_coverage_report(self, expected_canonical: Optional[List[str]] = None) -> SymbolMappingCoverageReport:
        """Generates a coverage report for registered mappings."""
        expected = set(self._normalize(s) for s in expected_canonical) if expected_canonical else self._canonical_symbols
        unmapped = sorted(expected - self._canonical_symbols)
        total_mappings = len(self._entries)
        vendors = sorted(self._vendors)

        # Detect ambiguous forward mappings
        fwd_counts: Dict[Tuple[str, str], set] = {}
        for e in self._entries:
            k = (self._normalize(e.vendor_name), self._normalize(e.vendor_symbol))
            fwd_counts.setdefault(k, set()).add(self._normalize(e.canonical_symbol))
        ambiguous = [f"{k[0]}/{k[1]}" for k, v in fwd_counts.items() if len(v) > 1]

        has_gaps = len(unmapped) > 0 or len(ambiguous) > 0
        status = "PARTIAL_COVERAGE" if has_gaps else "FULL_COVERAGE"

        notes = (
            f"SYMBOL MAPPING [{status}]: "
            f"Canonical = {len(self._canonical_symbols)}, Mappings = {total_mappings}, "
            f"Vendors = {len(vendors)}, Unmapped = {len(unmapped)}, "
            f"Ambiguous = {len(ambiguous)}."
        )

        if has_gaps:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SymbolMappingCoverageReport(
            total_canonical_symbols=len(self._canonical_symbols),
            total_mappings=total_mappings,
            vendors_covered=vendors,
            unmapped_canonical=unmapped,
            ambiguous_mappings=ambiguous,
            status=status,
            audit_notes=notes
        )
