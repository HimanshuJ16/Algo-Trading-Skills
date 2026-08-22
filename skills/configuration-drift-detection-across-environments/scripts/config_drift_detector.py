"""Configuration drift detection between a Golden Source baseline and a target
trading environment.

The detector is a *pre-trade gate*: it is intended to run during process
initialization or in a CI/CD deployment step, and to block startup when a
CRITICAL discrepancy is found. Because a false PASS is the expensive failure
mode here, ambiguous input is rejected loudly rather than audited partially.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Leaf key names that must never be treated as an allowed environment override.
#: These are risk-control parameters: if they differ between the Golden Source
#: and the target, that difference is drift by definition, whatever the
#: whitelist says. This list is a documented starting point keyed on *leaf
#: name*, not an exhaustive inventory of every risk parameter that exists --
#: extend it via the ``protected_keys`` constructor argument to match your own
#: configuration schema.
DEFAULT_PROTECTED_KEYS: frozenset = frozenset({
    "kill_switch_enabled",
    "max_drawdown_stop_pct",
    "max_order_qty",
    "max_order_usd",
    "max_position_size",
    "position_limit",
    "stop_loss_pct",
})


@dataclass
class DriftItem:
    key_path: str
    baseline_value: Any
    target_value: Any
    severity: str                     # 'CRITICAL', 'WARNING', 'ALLOWED'
    description: str


@dataclass
class ConfigAuditReport:
    is_compliant: bool
    total_keys_audited: int
    critical_drift_count: int
    warning_drift_count: int
    allowed_override_count: int
    drift_items: List[DriftItem] = field(default_factory=list)


class ConfigurationDriftDetector:
    """
    Compares target environment configuration against a Golden Source baseline,
    categorizing discrepancies by severity (CRITICAL, WARNING, ALLOWED).

    Args:
        allowed_overrides: Keys permitted to differ between environments. A key
            matches either as a full dot-separated path (``"system.api_url"``,
            the safer and more precise form) or as a bare leaf name
            (``"api_url"``), in which case it whitelists that leaf name
            *anywhere* in the configuration tree. Pass an empty set for
            zero-tolerance auditing; pass ``None`` (the default) to use the
            built-in convenience whitelist of connectivity/logging keys.
        protected_keys: Risk-control key names that may never be downgraded to
            an ALLOWED override, even if they also appear in
            ``allowed_overrides``. Defaults to :data:`DEFAULT_PROTECTED_KEYS`.
            Pass an empty set only if you are deliberately auditing a config
            tree that contains no risk parameters.

    Raises:
        ValueError: if ``allowed_overrides`` tries to whitelist a protected
            risk-control key. This is rejected at construction time rather than
            silently ignored at audit time, so the misconfiguration surfaces
            where it was written.
    """

    #: Convenience whitelist used when ``allowed_overrides`` is not supplied.
    #: Deliberately limited to connectivity, naming and logging keys.
    DEFAULT_ALLOWED_OVERRIDES: frozenset = frozenset({
        "env_name", "environment", "api_url", "broker_endpoint",
        "log_level", "port", "host", "db_name",
    })

    def __init__(
        self,
        allowed_overrides: Optional[Set[str]] = None,
        protected_keys: Optional[Set[str]] = None,
    ) -> None:
        # NOTE: an explicit empty set means "zero tolerance" and must be
        # honoured. Testing truthiness here would silently substitute the
        # permissive default whitelist for the strictest possible request.
        using_default_whitelist = allowed_overrides is None
        if using_default_whitelist:
            self.allowed_overrides: Set[str] = set(self.DEFAULT_ALLOWED_OVERRIDES)
        else:
            self.allowed_overrides = set(allowed_overrides)

        if protected_keys is None:
            self.protected_keys: Set[str] = set(DEFAULT_PROTECTED_KEYS)
        else:
            self.protected_keys = set(protected_keys)

        conflicts = sorted(
            k for k in self.allowed_overrides
            if k in self.protected_keys or k.split(".")[-1] in self.protected_keys
        )
        if conflicts:
            source = (
                "the built-in DEFAULT_ALLOWED_OVERRIDES whitelist"
                if using_default_whitelist else "allowed_overrides"
            )
            raise ValueError(
                "Risk-control parameters cannot be whitelisted as environment "
                f"overrides: {conflicts} (present in {source}). Remove them from "
                "allowed_overrides, or narrow protected_keys if they are genuinely "
                "not risk parameters in your schema."
            )

    def _is_protected(self, key_path: str) -> bool:
        return (
            key_path in self.protected_keys
            or key_path.split(".")[-1] in self.protected_keys
        )

    def _is_whitelisted(self, key_path: str) -> bool:
        return (
            key_path in self.allowed_overrides
            or key_path.split(".")[-1] in self.allowed_overrides
        )

    def _flatten_dict(
        self, d: Dict[str, Any], parent_key: str = "", sep: str = "."
    ) -> Dict[str, Any]:
        """
        Recursively flattens a nested dictionary into dot-separated keys.

        An empty nested dict is emitted as a leaf so that a whole subtree
        present in one config and absent from the other is still audited rather
        than vanishing during flattening.

        Raises:
            ValueError: if two distinct source keys flatten to the same path
                (for example a literal ``"a.b"`` key alongside a nested
                ``{"a": {"b": ...}}``). Silently collapsing them would drop one
                branch from the audit and could turn real drift into a PASS.
        """
        items: List[Tuple[str, Any]] = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
            if isinstance(v, dict) and v:
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))

        flattened: Dict[str, Any] = {}
        for key, value in items:
            if key in flattened:
                raise ValueError(
                    f"Ambiguous configuration key path '{key}': two distinct keys "
                    f"flatten to the same path using separator '{sep}'. Resolve the "
                    "collision before auditing -- it would otherwise hide drift."
                )
            flattened[key] = value
        return flattened

    def audit(
        self, golden_baseline: Dict[str, Any], target_config: Dict[str, Any]
    ) -> ConfigAuditReport:
        """
        Audits ``target_config`` against ``golden_baseline``.

        Severity assignment:
            * CRITICAL -- a baseline key is missing from the target, or a value
              (or its type) differs and the key is not a permitted override.
            * ALLOWED  -- a value differs on a whitelisted, non-protected key.
            * WARNING  -- a key exists in the target but not in the baseline.

        Only CRITICAL items affect ``is_compliant``. Extra keys are reported as
        WARNING and never block on their own, because the baseline cannot know
        what a legitimately-added key means; review WARNING items rather than
        assuming a compliant report means the target is identical.

        Values are compared with ``!=`` plus an exact type check, so ``True``
        and ``1``, or ``100`` and ``100.0``, are treated as drift. Lists are
        compared whole and order-sensitively.

        Raises:
            TypeError: if either argument is not a dict.
            ValueError: if either config contains an ambiguous key path.
        """
        if not isinstance(golden_baseline, dict):
            raise TypeError(
                f"golden_baseline must be a dict, got {type(golden_baseline).__name__}."
            )
        if not isinstance(target_config, dict):
            raise TypeError(
                f"target_config must be a dict, got {type(target_config).__name__}."
            )

        flat_baseline = self._flatten_dict(golden_baseline)
        flat_target = self._flatten_dict(target_config)

        all_keys = set(flat_baseline.keys()).union(set(flat_target.keys()))
        drift_items: List[DriftItem] = []

        critical_count = 0
        warning_count = 0
        allowed_count = 0

        for key in sorted(all_keys):
            if key not in flat_target:
                # Key present in baseline but missing in target -> CRITICAL.
                # This holds even for whitelisted keys: an override may change a
                # value, not remove the setting the engine expects to read.
                drift_items.append(DriftItem(
                    key_path=key,
                    baseline_value=flat_baseline[key],
                    target_value=None,
                    severity="CRITICAL",
                    description=f"Missing key '{key}' in target environment."
                ))
                critical_count += 1

            elif key not in flat_baseline:
                # Extra key present in target -> WARNING
                drift_items.append(DriftItem(
                    key_path=key,
                    baseline_value=None,
                    target_value=flat_target[key],
                    severity="WARNING",
                    description=f"Extra key '{key}' present in target environment."
                ))
                warning_count += 1

            else:
                base_val = flat_baseline[key]
                targ_val = flat_target[key]

                if base_val != targ_val or type(base_val) is not type(targ_val):
                    mismatch = (
                        f"Golden={base_val!r} ({type(base_val).__name__}) vs "
                        f"Target={targ_val!r} ({type(targ_val).__name__})"
                    )
                    if self._is_protected(key):
                        severity = "CRITICAL"
                        critical_count += 1
                        desc = (
                            f"Protected risk-control parameter '{key}' differs: "
                            f"{mismatch}. Risk parameters are never treated as "
                            "environment overrides."
                        )
                    elif self._is_whitelisted(key):
                        severity = "ALLOWED"
                        allowed_count += 1
                        desc = f"Whitelisted environment override for '{key}'."
                    else:
                        severity = "CRITICAL"
                        critical_count += 1
                        desc = f"Value mismatch for '{key}': {mismatch}."

                    drift_items.append(DriftItem(
                        key_path=key,
                        baseline_value=base_val,
                        target_value=targ_val,
                        severity=severity,
                        description=desc
                    ))

        is_compliant = (critical_count == 0)

        if not is_compliant:
            logger.error(
                "Configuration Audit FAILED: %d CRITICAL drift items detected.",
                critical_count,
            )
            for item in drift_items:
                if item.severity == "CRITICAL":
                    logger.error("CRITICAL drift: %s", item.description)
        else:
            logger.info(
                "Configuration Audit PASSED (%d warnings, %d allowed overrides).",
                warning_count, allowed_count,
            )

        return ConfigAuditReport(
            is_compliant=is_compliant,
            total_keys_audited=len(all_keys),
            critical_drift_count=critical_count,
            warning_drift_count=warning_count,
            allowed_override_count=allowed_count,
            drift_items=drift_items
        )
