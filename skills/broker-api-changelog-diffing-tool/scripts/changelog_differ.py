"""
broker-api-changelog-diffing-tool: Release-over-release OpenAPI/Swagger schema differ
for detecting breaking API payload changes in CI/CD pipelines.

Rigorous Institutional Quant Standards Implementation.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

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
    ENUM_MUTATION = "ENUM_MUTATION"
    REMOVED_RESPONSE_FIELD = "REMOVED_RESPONSE_FIELD"
    RESPONSE_TYPE_MUTATION = "RESPONSE_TYPE_MUTATION"

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
    Compares two OpenAPI / JSON API schema snapshots to detect breaking changes,
    incorporating request bodies, response models, parameters, and enum validations.
    """

    def diff_schemas(
        self,
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
        old_version: str = "v1.0",
        new_version: str = "v2.0",
    ) -> APIChangelogReport:
        changes: List[SchemaChange] = []
        old_paths = old_schema.get("paths", {})
        new_paths = new_schema.get("paths", {})

        # 1. Removed or modified endpoints
        for path, old_methods in old_paths.items():
            if path not in new_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_ENDPOINT,
                    severity=DiffSeverity.CRITICAL_BREAKING,
                    path=path,
                    description=f"Endpoint '{path}' was removed in {new_version}."
                ))
            else:
                new_methods = new_paths[path]
                for method, old_details in old_methods.items():
                    if method not in new_methods:
                        changes.append(SchemaChange(
                            change_type=ChangeType.REMOVED_ENDPOINT,
                            severity=DiffSeverity.CRITICAL_BREAKING,
                            path=f"{method.upper()} {path}",
                            description=f"Method '{method.upper()}' removed from endpoint '{path}'."
                        ))
                    else:
                        new_details = new_methods[method]
                        changes.extend(self._diff_endpoint_details(
                            f"{method.upper()} {path}",
                            old_details,
                            new_details
                        ))

        # 2. Added endpoints
        for path, new_methods in new_paths.items():
            if path not in old_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_ENDPOINT,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=path,
                    description=f"New endpoint '{path}' added in {new_version}."
                ))
            else:
                old_methods = old_paths[path]
                for method in new_methods.keys():
                    if method not in old_methods:
                        changes.append(SchemaChange(
                            change_type=ChangeType.ADDED_ENDPOINT,
                            severity=DiffSeverity.NON_BREAKING_INFO,
                            path=f"{method.upper()} {path}",
                            description=f"New method '{method.upper()}' added to endpoint '{path}'."
                        ))

        breaking_count = sum(1 for c in changes if c.severity != DiffSeverity.NON_BREAKING_INFO)
        is_compatible = (breaking_count == 0)

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
        changes.extend(self._diff_parameters(path, old_details.get("parameters", []), new_details.get("parameters", [])))
        changes.extend(self._diff_request_body(path, old_details.get("requestBody", {}), new_details.get("requestBody", {})))
        changes.extend(self._diff_responses(path, old_details.get("responses", {}), new_details.get("responses", {})))
        return changes

    def _diff_parameters(self, path: str, old_params: List[Dict[str, Any]], new_params: List[Dict[str, Any]]) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_param_map = {p["name"]: p for p in old_params if "name" in p}
        new_param_map = {p["name"]: p for p in new_params if "name" in p}

        for param_name, old_p in old_param_map.items():
            if param_name not in new_param_map:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_FIELD,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{path} -> parameter:{param_name}",
                    description=f"Parameter '{param_name}' removed."
                ))
            else:
                new_p = new_param_map[param_name]
                changes.extend(self._diff_schema_types(
                    f"{path} -> parameter:{param_name}",
                    old_p.get("schema", old_p),
                    new_p.get("schema", new_p),
                    is_response=False
                ))

        for param_name, new_p in new_param_map.items():
            if param_name not in old_param_map:
                if new_p.get("required", False):
                    changes.append(SchemaChange(
                        change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                        severity=DiffSeverity.MEDIUM_BREAKING,
                        path=f"{path} -> parameter:{param_name}",
                        description=f"New MANDATORY parameter '{param_name}' added."
                    ))
                else:
                    changes.append(SchemaChange(
                        change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                        severity=DiffSeverity.NON_BREAKING_INFO,
                        path=f"{path} -> parameter:{param_name}",
                        description=f"New optional parameter '{param_name}' added."
                    ))

        return changes

    def _diff_request_body(self, path: str, old_body: Dict[str, Any], new_body: Dict[str, Any]) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_content = old_body.get("content", {})
        new_content = new_body.get("content", {})

        for content_type, old_media in old_content.items():
            if content_type in new_content:
                changes.extend(self._diff_schema_types(
                    f"{path} -> requestBody:{content_type}",
                    old_media.get("schema", {}),
                    new_content[content_type].get("schema", {}),
                    is_response=False
                ))
        return changes

    def _diff_responses(self, path: str, old_resps: Dict[str, Any], new_resps: Dict[str, Any]) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        for status, old_resp in old_resps.items():
            if status in new_resps:
                old_content = old_resp.get("content", {})
                new_content = new_resps[status].get("content", {})
                for content_type, old_media in old_content.items():
                    if content_type in new_content:
                        changes.extend(self._diff_schema_types(
                            f"{path} -> response:{status}:{content_type}",
                            old_media.get("schema", {}),
                            new_content[content_type].get("schema", {}),
                            is_response=True
                        ))
        return changes

    def _diff_schema_types(self, context_path: str, old_schema: Dict[str, Any], new_schema: Dict[str, Any], is_response: bool) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_type = old_schema.get("type")
        new_type = new_schema.get("type")

        if old_type and new_type and old_type != new_type:
            changes.append(SchemaChange(
                change_type=ChangeType.RESPONSE_TYPE_MUTATION if is_response else ChangeType.TYPE_MUTATION,
                severity=DiffSeverity.HIGH_BREAKING,
                path=context_path,
                description=f"Type mutated from '{old_type}' to '{new_type}'."
            ))

        old_enum = old_schema.get("enum")
        new_enum = new_schema.get("enum")
        if old_enum and new_enum:
            removed_enums = set(old_enum) - set(new_enum)
            if removed_enums:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=context_path,
                    description=f"Enum values removed: {list(removed_enums)}."
                ))

        if old_type == "object" and new_type == "object":
            old_props = old_schema.get("properties", {})
            new_props = new_schema.get("properties", {})
            old_required = set(old_schema.get("required", []))
            new_required = set(new_schema.get("required", []))

            for prop, old_prop_schema in old_props.items():
                if prop not in new_props:
                    if is_response:
                        changes.append(SchemaChange(
                            change_type=ChangeType.REMOVED_RESPONSE_FIELD,
                            severity=DiffSeverity.HIGH_BREAKING,
                            path=f"{context_path}.{prop}",
                            description=f"Response field '{prop}' removed."
                        ))
                    else:
                        changes.append(SchemaChange(
                            change_type=ChangeType.REMOVED_FIELD,
                            severity=DiffSeverity.HIGH_BREAKING,
                            path=f"{context_path}.{prop}",
                            description=f"Request field '{prop}' removed."
                        ))
                else:
                    changes.extend(self._diff_schema_types(
                        f"{context_path}.{prop}",
                        old_prop_schema,
                        new_props[prop],
                        is_response
                    ))

            for prop, new_prop_schema in new_props.items():
                if prop not in old_props:
                    if not is_response and prop in new_required:
                        changes.append(SchemaChange(
                            change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                            severity=DiffSeverity.MEDIUM_BREAKING,
                            path=f"{context_path}.{prop}",
                            description=f"New MANDATORY field '{prop}' added."
                        ))
                    elif not is_response:
                        changes.append(SchemaChange(
                            change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                            severity=DiffSeverity.NON_BREAKING_INFO,
                            path=f"{context_path}.{prop}",
                            description=f"New optional field '{prop}' added."
                        ))
        elif old_type == "array" and new_type == "array":
            old_items = old_schema.get("items", {})
            new_items = new_schema.get("items", {})
            changes.extend(self._diff_schema_types(
                f"{context_path}[items]",
                old_items,
                new_items,
                is_response
            ))

        return changes
