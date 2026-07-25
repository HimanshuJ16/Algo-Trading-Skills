"""
broker-api-changelog-diffing-tool: Release-over-release OpenAPI/Swagger schema differ
for detecting breaking API payload changes in CI/CD pipelines.
"""
from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class DiffSeverity(Enum):
    CRITICAL_BREAKING = "CRITICAL_BREAKING"
    HIGH_BREAKING = "HIGH_BREAKING"
    MEDIUM_BREAKING = "MEDIUM_BREAKING"
    NON_BREAKING_INFO = "NON_BREAKING_INFO"


class ChangeType(Enum):
    REMOVED_ENDPOINT = "REMOVED_ENDPOINT"
    REMOVED_FIELD = "REMOVED_FIELD"
    TYPE_MUTATION = "TYPE_MUTATION"
    NEW_REQUIRED_PARAMETER = "NEW_REQUIRED_PARAMETER"
    ADDED_ENDPOINT = "ADDED_ENDPOINT"
    ADDED_OPTIONAL_FIELD = "ADDED_OPTIONAL_FIELD"


@dataclass
class SchemaChange:
    change_type: ChangeType
    severity: DiffSeverity
    path: str
    description: str


@dataclass
class APIChangelogReport:
    old_version: str
    new_version: str
    is_compatible: bool
    total_breaking_changes: int
    changes: List[SchemaChange]


class BrokerAPIChangelogDiffer:
    """
    Compares two OpenAPI / JSON API schema snapshots to detect breaking changes.
    """

    def diff_schemas(
        self,
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
        old_version: str = "v1.0",
        new_version: str = "v2.0",
    ) -> APIChangelogReport:
        """
        Diffs old_schema against new_schema and produces a breaking change report.
        """
        changes: List[SchemaChange] = []

        old_paths = old_schema.get("paths", {})
        new_paths = new_schema.get("paths", {})

        # 1. Check for Removed Endpoints (Critical Breaking)
        for path, old_methods in old_paths.items():
            if path not in new_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_ENDPOINT,
                    severity=DiffSeverity.CRITICAL_BREAKING,
                    path=path,
                    description=f"Endpoint '{path}' was removed in {new_version}.",
                ))
            else:
                new_methods = new_paths[path]
                for method, old_details in old_methods.items():
                    if method not in new_methods:
                        changes.append(SchemaChange(
                            change_type=ChangeType.REMOVED_ENDPOINT,
                            severity=DiffSeverity.CRITICAL_BREAKING,
                            path=f"{method.upper()} {path}",
                            description=f"Method '{method.upper()}' removed from endpoint '{path}'.",
                        ))
                    else:
                        new_details = new_methods[method]
                        # Diff parameters / schemas
                        changes.extend(self._diff_endpoint_details(f"{method.upper()} {path}", old_details, new_details))

        # 2. Check for Added Endpoints (Non-breaking Info)
        for path in new_paths:
            if path not in old_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_ENDPOINT,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=path,
                    description=f"New endpoint '{path}' added in {new_version}.",
                ))

        breaking_count = sum(1 for c in changes if c.severity != DiffSeverity.NON_BREAKING_INFO)
        is_compatible = breaking_count == 0

        if not is_compatible:
            logger.warning(f"API Schema Diff: {breaking_count} breaking changes detected between {old_version} and {new_version}.")

        return APIChangelogReport(
            old_version=old_version,
            new_version=new_version,
            is_compatible=is_compatible,
            total_breaking_changes=breaking_count,
            changes=changes,
        )

    def _diff_endpoint_details(self, path: str, old_details: Dict[str, Any], new_details: Dict[str, Any]) -> List[SchemaChange]:
        changes: List[SchemaChange] = []

        old_params = {p.get("name"): p for p in old_details.get("parameters", []) if "name" in p}
        new_params = {p.get("name"): p for p in new_details.get("parameters", []) if "name" in p}

        # Check removed parameters (High Breaking)
        for param_name, old_p in old_params.items():
            if param_name not in new_params:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_FIELD,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{path} -> parameter:{param_name}",
                    description=f"Parameter '{param_name}' removed.",
                ))
            else:
                new_p = new_params[param_name]
                old_type = old_p.get("type") or old_p.get("schema", {}).get("type")
                new_type = new_p.get("type") or new_p.get("schema", {}).get("type")

                if old_type and new_type and old_type != new_type:
                    changes.append(SchemaChange(
                        change_type=ChangeType.TYPE_MUTATION,
                        severity=DiffSeverity.HIGH_BREAKING,
                        path=f"{path} -> parameter:{param_name}",
                        description=f"Parameter '{param_name}' type mutated from '{old_type}' to '{new_type}'.",
                    ))

        # Check new required parameters (Medium Breaking)
        for param_name, new_p in new_params.items():
            if param_name not in old_params:
                if new_p.get("required", False):
                    changes.append(SchemaChange(
                        change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                        severity=DiffSeverity.MEDIUM_BREAKING,
                        path=f"{path} -> parameter:{param_name}",
                        description=f"New MANDATORY parameter '{param_name}' added.",
                    ))
                else:
                    changes.append(SchemaChange(
                        change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                        severity=DiffSeverity.NON_BREAKING_INFO,
                        path=f"{path} -> parameter:{param_name}",
                        description=f"New optional parameter '{param_name}' added.",
                    ))

        return changes
