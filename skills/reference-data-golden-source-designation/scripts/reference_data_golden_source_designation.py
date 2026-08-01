import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str

@dataclass
class GoldenSourceConfig:
    # field_name -> ordered list of vendor names (highest priority first)
    priority_rules: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class VendorFieldData:
    vendor_name: str
    fields: Dict[str, Optional[str]]     # field_name -> value (None = not provided)

@dataclass
class FieldResolution:
    field_name: str
    golden_value: Optional[str]
    golden_vendor: Optional[str]
    all_vendor_values: Dict[str, Optional[str]]
    has_conflict: bool                   # True if vendors disagree on value

@dataclass
class GoldenRecordReport:
    instrument_id: str
    golden_record: Dict[str, Optional[str]]
    resolutions: List[FieldResolution]
    total_fields: int
    conflicts_detected: int
    fields_without_data: int
    status: str                          # 'RESOLVED', 'CONFLICTS_FOUND', 'MISSING_DATA'
    audit_notes: str

class Engine:
    """Legacy engine retained for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def process(self, data):
        return data

class GoldenSourceDesignationEngine:
    """
    Golden source designation engine for reference data governance, resolving
    multi-vendor field conflicts by applying priority-ranked authoritative source rules.
    """
    def __init__(self, config: Optional[GoldenSourceConfig] = None):
        self.config = config or GoldenSourceConfig()

    def resolve_golden_record(
        self,
        instrument_id: str,
        vendor_data: List[VendorFieldData]
    ) -> GoldenRecordReport:
        """
        Resolves multi-vendor field conflicts using priority rules to produce
        a single golden record per instrument.
        """
        # Collect all field names across vendors
        all_fields: Set[str] = set()
        for vd in vendor_data:
            all_fields.update(vd.fields.keys())

        # Build vendor value map: field -> {vendor -> value}
        vendor_map: Dict[str, Dict[str, Optional[str]]] = {}
        for f in all_fields:
            vendor_map[f] = {}
            for vd in vendor_data:
                if f in vd.fields:
                    vendor_map[f][vd.vendor_name] = vd.fields[f]

        resolutions: List[FieldResolution] = []
        golden_record: Dict[str, Optional[str]] = {}
        conflicts = 0
        missing = 0

        for f in sorted(all_fields):
            vendors_for_field = vendor_map[f]
            priority_list = self.config.priority_rules.get(f, [])

            # Detect conflicts: distinct non-None values
            non_null_values = {v for v in vendors_for_field.values() if v is not None}
            has_conflict = len(non_null_values) > 1

            if has_conflict:
                conflicts += 1

            # Resolve using priority rules (skip null values)
            golden_value = None
            golden_vendor = None

            for vendor in priority_list:
                val = vendors_for_field.get(vendor)
                if val is not None:
                    golden_value = val
                    golden_vendor = vendor
                    break

            # Fallback: if no priority rule matched, use first non-null from any vendor
            if golden_value is None:
                for vendor, val in vendors_for_field.items():
                    if val is not None:
                        golden_value = val
                        golden_vendor = vendor
                        break

            if golden_value is None:
                missing += 1

            golden_record[f] = golden_value
            resolutions.append(FieldResolution(
                field_name=f,
                golden_value=golden_value,
                golden_vendor=golden_vendor,
                all_vendor_values=dict(vendors_for_field),
                has_conflict=has_conflict
            ))

        total = len(all_fields)
        if conflicts > 0:
            status = "CONFLICTS_FOUND"
        elif missing > 0:
            status = "MISSING_DATA"
        else:
            status = "RESOLVED"

        notes = (
            f"GOLDEN SOURCE [{status}] {instrument_id}: "
            f"Fields = {total}, Conflicts = {conflicts}, Missing = {missing}."
        )

        if conflicts > 0:
            logger.warning(notes)
        else:
            logger.info(notes)

        return GoldenRecordReport(
            instrument_id=instrument_id,
            golden_record=golden_record,
            resolutions=resolutions,
            total_fields=total,
            conflicts_detected=conflicts,
            fields_without_data=missing,
            status=status,
            audit_notes=notes
        )
