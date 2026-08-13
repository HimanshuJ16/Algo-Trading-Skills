"""
Pre-trade controls for algorithmic-order disclosure and exchange membership.

The engine deliberately validates the exchange-facing order metadata. It does
not decide whether a strategy is algorithmic under a particular jurisdiction;
that classification must be supplied by the upstream control framework.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from typing import Optional, Union

logger = logging.getLogger(__name__)

APPROVED_STATUS = "APPROVED"


@dataclasses.dataclass(frozen=True)
class AlgoRegistration:
    """A registry record for one disclosed algorithm identifier."""

    status: str
    venues: frozenset[str] = frozenset()
    version: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class OutboundOrder:
    order_id: str
    symbol: str
    is_algorithmic: bool
    algo_id: Optional[str] = None
    trader_id: Optional[str] = None
    venue: Optional[str] = None
    algo_version: Optional[str] = None
    parent_algo_id: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class OrderComplianceReport:
    order_id: str
    is_compliant: bool
    rejection_reason: Optional[str] = None
    reason_code: Optional[str] = None
    algo_id: Optional[str] = None
    registry_status: Optional[str] = None


RegistryValue = Union[str, AlgoRegistration]


class AlgoDisclosureComplianceEngine:
    """Fail-closed validation for exchange-facing algorithmic order metadata.

    A string registry value remains supported for compatibility, for example
    ``{"TWAP_V1.2": "APPROVED"}``. Use :class:`AlgoRegistration` when the
    approval is venue-scoped or tied to a specific registered version.
    """

    def __init__(self, approved_algo_registry: Mapping[str, RegistryValue]):
        if not isinstance(approved_algo_registry, Mapping):
            raise TypeError("approved_algo_registry must be a mapping")

        self.registry = {
            self._require_text(algo_id, "algo_id"): self._normalize_registration(
                algo_id, registration
            )
            for algo_id, registration in approved_algo_registry.items()
        }

    def evaluate_order(self, order: OutboundOrder) -> OrderComplianceReport:
        """Evaluate an order without mutating the order or registry."""

        if not isinstance(order, OutboundOrder):
            raise TypeError("order must be an OutboundOrder")

        invalid_field = self._first_invalid_field(order)
        if invalid_field is not None:
            return self._reject(
                order,
                "INVALID_ORDER_FIELD",
                f"Order field '{invalid_field}' is missing or invalid.",
            )

        algo_id = self._optional_text(order.algo_id)
        trader_id = self._optional_text(order.trader_id)
        venue = self._optional_text(order.venue)
        algo_version = self._optional_text(order.algo_version)
        parent_algo_id = self._optional_text(order.parent_algo_id)

        if not order.is_algorithmic:
            if algo_id is not None:
                return self._reject(
                    order,
                    "MANUAL_ORDER_HAS_ALGO_ID",
                    "Manual order must not carry an algorithm identifier.",
                    algo_id=algo_id,
                )
            if trader_id is None:
                return self._reject(
                    order,
                    "MANUAL_TRADER_ID_MISSING",
                    "Manual order is missing trader_id.",
                )
            return self._approve(order, algo_id=None)

        if algo_id is None:
            return self._reject(
                order,
                "ALGO_ID_MISSING",
                "Algorithmic order is missing the exchange-facing algo_id tag.",
            )

        if parent_algo_id is not None and parent_algo_id != algo_id:
            return self._reject(
                order,
                "PARENT_ALGO_ID_MISMATCH",
                "Child order algo_id does not match the parent algorithm identifier.",
                algo_id=algo_id,
            )

        registration = self.registry.get(algo_id)
        if registration is None:
            return self._reject(
                order,
                "ALGO_NOT_REGISTERED",
                f"Algo ID '{algo_id}' is not present in the compliance registry.",
                algo_id=algo_id,
            )

        if registration.status != APPROVED_STATUS:
            return self._reject(
                order,
                "ALGO_NOT_APPROVED",
                f"Regulatory breach prevented: Algo ID '{algo_id}' is not an approved and disclosed algorithm (status: {registration.status}).",
                algo_id=algo_id,
                registry_status=registration.status,
            )

        if registration.version is not None and algo_version != registration.version:
            return self._reject(
                order,
                "ALGO_VERSION_MISMATCH",
                f"Algo ID '{algo_id}' requires registered version '{registration.version}'.",
                algo_id=algo_id,
                registry_status=registration.status,
            )

        if registration.venues:
            if venue is None:
                return self._reject(
                    order,
                    "VENUE_MISSING",
                    f"Algo ID '{algo_id}' is approved only for specific venues; venue is required.",
                    algo_id=algo_id,
                    registry_status=registration.status,
                )
            if venue.upper() not in registration.venues:
                return self._reject(
                    order,
                    "VENUE_NOT_APPROVED",
                    f"Algo ID '{algo_id}' is not approved for venue '{venue}'.",
                    algo_id=algo_id,
                    registry_status=registration.status,
                )

        return self._approve(order, algo_id=algo_id, registry_status=registration.status)

    @staticmethod
    def _normalize_registration(
        algo_id: str, registration: RegistryValue
    ) -> AlgoRegistration:
        if isinstance(registration, str):
            registration = AlgoRegistration(status=registration)
        elif not isinstance(registration, AlgoRegistration):
            raise TypeError(
                f"Registry entry for '{algo_id}' must be a status string or AlgoRegistration"
            )

        status = AlgoDisclosureComplianceEngine._require_text(
            registration.status, f"status for '{algo_id}'"
        ).upper()
        normalized_venues = frozenset(
            AlgoDisclosureComplianceEngine._require_text(venue, "venue").upper()
            for venue in registration.venues
        )
        version = (
            AlgoDisclosureComplianceEngine._optional_text(registration.version)
            if registration.version is not None
            else None
        )
        return AlgoRegistration(status, normalized_venues, version)

    @staticmethod
    def _first_invalid_field(order: OutboundOrder) -> Optional[str]:
        if not isinstance(order.order_id, str) or not order.order_id.strip():
            return "order_id"
        if not isinstance(order.symbol, str) or not order.symbol.strip():
            return "symbol"
        if not isinstance(order.is_algorithmic, bool):
            return "is_algorithmic"
        for field_name in (
            "algo_id",
            "trader_id",
            "venue",
            "algo_version",
            "parent_algo_id",
        ):
            value = getattr(order, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return field_name
        return None

    @staticmethod
    def _require_text(value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_text(value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None

    @staticmethod
    def _approve(
        order: OutboundOrder,
        *,
        algo_id: Optional[str],
        registry_status: Optional[str] = None,
    ) -> OrderComplianceReport:
        logger.info(
            "order_compliance_approved",
            extra={
                "order_id": order.order_id,
                "algo_id": algo_id,
                "registry_status": registry_status,
            },
        )
        return OrderComplianceReport(
            order_id=order.order_id,
            is_compliant=True,
            algo_id=algo_id,
            registry_status=registry_status,
        )

    @staticmethod
    def _reject(
        order: OutboundOrder,
        reason_code: str,
        rejection_reason: str,
        *,
        algo_id: Optional[str] = None,
        registry_status: Optional[str] = None,
    ) -> OrderComplianceReport:
        logger.warning(
            "order_compliance_rejected",
            extra={
                "order_id": order.order_id,
                "reason_code": reason_code,
                "algo_id": algo_id,
                "registry_status": registry_status,
            },
        )
        return OrderComplianceReport(
            order_id=order.order_id,
            is_compliant=False,
            rejection_reason=rejection_reason,
            reason_code=reason_code,
            algo_id=algo_id,
            registry_status=registry_status,
        )
