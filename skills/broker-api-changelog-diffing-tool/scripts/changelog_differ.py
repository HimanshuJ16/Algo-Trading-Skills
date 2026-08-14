"""
broker-api-changelog-diffing-tool: Release-over-release OpenAPI/Swagger schema differ
for detecting breaking API payload changes in CI/CD pipelines.

The tool is a **gate**, so its failure modes are asymmetric: a false positive costs a
developer five minutes, a false negative ships a broken broker integration into an order
path. Everything below is biased accordingly.

Three properties do most of the work:

  1. **``$ref`` is resolved before comparison.** Real broker specs (Binance, Coinbase,
     IBKR) describe payloads almost entirely through ``$ref``. A ``$ref`` schema carries
     no ``type``, ``properties`` or ``enum`` of its own, so a differ that reads those keys
     directly sees an empty object on both sides and reports "compatible" for a release
     that deleted the entire response model. References are resolved against the document
     they came from — ``#/components/schemas/...`` for OpenAPI 3.x and
     ``#/definitions/...`` for Swagger 2.0 — with cycle protection, since self-referential
     models are common.

  2. **Absence is a change.** A removed response status code, a removed request content
     type, a removed ``requestBody`` and a removed enum constraint are all breaking, and
     all of them are invisible to a differ that only walks keys present on both sides.

  3. **Direction decides severity.** The same edit is breaking in one direction and
     harmless in the other. Adding a value to a *response* enum breaks an exhaustive
     consumer state machine; adding one to a *request* enum does not. Dropping a property
     from a response's ``required`` list breaks consumers that relied on it; dropping one
     from a request's does not. ``is_response`` carries that context through the recursion.

Scope limits:

  - Local (``#/...``) references only. External and remote references are reported as
    ``UNRESOLVED_REF`` rather than skipped silently — an unresolvable ref means a region
    of the schema was **not** compared, and the caller must know that.
  - Structural diffing only. It does not evaluate ``oneOf``/``anyOf``/``allOf``
    composition, nor semantic changes (rate limits, auth scopes, business rules) that a
    schema cannot express. A clean report is not a promise that the release is safe.
  - Type comparison is deliberately conservative: any difference in the normalized type
    set is reported, including widenings that a permissive client would tolerate.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: Path Item Object keys that are operations. Everything else legally appearing beside
#: them ("parameters", "servers", "summary", "description", "$ref") is not a method, and
#: treating it as one raises AttributeError on a perfectly valid specification.
HTTP_METHODS: FrozenSet[str] = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


class SchemaDiffError(ValueError):
    """Raised when a supplied document is not a usable API schema."""


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
    REMOVED_RESPONSE_CODE = "REMOVED_RESPONSE_CODE"
    REMOVED_CONTENT_TYPE = "REMOVED_CONTENT_TYPE"
    REMOVED_REQUEST_BODY = "REMOVED_REQUEST_BODY"
    REQUIREMENT_MUTATION = "REQUIREMENT_MUTATION"
    UNRESOLVED_REF = "UNRESOLVED_REF"


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

    @property
    def breaking_changes(self) -> List[SchemaChange]:
        return [c for c in self.changes if c.severity is not DiffSeverity.NON_BREAKING_INFO]

    @property
    def exit_code(self) -> int:
        """0 when compatible, 1 otherwise — for use as a CI gate's exit status."""
        return 0 if self.is_compatible else 1

    def format_report(self) -> str:
        """Human-readable summary, breaking changes first."""
        lines = [
            f"API schema diff: {self.old_version} -> {self.new_version}",
            f"  compatible: {self.is_compatible}  "
            f"breaking: {self.total_breaking_changes}  total: {len(self.changes)}",
        ]
        order = {
            DiffSeverity.CRITICAL_BREAKING: 0,
            DiffSeverity.HIGH_BREAKING: 1,
            DiffSeverity.MEDIUM_BREAKING: 2,
            DiffSeverity.NON_BREAKING_INFO: 3,
        }
        for change in sorted(self.changes, key=lambda c: (order[c.severity], c.path)):
            lines.append(
                f"  [{change.severity.value}] {change.change_type.value} "
                f"@ {change.path}: {change.description}"
            )
        return "\n".join(lines)


def _normalize_type(schema: Dict[str, Any]) -> FrozenSet[str]:
    """
    Reduce a schema's declared type(s) to a comparable set.

    OpenAPI 3.1 permits ``type`` to be a list (``["string", "null"]``) where 3.0 used a
    scalar plus ``nullable: true``. Comparing those two spellings directly reports a
    mutation that did not happen, and an equality test against the literal ``"object"``
    silently skips property diffing for ``["object", "null"]``.
    """
    raw = schema.get("type")
    if raw is None:
        types: Set[str] = set()
    elif isinstance(raw, str):
        types = {raw}
    elif isinstance(raw, (list, tuple)):
        types = {t for t in raw if isinstance(t, str)}
    else:
        types = set()
    if schema.get("nullable") is True:
        types.add("null")
    return frozenset(types)


class BrokerAPIChangelogDiffer:
    """
    Compares two OpenAPI / Swagger schema snapshots to detect breaking changes across
    endpoints, parameters, request bodies, response models and enums.
    """

    def diff_schemas(
        self,
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
        old_version: str = "v1.0",
        new_version: str = "v2.0",
    ) -> APIChangelogReport:
        """
        Diff two API schema documents.

        Raises:
            SchemaDiffError: either document is not a mapping, or lacks a usable ``paths``
                object. An empty or malformed document must not produce a clean report —
                a failed download or a wrong path would otherwise pass the gate with zero
                findings.
        """
        self._validate_document(old_schema, "old_schema")
        self._validate_document(new_schema, "new_schema")

        changes: List[SchemaChange] = []
        old_paths = old_schema["paths"]
        new_paths = new_schema["paths"]

        # 1. Removed or modified endpoints
        for path, old_item in old_paths.items():
            if not isinstance(old_item, dict):
                raise SchemaDiffError(
                    f"old_schema paths['{path}'] must be a Path Item Object (mapping), "
                    f"got {type(old_item).__name__}."
                )
            if path not in new_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_ENDPOINT,
                    severity=DiffSeverity.CRITICAL_BREAKING,
                    path=path,
                    description=f"Endpoint '{path}' was removed in {new_version}.",
                ))
                continue

            new_item = new_paths[path]
            if not isinstance(new_item, dict):
                raise SchemaDiffError(
                    f"new_schema paths['{path}'] must be a Path Item Object (mapping), "
                    f"got {type(new_item).__name__}."
                )

            # Path-level parameters apply to every operation on the path.
            changes.extend(self._diff_parameters(
                f"{path} (path-level)",
                old_item.get("parameters", []),
                new_item.get("parameters", []),
                old_schema, new_schema,
            ))

            for method in sorted(k for k in old_item if k.lower() in HTTP_METHODS):
                if method not in new_item:
                    changes.append(SchemaChange(
                        change_type=ChangeType.REMOVED_ENDPOINT,
                        severity=DiffSeverity.CRITICAL_BREAKING,
                        path=f"{method.upper()} {path}",
                        description=f"Method '{method.upper()}' removed from endpoint '{path}'.",
                    ))
                    continue
                changes.extend(self._diff_endpoint_details(
                    f"{method.upper()} {path}",
                    old_item[method], new_item[method],
                    old_schema, new_schema,
                ))

        # 2. Added endpoints
        for path, new_item in new_paths.items():
            if not isinstance(new_item, dict):
                continue
            if path not in old_paths:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_ENDPOINT,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=path,
                    description=f"New endpoint '{path}' added in {new_version}.",
                ))
                continue
            old_item = old_paths[path]
            for method in sorted(k for k in new_item if k.lower() in HTTP_METHODS):
                if method not in old_item:
                    changes.append(SchemaChange(
                        change_type=ChangeType.ADDED_ENDPOINT,
                        severity=DiffSeverity.NON_BREAKING_INFO,
                        path=f"{method.upper()} {path}",
                        description=f"New method '{method.upper()}' added to endpoint '{path}'.",
                    ))

        breaking_count = sum(
            1 for c in changes if c.severity is not DiffSeverity.NON_BREAKING_INFO
        )
        is_compatible = breaking_count == 0

        if not is_compatible:
            logger.warning(
                f"API Schema Diff: {breaking_count} breaking changes detected between "
                f"{old_version} and {new_version}."
            )

        return APIChangelogReport(
            old_version=old_version,
            new_version=new_version,
            is_compatible=is_compatible,
            total_breaking_changes=breaking_count,
            changes=changes,
        )

    # ------------------------------------------------------------------
    # Validation and reference resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_document(document: Any, label: str) -> None:
        if not isinstance(document, dict):
            raise SchemaDiffError(
                f"{label} must be a parsed schema mapping, got {type(document).__name__}."
            )
        paths = document.get("paths")
        if paths is None:
            raise SchemaDiffError(
                f"{label} has no 'paths' object. Refusing to diff: an empty or wrong "
                f"document would otherwise report zero changes and pass the gate."
            )
        if not isinstance(paths, dict):
            raise SchemaDiffError(
                f"{label}['paths'] must be a mapping, got {type(paths).__name__}."
            )

    @staticmethod
    def _resolve_ref(
        schema: Dict[str, Any], document: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Follow a local ``$ref`` to its target.

        Returns ``(resolved_schema, unresolved_ref)``. ``unresolved_ref`` is non-None when
        the reference could not be followed — external/remote references, or a local
        pointer with no target. Such a region is reported rather than skipped: an
        unresolved ref means that part of the schema was never compared.
        """
        seen: Set[str] = set()
        while isinstance(schema, dict) and "$ref" in schema:
            ref = schema["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                return {}, str(ref)
            if ref in seen:
                # Self-referential model; stop rather than recurse forever.
                return {}, None
            seen.add(ref)
            target: Any = document
            for token in ref[2:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or token not in target:
                    return {}, ref
                target = target[token]
            if not isinstance(target, dict):
                return {}, ref
            schema = target
        return schema if isinstance(schema, dict) else {}, None

    # ------------------------------------------------------------------
    # Endpoint-level diffing
    # ------------------------------------------------------------------

    def _diff_endpoint_details(
        self, path: str, old_details: Any, new_details: Any,
        old_doc: Dict[str, Any], new_doc: Dict[str, Any],
    ) -> List[SchemaChange]:
        if not isinstance(old_details, dict) or not isinstance(new_details, dict):
            raise SchemaDiffError(
                f"Operation at '{path}' must be a mapping in both documents."
            )
        changes: List[SchemaChange] = []
        changes.extend(self._diff_parameters(
            path, old_details.get("parameters", []), new_details.get("parameters", []),
            old_doc, new_doc,
        ))
        changes.extend(self._diff_request_body(
            path, old_details.get("requestBody"), new_details.get("requestBody"),
            old_doc, new_doc,
        ))
        changes.extend(self._diff_responses(
            path, old_details.get("responses", {}), new_details.get("responses", {}),
            old_doc, new_doc,
        ))
        return changes

    def _diff_parameters(
        self, path: str, old_params: Any, new_params: Any,
        old_doc: Dict[str, Any], new_doc: Dict[str, Any],
    ) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        if not isinstance(old_params, Sequence) or isinstance(old_params, (str, bytes)):
            old_params = []
        if not isinstance(new_params, Sequence) or isinstance(new_params, (str, bytes)):
            new_params = []

        old_map = {p["name"]: p for p in old_params if isinstance(p, dict) and "name" in p}
        new_map = {p["name"]: p for p in new_params if isinstance(p, dict) and "name" in p}

        for name, old_p in old_map.items():
            if name not in new_map:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_FIELD,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{path} -> parameter:{name}",
                    description=f"Parameter '{name}' removed.",
                ))
                continue
            new_p = new_map[name]
            # An optional parameter becoming mandatory breaks every existing caller
            # that omits it, and is invisible to a pure schema comparison.
            if not old_p.get("required", False) and new_p.get("required", False):
                changes.append(SchemaChange(
                    change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                    severity=DiffSeverity.MEDIUM_BREAKING,
                    path=f"{path} -> parameter:{name}",
                    description=f"Parameter '{name}' changed from optional to REQUIRED.",
                ))
            changes.extend(self._diff_schema_types(
                f"{path} -> parameter:{name}",
                old_p.get("schema", old_p), new_p.get("schema", new_p),
                is_response=False, old_doc=old_doc, new_doc=new_doc,
            ))

        for name, new_p in new_map.items():
            if name in old_map:
                continue
            if new_p.get("required", False):
                changes.append(SchemaChange(
                    change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                    severity=DiffSeverity.MEDIUM_BREAKING,
                    path=f"{path} -> parameter:{name}",
                    description=f"New MANDATORY parameter '{name}' added.",
                ))
            else:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=f"{path} -> parameter:{name}",
                    description=f"New optional parameter '{name}' added.",
                ))
        return changes

    def _diff_request_body(
        self, path: str, old_body: Any, new_body: Any,
        old_doc: Dict[str, Any], new_doc: Dict[str, Any],
    ) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_body = old_body if isinstance(old_body, dict) else {}
        new_body = new_body if isinstance(new_body, dict) else {}
        old_content = old_body.get("content", {})
        new_content = new_body.get("content", {})
        old_content = old_content if isinstance(old_content, dict) else {}
        new_content = new_content if isinstance(new_content, dict) else {}

        if old_content and not new_content:
            changes.append(SchemaChange(
                change_type=ChangeType.REMOVED_REQUEST_BODY,
                severity=DiffSeverity.CRITICAL_BREAKING,
                path=f"{path} -> requestBody",
                description="Request body removed entirely; existing callers will send a "
                            "payload the endpoint no longer accepts.",
            ))
            return changes

        if not old_body.get("required", False) and new_body.get("required", False):
            changes.append(SchemaChange(
                change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                severity=DiffSeverity.MEDIUM_BREAKING,
                path=f"{path} -> requestBody",
                description="Request body changed from optional to REQUIRED.",
            ))

        for content_type, old_media in old_content.items():
            if content_type not in new_content:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_CONTENT_TYPE,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{path} -> requestBody:{content_type}",
                    description=f"Request content type '{content_type}' no longer accepted.",
                ))
                continue
            changes.extend(self._diff_schema_types(
                f"{path} -> requestBody:{content_type}",
                (old_media or {}).get("schema", {}),
                (new_content[content_type] or {}).get("schema", {}),
                is_response=False, old_doc=old_doc, new_doc=new_doc,
            ))
        return changes

    def _diff_responses(
        self, path: str, old_resps: Any, new_resps: Any,
        old_doc: Dict[str, Any], new_doc: Dict[str, Any],
    ) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_resps = old_resps if isinstance(old_resps, dict) else {}
        new_resps = new_resps if isinstance(new_resps, dict) else {}

        for status, old_resp in old_resps.items():
            if status not in new_resps:
                changes.append(SchemaChange(
                    change_type=ChangeType.REMOVED_RESPONSE_CODE,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{path} -> response:{status}",
                    description=f"Response status '{status}' removed; consumers branching on "
                                f"it will not see it again.",
                ))
                continue
            old_content = (old_resp or {}).get("content", {})
            new_content = (new_resps[status] or {}).get("content", {})
            old_content = old_content if isinstance(old_content, dict) else {}
            new_content = new_content if isinstance(new_content, dict) else {}
            for content_type, old_media in old_content.items():
                if content_type not in new_content:
                    changes.append(SchemaChange(
                        change_type=ChangeType.REMOVED_CONTENT_TYPE,
                        severity=DiffSeverity.HIGH_BREAKING,
                        path=f"{path} -> response:{status}:{content_type}",
                        description=f"Response content type '{content_type}' removed.",
                    ))
                    continue
                changes.extend(self._diff_schema_types(
                    f"{path} -> response:{status}:{content_type}",
                    (old_media or {}).get("schema", {}),
                    (new_content[content_type] or {}).get("schema", {}),
                    is_response=True, old_doc=old_doc, new_doc=new_doc,
                ))
        return changes

    # ------------------------------------------------------------------
    # Schema-level diffing
    # ------------------------------------------------------------------

    def _diff_schema_types(
        self, context_path: str, old_schema: Any, new_schema: Any, is_response: bool,
        old_doc: Dict[str, Any], new_doc: Dict[str, Any],
        _seen: Optional[Set[Tuple[int, int]]] = None,
    ) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_schema = old_schema if isinstance(old_schema, dict) else {}
        new_schema = new_schema if isinstance(new_schema, dict) else {}

        old_schema, old_unresolved = self._resolve_ref(old_schema, old_doc)
        new_schema, new_unresolved = self._resolve_ref(new_schema, new_doc)
        for ref in (old_unresolved, new_unresolved):
            if ref:
                changes.append(SchemaChange(
                    change_type=ChangeType.UNRESOLVED_REF,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=context_path,
                    description=f"Reference '{ref}' could not be resolved locally; this part "
                                f"of the schema was NOT compared.",
                ))
                logger.warning(
                    f"{context_path}: unresolved reference {ref!r}; region not compared."
                )

        # Recursive models (Order -> parent -> Order) would otherwise loop forever once
        # references resolve. Identity of the resolved pair is a sufficient cycle key.
        if _seen is None:
            _seen = set()
        key = (id(old_schema), id(new_schema))
        if key in _seen:
            return changes
        _seen = _seen | {key}

        old_types = _normalize_type(old_schema)
        new_types = _normalize_type(new_schema)
        if old_types and new_types and old_types != new_types:
            changes.append(SchemaChange(
                change_type=(ChangeType.RESPONSE_TYPE_MUTATION if is_response
                             else ChangeType.TYPE_MUTATION),
                severity=DiffSeverity.HIGH_BREAKING,
                path=context_path,
                description=f"Type mutated from {sorted(old_types)} to {sorted(new_types)}.",
            ))

        changes.extend(self._diff_enums(context_path, old_schema, new_schema, is_response))

        if "object" in old_types and "object" in new_types:
            changes.extend(self._diff_object(
                context_path, old_schema, new_schema, is_response, old_doc, new_doc, _seen
            ))
        elif "array" in old_types and "array" in new_types:
            changes.extend(self._diff_schema_types(
                f"{context_path}[items]",
                old_schema.get("items", {}), new_schema.get("items", {}),
                is_response, old_doc, new_doc, _seen,
            ))
        return changes

    @staticmethod
    def _diff_enums(
        context_path: str, old_schema: Dict[str, Any], new_schema: Dict[str, Any],
        is_response: bool,
    ) -> List[SchemaChange]:
        """
        Compare enum constraints, weighting each direction by who it breaks.

        A request enum constrains what the *client may send*: losing a value breaks callers
        that send it, gaining one does not. A response enum constrains what the *client
        must handle*: gaining a value breaks an exhaustive state machine, losing one does
        not. Treating both directions identically — as a bare set difference — misses half
        the breaking cases and raises false alarms on the other half.
        """
        changes: List[SchemaChange] = []
        old_enum = old_schema.get("enum")
        new_enum = new_schema.get("enum")
        old_set = set(old_enum) if isinstance(old_enum, (list, tuple)) else None
        new_set = set(new_enum) if isinstance(new_enum, (list, tuple)) else None

        if old_set is None and new_set is None:
            return changes

        if old_set is None:
            # A constraint appeared where there was none.
            if not is_response:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=context_path,
                    description=f"Field is now restricted to {sorted(map(str, new_set))}; "
                                f"previously unconstrained values will be rejected.",
                ))
            return changes

        if new_set is None:
            # A constraint disappeared: responses may now carry anything.
            if is_response:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=context_path,
                    description="Response enum constraint removed; the field is no longer a "
                                "closed set and consumers can no longer branch exhaustively.",
                ))
            return changes

        removed = old_set - new_set
        added = new_set - old_set

        if is_response:
            if added:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=context_path,
                    description=f"New response enum values {sorted(map(str, added))}; consumers "
                                f"handling the old set exhaustively will not recognise them.",
                ))
            if removed:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=context_path,
                    description=f"Response enum values no longer returned: "
                                f"{sorted(map(str, removed))}.",
                ))
        else:
            if removed:
                changes.append(SchemaChange(
                    change_type=ChangeType.ENUM_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=context_path,
                    description=f"Request enum values removed {sorted(map(str, removed))}; "
                                f"callers sending them will be rejected.",
                ))
            if added:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=context_path,
                    description=f"New request enum values accepted: {sorted(map(str, added))}.",
                ))
        return changes

    def _diff_object(
        self, context_path: str, old_schema: Dict[str, Any], new_schema: Dict[str, Any],
        is_response: bool, old_doc: Dict[str, Any], new_doc: Dict[str, Any],
        seen: Set[Tuple[int, int]],
    ) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        old_props = old_schema.get("properties", {})
        new_props = new_schema.get("properties", {})
        old_props = old_props if isinstance(old_props, dict) else {}
        new_props = new_props if isinstance(new_props, dict) else {}
        old_required = set(old_schema.get("required", []) or [])
        new_required = set(new_schema.get("required", []) or [])

        for prop, old_prop_schema in old_props.items():
            if prop not in new_props:
                changes.append(SchemaChange(
                    change_type=(ChangeType.REMOVED_RESPONSE_FIELD if is_response
                                 else ChangeType.REMOVED_FIELD),
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{context_path}.{prop}",
                    description=(f"Response field '{prop}' removed." if is_response
                                 else f"Request field '{prop}' removed."),
                ))
                continue

            # Requirement transitions on fields that exist in both versions.
            if is_response and prop in old_required and prop not in new_required:
                changes.append(SchemaChange(
                    change_type=ChangeType.REQUIREMENT_MUTATION,
                    severity=DiffSeverity.HIGH_BREAKING,
                    path=f"{context_path}.{prop}",
                    description=f"Response field '{prop}' is no longer guaranteed present; "
                                f"parsers that assume it will fail on responses that omit it.",
                ))
            elif not is_response and prop not in old_required and prop in new_required:
                changes.append(SchemaChange(
                    change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                    severity=DiffSeverity.MEDIUM_BREAKING,
                    path=f"{context_path}.{prop}",
                    description=f"Request field '{prop}' changed from optional to REQUIRED.",
                ))

            changes.extend(self._diff_schema_types(
                f"{context_path}.{prop}", old_prop_schema, new_props[prop],
                is_response, old_doc, new_doc, seen,
            ))

        for prop in new_props:
            if prop in old_props:
                continue
            if is_response:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=f"{context_path}.{prop}",
                    description=f"New response field '{prop}' added.",
                ))
            elif prop in new_required:
                changes.append(SchemaChange(
                    change_type=ChangeType.NEW_REQUIRED_PARAMETER,
                    severity=DiffSeverity.MEDIUM_BREAKING,
                    path=f"{context_path}.{prop}",
                    description=f"New MANDATORY field '{prop}' added.",
                ))
            else:
                changes.append(SchemaChange(
                    change_type=ChangeType.ADDED_OPTIONAL_FIELD,
                    severity=DiffSeverity.NON_BREAKING_INFO,
                    path=f"{context_path}.{prop}",
                    description=f"New optional field '{prop}' added.",
                ))
        return changes
