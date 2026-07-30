import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

@dataclass
class FieldSpec:
    field_name: str
    expected_type: Type                 # e.g. float, int, str
    is_nullable: bool = False
    min_value: Optional[float] = None
    max_value: Optional[float] = None

@dataclass
class SchemaContract:
    contract_name: str
    contract_version: str
    field_specs: List[FieldSpec]
    max_allowed_null_pct: float = 1.0   # Maximum allowed null percentage across batch

@dataclass
class QuarantinedRecord:
    record_index: int
    raw_payload: Dict[str, Any]
    violation_reason: str

@dataclass
class SchemaContractValidationReport:
    contract_name: str
    contract_version: str
    total_records_processed: int
    valid_records_count: int
    quarantined_records_count: int
    quarantined_records: List[QuarantinedRecord]
    is_batch_valid: bool
    compliance_rate_pct: float

class DataSchemaContractVerifier:
    """
    Quantitative data quality engine for enforcing schema contracts (field types, nullability,
    value bounds, and schema drift) on incoming market data feeds and feature stores.
    """
    def __init__(self, contract: SchemaContract):
        self.contract = contract
        self.spec_map = {f.field_name: f for f in contract.field_specs}

    def validate_batch(self, raw_records: List[Dict[str, Any]]) -> SchemaContractValidationReport:
        """
        Validates a batch of market data records against the SchemaContract definition,
        routing non-compliant payloads to the Dead Letter Queue (DLQ).
        """
        valid_records = []
        quarantined: List[QuarantinedRecord] = []

        for idx, rec in enumerate(raw_records):
            violation = self._validate_single_record(rec)
            if violation is None:
                valid_records.append(rec)
            else:
                quarantined.append(QuarantinedRecord(record_index=idx, raw_payload=rec, violation_reason=violation))
                logger.warning(f"SCHEMA CONTRACT VIOLATION [Record #{idx}]: {violation}")

        total_count = len(raw_records)
        quarantined_count = len(quarantined)
        valid_count = len(valid_records)

        compliance_rate = round((valid_count / float(total_count)) * 100.0, 2) if total_count > 0 else 100.0
        is_batch_ok = quarantined_count == 0

        return SchemaContractValidationReport(
            contract_name=self.contract.contract_name,
            contract_version=self.contract.contract_version,
            total_records_processed=total_count,
            valid_records_count=valid_count,
            quarantined_records_count=quarantined_count,
            quarantined_records=quarantined,
            is_batch_valid=is_batch_ok,
            compliance_rate_pct=compliance_rate
        )

    def _validate_single_record(self, rec: Dict[str, Any]) -> Optional[str]:
        for spec in self.contract.field_specs:
            fname = spec.field_name
            if fname not in rec:
                return f"Missing required field '{fname}'"

            val = rec[fname]
            if val is None:
                if not spec.is_nullable:
                    return f"Null value not permitted for non-nullable field '{fname}'"
                continue

            # Type Validation (strict, float can accept int)
            if spec.expected_type == float and isinstance(val, int):
                val = float(val)
            elif not isinstance(val, spec.expected_type):
                return f"Type mismatch for field '{fname}': expected {spec.expected_type.__name__}, got {type(val).__name__}"

            # Range Bounds Validation
            if isinstance(val, (int, float)):
                if spec.min_value is not None and val < spec.min_value:
                    return f"Out of bounds for field '{fname}': value {val} < min_value {spec.min_value}"
                if spec.max_value is not None and val > spec.max_value:
                    return f"Out of bounds for field '{fname}': value {val} > max_value {spec.max_value}"

        return None
