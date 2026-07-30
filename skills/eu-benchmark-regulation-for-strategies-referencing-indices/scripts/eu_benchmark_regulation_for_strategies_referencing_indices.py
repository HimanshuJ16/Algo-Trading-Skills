import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class BenchmarkSpec:
    benchmark_id: str
    benchmark_name: str                 # e.g. 'EURO STOXX 50', 'EURIBOR 3M', 'CUSTOM_UNLICENSED_INDEX'
    administrator_name: str             # e.g. 'STOXX Ltd', 'EMMI'
    is_esma_registered: bool            # Listed on ESMA BMR Register
    category: str                       # 'CRITICAL', 'SIGNIFICANT', 'NON_SIGNIFICANT'
    fallback_benchmark_name: str        # e.g. 'STOXX Europe 600' or '€STR + 0.139%'

@dataclass
class StrategyBenchmarkUsage:
    strategy_id: str
    strategy_name: str
    referenced_benchmark_id: str
    has_written_fallback_plan: bool     # Article 28(2) requirement

@dataclass
class EuBmrAuditReport:
    strategy_id: str
    benchmark_name: str
    administrator_name: str
    is_esma_registered: bool
    has_written_fallback_plan: bool
    compliance_status: str              # 'BMR_COMPLIANT', 'UNAUTHORIZED_BENCHMARK_VIOLATION', 'MISSING_FALLBACK_PLAN_VIOLATION'
    fallback_benchmark_name: str
    audit_notes: str

class EuBmrComplianceEngine:
    """
    Quantitative European regulatory compliance engine for auditing EU Benchmark Regulation
    (EU BMR - Regulation 2016/1011) authorization, ESMA register status, and Article 28(2) robust written fallback provisions.
    """
    def __init__(self):
        self.benchmarks: Dict[str, BenchmarkSpec] = {}

    def register_benchmark(self, spec: BenchmarkSpec):
        self.benchmarks[spec.benchmark_id] = spec

    def audit_strategy_bmr_compliance(self, usage: StrategyBenchmarkUsage) -> EuBmrAuditReport:
        """
        Audits strategy index referencing against ESMA BMR Register and Article 28(2) fallback requirements.
        """
        bm = self.benchmarks.get(usage.referenced_benchmark_id)

        if bm is None:
            msg = f"UNKNOWN BENCHMARK [{usage.strategy_id}]: Referenced benchmark ID '{usage.referenced_benchmark_id}' not found in registry."
            logger.error(msg)
            return EuBmrAuditReport(
                strategy_id=usage.strategy_id, benchmark_name="UNKNOWN", administrator_name="UNKNOWN",
                is_esma_registered=False, has_written_fallback_plan=usage.has_written_fallback_plan,
                compliance_status="UNAUTHORIZED_BENCHMARK_VIOLATION", fallback_benchmark_name="NONE", audit_notes=msg
            )

        # 1. Audit ESMA BMR Register Authorization
        if not bm.is_esma_registered:
            msg = f"UNAUTHORIZED BENCHMARK VIOLATION [{usage.strategy_id}]: Benchmark '{bm.benchmark_name}' (Administrator: {bm.administrator_name}) is NOT listed on the ESMA BMR Register!"
            logger.critical(msg)
            return EuBmrAuditReport(
                strategy_id=usage.strategy_id, benchmark_name=bm.benchmark_name, administrator_name=bm.administrator_name,
                is_esma_registered=False, has_written_fallback_plan=usage.has_written_fallback_plan,
                compliance_status="UNAUTHORIZED_BENCHMARK_VIOLATION", fallback_benchmark_name=bm.fallback_benchmark_name, audit_notes=msg
            )

        # 2. Audit Article 28(2) Robust Written Fallback Plan
        if not usage.has_written_fallback_plan:
            msg = f"MISSING FALLBACK PLAN VIOLATION [{usage.strategy_id}]: Strategy references '{bm.benchmark_name}' without documented BMR Article 28(2) written fallback provisions!"
            logger.error(msg)
            return EuBmrAuditReport(
                strategy_id=usage.strategy_id, benchmark_name=bm.benchmark_name, administrator_name=bm.administrator_name,
                is_esma_registered=True, has_written_fallback_plan=False,
                compliance_status="MISSING_FALLBACK_PLAN_VIOLATION", fallback_benchmark_name=bm.fallback_benchmark_name, audit_notes=msg
            )

        notes = f"EU BMR COMPLIANT [{usage.strategy_id}]: Benchmark '{bm.benchmark_name}' is ESMA registered. Article 28(2) fallback plan configured ({bm.fallback_benchmark_name})."
        logger.info(notes)

        return EuBmrAuditReport(
            strategy_id=usage.strategy_id,
            benchmark_name=bm.benchmark_name,
            administrator_name=bm.administrator_name,
            is_esma_registered=True,
            has_written_fallback_plan=True,
            compliance_status="BMR_COMPLIANT",
            fallback_benchmark_name=bm.fallback_benchmark_name,
            audit_notes=notes
        )
