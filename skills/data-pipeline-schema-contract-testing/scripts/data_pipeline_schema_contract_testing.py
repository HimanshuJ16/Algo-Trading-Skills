"""Schema contract enforcement for market data ingestion pipelines.

Validates raw vendor payloads (field presence, type, nullability, numeric bounds,
non-finite values, and unknown-field schema drift) before they reach feature
stores, backtests, or execution algorithms. Non-compliant records are routed to a
Dead Letter Queue (DLQ) rather than silently dropped.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)

# Numeric types subject to bounds and finiteness checks.
_NUMERIC_TYPES = (int, float)


@dataclass
class FieldSpec:
    """Contract for a single field within an incoming record.

    Attributes:
        field_name: Key expected in the raw payload.
        expected_type: Python type the value must be an instance of. ``int``
            values are widened to ``float`` when ``expected_type`` is ``float``
            (JSON emits ``100`` for a round price), but ``bool`` is never
            accepted for a numeric field.
        is_nullable: Whether ``None`` is a permitted value for this field.
        min_value: Inclusive lower bound for numeric fields.
        max_value: Inclusive upper bound for numeric fields.
        allow_non_finite: When False (default), NaN/+Inf/-Inf are rejected for
            numeric fields. Enable only for feeds that use non-finite sentinels
            deliberately.
    """
    field_name: str
    expected_type: Type                 # e.g. float, int, str
    is_nullable: bool = False
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allow_non_finite: bool = False


@dataclass
class SchemaContract:
    """Versioned schema contract applied to a batch of records.

    Attributes:
        max_allowed_null_pct: Maximum tolerated null rate **in percent (0-100)**
            for any single nullable field, measured across the records that pass
            per-record validation (i.e. the records that actually reach the
            downstream pipeline). This is an operational policy knob, not an
            externally mandated threshold -- see ``references/standards.md``.
        forbid_unknown_fields: When True, a record carrying any field absent from
            ``field_specs`` is quarantined. When False (default), unknown fields
            are still reported as drift signals but do not fail the record.
    """
    contract_name: str
    contract_version: str
    field_specs: List[FieldSpec]
    max_allowed_null_pct: float = 1.0
    forbid_unknown_fields: bool = False


@dataclass
class QuarantinedRecord:
    """A single record routed to the Dead Letter Queue.

    ``raw_payload`` is a shallow copy taken at validation time so that later
    mutation of the caller's record cannot rewrite DLQ forensic evidence. Nested
    mutable values are still shared; market data records are normally flat.
    """
    record_index: int
    raw_payload: Any
    violation_reason: str               # All violations for this record, '; '-joined
    violations: List[str] = field(default_factory=list)


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
    null_pct_by_field: Dict[str, float] = field(default_factory=dict)
    null_breach_fields: List[str] = field(default_factory=list)
    observed_unknown_fields: List[str] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)


class DataSchemaContractVerifier:
    """
    Quantitative data quality engine for enforcing schema contracts (field types, nullability,
    value bounds, and schema drift) on incoming market data feeds and feature stores.
    """

    def __init__(self, contract: SchemaContract) -> None:
        self._validate_contract(contract)
        self.contract = contract
        self.spec_map = {f.field_name: f for f in contract.field_specs}

    @staticmethod
    def _validate_contract(contract: SchemaContract) -> None:
        """Fails fast on a misconfigured contract rather than silently passing every record."""
        if not contract.field_specs:
            raise ValueError("SchemaContract must declare at least one FieldSpec.")

        seen = set()
        for spec in contract.field_specs:
            if not spec.field_name:
                raise ValueError("FieldSpec.field_name must be a non-empty string.")
            if spec.field_name in seen:
                raise ValueError(f"Duplicate FieldSpec for field '{spec.field_name}'.")
            seen.add(spec.field_name)
            if not isinstance(spec.expected_type, type):
                raise ValueError(
                    f"FieldSpec.expected_type for '{spec.field_name}' must be a type."
                )
            if (spec.min_value is not None and spec.max_value is not None
                    and spec.min_value > spec.max_value):
                raise ValueError(
                    f"FieldSpec '{spec.field_name}' has min_value {spec.min_value} > "
                    f"max_value {spec.max_value}."
                )

        if not 0.0 <= contract.max_allowed_null_pct <= 100.0:
            raise ValueError(
                "SchemaContract.max_allowed_null_pct must be a percentage in [0, 100], "
                f"got {contract.max_allowed_null_pct}."
            )

    def validate_batch(self, raw_records: List[Dict[str, Any]]) -> SchemaContractValidationReport:
        """
        Validates a batch of market data records against the SchemaContract definition,
        routing non-compliant payloads to the Dead Letter Queue (DLQ).

        A batch is valid only if every record passes per-record validation AND no
        nullable field breaches ``max_allowed_null_pct`` across the surviving records.
        """
        valid_records: List[Dict[str, Any]] = []
        quarantined: List[QuarantinedRecord] = []
        unknown_fields = set()

        for idx, rec in enumerate(raw_records):
            if not isinstance(rec, dict):
                # A malformed payload must not abort the batch loop.
                reason = f"Record is not a mapping: got {type(rec).__name__}"
                quarantined.append(QuarantinedRecord(
                    record_index=idx, raw_payload=rec,
                    violation_reason=reason, violations=[reason],
                ))
                logger.warning("SCHEMA CONTRACT VIOLATION [Record #%d]: %s", idx, reason)
                continue

            record_unknown = [k for k in rec if k not in self.spec_map]
            unknown_fields.update(record_unknown)

            violations = self._validate_single_record(rec, record_unknown)
            if not violations:
                valid_records.append(rec)
            else:
                reason = "; ".join(violations)
                quarantined.append(QuarantinedRecord(
                    record_index=idx,
                    raw_payload=dict(rec),      # snapshot: DLQ evidence must not alias the caller
                    violation_reason=reason,
                    violations=violations,
                ))
                logger.warning("SCHEMA CONTRACT VIOLATION [Record #%d]: %s", idx, reason)

        total_count = len(raw_records)
        quarantined_count = len(quarantined)
        valid_count = len(valid_records)

        null_pct_by_field, null_breach_fields = self._compute_null_rates(valid_records)

        compliance_rate = (
            round((valid_count / float(total_count)) * 100.0, 2) if total_count > 0 else 100.0
        )
        is_batch_ok = quarantined_count == 0 and not null_breach_fields

        alerts: List[str] = []
        for fname in null_breach_fields:
            msg = (f"NULL CEILING BREACH [{fname}]: {null_pct_by_field[fname]:.2f}% nulls "
                   f"exceeds max_allowed_null_pct {self.contract.max_allowed_null_pct}%.")
            alerts.append(msg)
            logger.warning(msg)

        if unknown_fields:
            msg = (f"SCHEMA DRIFT [{self.contract.contract_name} "
                   f"{self.contract.contract_version}]: undeclared field(s) "
                   f"{sorted(unknown_fields)} present in payloads.")
            alerts.append(msg)
            logger.warning(msg)

        return SchemaContractValidationReport(
            contract_name=self.contract.contract_name,
            contract_version=self.contract.contract_version,
            total_records_processed=total_count,
            valid_records_count=valid_count,
            quarantined_records_count=quarantined_count,
            quarantined_records=quarantined,
            is_batch_valid=is_batch_ok,
            compliance_rate_pct=compliance_rate,
            null_pct_by_field=null_pct_by_field,
            null_breach_fields=null_breach_fields,
            observed_unknown_fields=sorted(unknown_fields),
            alerts=alerts,
        )

    def _compute_null_rates(
        self, valid_records: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, float], List[str]]:
        """Null rate per nullable field, measured over records that reach the pipeline.

        Non-nullable nulls are already quarantined per-record, so they cannot appear
        here; this metric therefore tracks the nulls that actually land downstream.
        """
        null_pct_by_field: Dict[str, float] = {}
        breaches: List[str] = []
        denom = len(valid_records)

        for spec in self.contract.field_specs:
            if not spec.is_nullable:
                continue
            if denom == 0:
                null_pct_by_field[spec.field_name] = 0.0
                continue
            nulls = sum(1 for r in valid_records if r.get(spec.field_name) is None)
            pct = round((nulls / float(denom)) * 100.0, 4)
            null_pct_by_field[spec.field_name] = pct
            if pct > self.contract.max_allowed_null_pct:
                breaches.append(spec.field_name)

        return null_pct_by_field, breaches

    def _validate_single_record(self, rec: Dict[str, Any],
                                unknown_fields: List[str]) -> List[str]:
        """Returns every contract violation found in ``rec`` (empty list if compliant)."""
        violations: List[str] = []

        if unknown_fields and self.contract.forbid_unknown_fields:
            violations.append(f"Undeclared field(s) present: {sorted(unknown_fields)}")

        for spec in self.contract.field_specs:
            fname = spec.field_name
            if fname not in rec:
                violations.append(f"Missing required field '{fname}'")
                continue

            val = rec[fname]
            if val is None:
                if not spec.is_nullable:
                    violations.append(
                        f"Null value not permitted for non-nullable field '{fname}'"
                    )
                continue

            # bool is a subclass of int: never let JSON true/false satisfy a numeric field.
            if isinstance(val, bool) and spec.expected_type is not bool:
                violations.append(
                    f"Type mismatch for field '{fname}': expected "
                    f"{spec.expected_type.__name__}, got bool"
                )
                continue

            # Type Validation (strict, float can accept int)
            if spec.expected_type is float and isinstance(val, int):
                val = float(val)
            elif not isinstance(val, spec.expected_type):
                violations.append(
                    f"Type mismatch for field '{fname}': expected "
                    f"{spec.expected_type.__name__}, got {type(val).__name__}"
                )
                continue

            if isinstance(val, _NUMERIC_TYPES):
                # NaN/Inf silently defeat every bounds comparison below, so reject first.
                if not spec.allow_non_finite and not math.isfinite(val):
                    violations.append(f"Non-finite value for field '{fname}': {val}")
                    continue
                if spec.min_value is not None and val < spec.min_value:
                    violations.append(
                        f"Out of bounds for field '{fname}': value {val} < "
                        f"min_value {spec.min_value}"
                    )
                if spec.max_value is not None and val > spec.max_value:
                    violations.append(
                        f"Out of bounds for field '{fname}': value {val} > "
                        f"max_value {spec.max_value}"
                    )

        return violations
