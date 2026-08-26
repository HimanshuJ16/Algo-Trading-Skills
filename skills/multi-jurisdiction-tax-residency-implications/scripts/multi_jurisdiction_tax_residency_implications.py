"""
Multi-jurisdiction tax residency assessment engine for globally-distributed
trading operations.

Scope: this engine determines *which jurisdictions claim an entity or an
individual as a tax resident*, resolves dual residence through the tie-breaker
the applicable treaty actually contains, and flags permanent establishment
exposure. It stops there.

It deliberately performs no withholding tax or Foreign Tax Credit arithmetic.
Residence is the input to that calculation, not part of it; see the skill
`double-taxation-treaty-considerations-cross-border-trading`, which models
treaty rates, the noncompulsory-payment limit on creditability, and the
residence-country credit ceiling. Splitting the two keeps a single answer for
any given payment instead of two engines disagreeing about it.

Nothing here is a treaty or statute database. Residency rules are
jurisdiction-specific and mutually inconsistent -- India's basic individual
test is 182 days (Income-tax Act s.6(1)), the UK's automatic UK test is 183
days under the Statutory Residence Test, and the US Substantial Presence Test
is a weighted three-year formula (IRC s.7701(b)(3)) that no single-year day
count reproduces. An unregistered jurisdiction resolves to REVIEW_REQUIRED
rather than to a guessed threshold.

Output is decision support for a tax adviser, not a filing position.
"""
import logging
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Bases on which a jurisdiction may claim corporate residence -----------
# A country typically taxes a company as resident because it was incorporated
# there, because it is managed there, or both. The UK does both: UK-incorporated
# companies are resident by incorporation, and a foreign-incorporated company is
# UK resident if its central management and control sits in the UK. India does
# both under Income-tax Act s.6(3): incorporation, or place of effective
# management in India.
BASIS_INCORPORATION = "INCORPORATION"
BASIS_EFFECTIVE_MANAGEMENT = "EFFECTIVE_MANAGEMENT"

# --- Individual physical-presence test shapes ------------------------------
# SIMPLE_DAY_THRESHOLD: days in the tax year alone decide (UK automatic UK test
#   at 183 days; India's basic test at 182 days).
# WEIGHTED_LOOKBACK: the current year and a number of preceding years are
#   combined under fixed weights. The US Substantial Presence Test is this
#   shape: all days in the current year, one third of the first preceding year
#   and one sixth of the second, against a 183-day threshold, with a separate
#   floor of 31 days in the current year.
PRESENCE_TEST_SIMPLE = "SIMPLE_DAY_THRESHOLD"
PRESENCE_TEST_WEIGHTED_LOOKBACK = "WEIGHTED_LOOKBACK"
VALID_PRESENCE_TESTS = frozenset({PRESENCE_TEST_SIMPLE, PRESENCE_TEST_WEIGHTED_LOOKBACK})

# --- Treaty tie-breakers for persons other than individuals ----------------
# TIEBREAK_COMPETENT_AUTHORITY: the 2017 OECD Model Art. 4(3), and MLI Art. 4,
#   send dual-resident entities to the competent authorities, who "shall
#   endeavour to determine by mutual agreement" the single state of residence
#   having regard to place of effective management, place of incorporation and
#   any other relevant factors. Critically: "In the absence of such agreement,
#   such person shall not be entitled to any relief or exemption from tax
#   provided by this Convention except to the extent and in such manner as may
#   be agreed upon by the competent authorities."
# TIEBREAK_POEM: the pre-BEPS OECD Model and the UN Model break the tie
#   automatically in favour of the place of effective management. Many treaties
#   in force still read this way -- MLI Art. 4 is not a minimum standard and a
#   Party may reserve out of it entirely.
TIEBREAK_COMPETENT_AUTHORITY = "COMPETENT_AUTHORITY_MAP"
TIEBREAK_POEM = "PLACE_OF_EFFECTIVE_MANAGEMENT"
VALID_TIEBREAKS = frozenset({TIEBREAK_COMPETENT_AUTHORITY, TIEBREAK_POEM})

# --- Assessment outcomes ---------------------------------------------------
STATUS_SINGLE_RESIDENCE = "SINGLE_RESIDENCE"
STATUS_DUAL_RESIDENCE_RESOLVED = "DUAL_RESIDENCE_RESOLVED"
STATUS_DUAL_RESIDENCE_UNRESOLVED = "DUAL_RESIDENCE_UNRESOLVED"
STATUS_NO_RESIDENCE_CLAIMED = "NO_RESIDENCE_CLAIMED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

# The US Substantial Presence Test weights, exact rather than binary floats.
US_SPT_WEIGHTS: Tuple[Fraction, ...] = (Fraction(1), Fraction(1, 3), Fraction(1, 6))


# --- Registered jurisdiction rules ----------------------------------------


@dataclass
class CorporateResidenceRule:
    """
    How one jurisdiction decides that a company is resident there.

    Both flags may be true: a jurisdiction that taxes on incorporation usually
    also taxes a foreign-incorporated company managed from its territory.
    Both may be false, which is a meaningful registration -- it records a
    jurisdiction that asserts no corporate residence at all, rather than
    leaving it unregistered and indistinguishable from "not yet researched".
    """
    country: str
    taxes_on_incorporation: bool
    taxes_on_effective_management: bool
    source: str = ""                      # statute / guidance this was taken from
    notes: str = ""


@dataclass
class IndividualPresenceRule:
    """
    One jurisdiction's physical-presence test for individuals.

    `day_threshold` is compared against the weighted day total. `lookback_weights`
    is ordered from the current tax year backwards; `(1,)` is a single-year test.
    `min_days_current_year` adds an independent floor that must also be met.

    This models the *arithmetic* test only. Several jurisdictions layer further
    alternative tests on top -- India's 60-day-plus-365-day rule and its
    120-day rule for citizens and persons of Indian origin with Indian-sourced
    income above INR 1.5 million, and the UK's sufficient ties test, which can
    make an individual resident on well under 183 days. Failing the registered
    test is therefore not a clearance; see `ResidencyRuleOutcome.caveat`.
    """
    country: str
    day_threshold: int
    test_kind: str = PRESENCE_TEST_SIMPLE
    lookback_weights: Sequence[Fraction] = (Fraction(1),)
    min_days_current_year: Optional[int] = None
    source: str = ""
    has_additional_unmodelled_tests: bool = True


@dataclass
class TreatyResidenceTieBreaker:
    """
    The tie-breaker for persons other than individuals in one bilateral treaty.

    Register the rule the treaty in force actually contains, checked against any
    protocol and against both parties' MLI positions -- not the current OECD
    Model, which many treaties have not adopted.
    """
    country_a: str
    country_b: str
    method: str
    source: str = ""


# --- Subject profiles ------------------------------------------------------


@dataclass
class EntityProfile:
    """
    A trading entity whose residence is being assessed.

    `effective_management_country` is where the entity is in fact managed: where
    the board substantively decides, not the registered office. For a fund whose
    directors meet by video from three countries, this is a question of evidence,
    and the answer belongs in board minutes before it belongs here.

    `fixed_places_of_business` maps a country to a description of what the entity
    has there, and drives permanent establishment flagging.

    `competent_authority_determination` records the outcome of a mutual agreement
    procedure that has actually concluded. Leave it None until it has; an
    unresolved MAP is the risk this engine exists to surface.
    """
    entity_id: str
    incorporation_country: str
    effective_management_country: Optional[str] = None
    fixed_places_of_business: Dict[str, str] = field(default_factory=dict)
    competent_authority_determination: Optional[str] = None


@dataclass
class IndividualPresence:
    """
    Days a decision-maker was physically present per country, per tax year.

    Relevant to a trading operation because the individuals exercising judgement
    over the strategy are the ones whose location evidences where the entity is
    managed, and because they carry their own residence exposure.

    `days_by_country_year` is {country: {tax_year: days}}.
    """
    person_id: str
    days_by_country_year: Dict[str, Dict[int, int]]
    role: str = ""


# --- Findings --------------------------------------------------------------


@dataclass
class ResidencyClaim:
    """One jurisdiction's claim over the entity, and what founds it."""
    country: str
    bases: List[str]
    rule_source: str


@dataclass
class ResidencyRuleOutcome:
    """Result of applying one jurisdiction's presence test to one individual."""
    person_id: str
    country: str
    tax_year: int
    weighted_days: Optional[float]
    day_threshold: Optional[int]
    meets_registered_test: Optional[bool]
    status: str
    caveat: str


@dataclass
class PermanentEstablishmentFlag:
    """A place of business outside the resolved residence country."""
    country: str
    description: str
    assessment_required: str


@dataclass
class TaxResidencyReport:
    entity_id: str
    tax_year: int
    status: str
    residency_claims: List[ResidencyClaim]
    claiming_countries: List[str]
    resolved_residence_country: Optional[str]
    tie_breaker_method: Optional[str]
    treaty_benefits_at_risk: bool
    permanent_establishment_flags: List[PermanentEstablishmentFlag]
    individual_findings: List[ResidencyRuleOutcome]
    required_actions: List[str]
    audit_notes: str


class MultiJurisdictionTaxResidencyEngine:
    """
    Determines which jurisdictions claim residence over a trading entity and its
    decision-makers, resolves dual corporate residence through the registered
    treaty tie-breaker, and flags permanent establishment exposure.

    All jurisdiction rules must be registered by the caller. The engine has no
    built-in thresholds: a hard-coded 183 days is wrong for India (182), is not
    what the US Substantial Presence Test computes, and does not clear an
    individual under the UK sufficient ties test.
    """

    def __init__(self) -> None:
        self.corporate_rules: Dict[str, CorporateResidenceRule] = {}
        self.presence_rules: Dict[str, IndividualPresenceRule] = {}
        self.tie_breakers: Dict[FrozenSet[str], TreatyResidenceTieBreaker] = {}

    # -- normalisation -----------------------------------------------------

    @staticmethod
    def _norm_country(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError(f"{field_name} must be a non-empty string")
        return cleaned

    @staticmethod
    def _norm_days(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            # A float day count is a unit error, not a rounding question: days
            # present are whole days, and 182.5 means the caller has divided
            # something it should not have.
            raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
        if value < 0:
            raise ValueError(f"{field_name} must not be negative, got {value}")
        if value > 366:
            raise ValueError(
                f"{field_name} is {value}; no tax year has more than 366 days. "
                "Check for days double-counted across two countries."
            )
        return value

    @classmethod
    def _norm_country_keys(
        cls, mapping: Dict[str, object], label: str
    ) -> List[Tuple[str, str]]:
        """
        Normalises the country keys of a mapping, rejecting collisions.

        Two keys that normalise to the same country -- "US" and "us" -- would
        otherwise be processed as two separate countries. For day counts that
        silently halves the presence total: 100 days under each key reads as two
        non-resident findings of 100 days rather than one resident finding of
        200. Merging them would be just as wrong where the duplication is really
        double-counted travel, so the ambiguity is raised rather than resolved.

        Returns (normalised, original) pairs in normalised order.
        """
        seen: Dict[str, str] = {}
        for raw in mapping:
            country = cls._norm_country(raw, f"{label} key")
            if country in seen:
                raise ValueError(
                    f"{label} has two keys that both denote {country}: "
                    f"{seen[country]!r} and {raw!r}. Merge them into one entry -- "
                    "leaving both would split the value across two findings."
                )
            seen[country] = raw
        return sorted(seen.items())

    # -- registration ------------------------------------------------------

    def register_corporate_residence_rule(self, rule: CorporateResidenceRule) -> None:
        """Registers how one jurisdiction asserts corporate residence."""
        if not isinstance(rule, CorporateResidenceRule):
            raise TypeError(
                f"rule must be CorporateResidenceRule, got {type(rule).__name__}"
            )
        for flag_name in ("taxes_on_incorporation", "taxes_on_effective_management"):
            if not isinstance(getattr(rule, flag_name), bool):
                raise TypeError(f"rule.{flag_name} must be a bool")
        country = self._norm_country(rule.country, "rule.country")
        self.corporate_rules[country] = CorporateResidenceRule(
            country=country,
            taxes_on_incorporation=rule.taxes_on_incorporation,
            taxes_on_effective_management=rule.taxes_on_effective_management,
            source=rule.source,
            notes=rule.notes,
        )

    def register_individual_presence_rule(self, rule: IndividualPresenceRule) -> None:
        """
        Registers one jurisdiction's physical-presence test for individuals.

        Rejects a weighted test declared with a single weight, and a simple test
        declared with several: the mismatch means the caller has picked the wrong
        test_kind for the rule it is trying to express, and the resulting day
        total would be silently wrong rather than obviously wrong.
        """
        if not isinstance(rule, IndividualPresenceRule):
            raise TypeError(
                f"rule must be IndividualPresenceRule, got {type(rule).__name__}"
            )
        country = self._norm_country(rule.country, "rule.country")
        if rule.test_kind not in VALID_PRESENCE_TESTS:
            raise ValueError(
                f"rule.test_kind {rule.test_kind!r} is not one of "
                f"{sorted(VALID_PRESENCE_TESTS)}"
            )
        if isinstance(rule.day_threshold, bool) or not isinstance(rule.day_threshold, int):
            raise TypeError("rule.day_threshold must be an int")
        if rule.day_threshold <= 0:
            raise ValueError(
                f"rule.day_threshold must be positive, got {rule.day_threshold}"
            )
        weights = tuple(rule.lookback_weights)
        if not weights:
            raise ValueError(
                "rule.lookback_weights must contain at least the current year"
            )
        for w in weights:
            if isinstance(w, bool) or not isinstance(w, (int, Fraction)):
                raise TypeError(
                    "rule.lookback_weights must be ints or Fractions; a float weight "
                    "cannot represent 1/3 exactly and would shift the threshold."
                )
            if w < 0:
                raise ValueError("rule.lookback_weights must not be negative")
        if rule.test_kind == PRESENCE_TEST_SIMPLE and len(weights) != 1:
            raise ValueError(
                f"{country}: {PRESENCE_TEST_SIMPLE} takes exactly one weight, got "
                f"{len(weights)}; use {PRESENCE_TEST_WEIGHTED_LOOKBACK} for a "
                "multi-year test."
            )
        if rule.test_kind == PRESENCE_TEST_WEIGHTED_LOOKBACK and len(weights) < 2:
            raise ValueError(
                f"{country}: {PRESENCE_TEST_WEIGHTED_LOOKBACK} needs a weight for at "
                f"least one preceding year, got {len(weights)}"
            )
        if rule.min_days_current_year is not None:
            self._norm_days(rule.min_days_current_year, "rule.min_days_current_year")
        self.presence_rules[country] = IndividualPresenceRule(
            country=country,
            day_threshold=rule.day_threshold,
            test_kind=rule.test_kind,
            lookback_weights=tuple(Fraction(w) for w in weights),
            min_days_current_year=rule.min_days_current_year,
            source=rule.source,
            has_additional_unmodelled_tests=rule.has_additional_unmodelled_tests,
        )

    def register_treaty_tie_breaker(self, tie_breaker: TreatyResidenceTieBreaker) -> None:
        """Registers the entity tie-breaker contained in one bilateral treaty."""
        if not isinstance(tie_breaker, TreatyResidenceTieBreaker):
            raise TypeError(
                "tie_breaker must be TreatyResidenceTieBreaker, got "
                f"{type(tie_breaker).__name__}"
            )
        a = self._norm_country(tie_breaker.country_a, "tie_breaker.country_a")
        b = self._norm_country(tie_breaker.country_b, "tie_breaker.country_b")
        if a == b:
            raise ValueError(
                f"tie_breaker country_a and country_b are both {a!r}; a tie-breaker "
                "resolves a conflict between two different jurisdictions."
            )
        if tie_breaker.method not in VALID_TIEBREAKS:
            raise ValueError(
                f"tie_breaker.method {tie_breaker.method!r} is not one of "
                f"{sorted(VALID_TIEBREAKS)}"
            )
        self.tie_breakers[frozenset({a, b})] = TreatyResidenceTieBreaker(
            country_a=a, country_b=b, method=tie_breaker.method, source=tie_breaker.source
        )

    # -- individual physical presence --------------------------------------

    def assess_individual_presence(
        self, person: IndividualPresence, tax_year: int
    ) -> List[ResidencyRuleOutcome]:
        """
        Applies each registered presence rule to one individual for one tax year.

        Returns one outcome per country the person spent time in. A country with
        no registered rule yields REVIEW_REQUIRED rather than a threshold guess.

        A False result is never a clearance. Jurisdictions layer alternative
        tests on top of the day count -- the UK sufficient ties test can make an
        individual resident on fewer than 46 days, and India taxes a 60-day stay
        combined with 365 days across the four preceding years -- and those tests
        are not modelled here.
        """
        if not isinstance(person, IndividualPresence):
            raise TypeError(
                f"person must be IndividualPresence, got {type(person).__name__}"
            )
        if not isinstance(person.person_id, str) or not person.person_id.strip():
            raise ValueError("person.person_id must be a non-empty string")
        if not isinstance(person.days_by_country_year, dict):
            raise TypeError("person.days_by_country_year must be a dict")
        if isinstance(tax_year, bool) or not isinstance(tax_year, int):
            raise TypeError("tax_year must be an int")

        outcomes: List[ResidencyRuleOutcome] = []
        for country, raw_country in self._norm_country_keys(
            person.days_by_country_year, "days_by_country_year"
        ):
            by_year = person.days_by_country_year[raw_country]
            if not isinstance(by_year, dict):
                raise TypeError(
                    f"days_by_country_year[{raw_country!r}] must be a dict of "
                    "{tax_year: days}"
                )
            for y, d in by_year.items():
                if isinstance(y, bool) or not isinstance(y, int):
                    raise TypeError(
                        f"days_by_country_year[{raw_country!r}] key {y!r} must be an "
                        "int tax year"
                    )
                self._norm_days(d, f"days_by_country_year[{raw_country!r}][{y}]")

            rule = self.presence_rules.get(country)
            if rule is None:
                outcomes.append(ResidencyRuleOutcome(
                    person_id=person.person_id, country=country, tax_year=tax_year,
                    weighted_days=None, day_threshold=None, meets_registered_test=None,
                    status=STATUS_REVIEW_REQUIRED,
                    caveat=(
                        f"No presence rule registered for {country}. No threshold is "
                        "assumed: the basic test is 182 days in India, 183 in the UK, "
                        "and a weighted three-year formula in the US."
                    ),
                ))
                continue

            weighted = Fraction(0)
            for offset, weight in enumerate(rule.lookback_weights):
                weighted += weight * by_year.get(tax_year - offset, 0)

            meets = weighted >= rule.day_threshold
            if rule.min_days_current_year is not None:
                # The US 31-day floor is a separate cumulative condition, not an
                # alternative route to residence: failing it defeats the test even
                # when the weighted total clears 183.
                meets = meets and by_year.get(tax_year, 0) >= rule.min_days_current_year

            caveat = (
                f"Registered test only ({rule.source or 'source not recorded'})."
            )
            if not meets and rule.has_additional_unmodelled_tests:
                caveat += (
                    f" Not meeting it does NOT establish non-residence in {country}: "
                    "alternative tests not modelled here (income-conditioned day "
                    "thresholds, ties tests, prior-year aggregation) can still apply."
                )
            outcomes.append(ResidencyRuleOutcome(
                person_id=person.person_id, country=country, tax_year=tax_year,
                weighted_days=float(weighted), day_threshold=rule.day_threshold,
                meets_registered_test=meets,
                status=STATUS_SINGLE_RESIDENCE if meets else STATUS_NO_RESIDENCE_CLAIMED,
                caveat=caveat,
            ))
        return outcomes

    # -- corporate residence and tie-breaking ------------------------------

    def _collect_claims(
        self, profile: EntityProfile, incorporation: str, poem: Optional[str]
    ) -> Tuple[List[ResidencyClaim], List[str]]:
        """
        Returns the jurisdictions asserting residence, and any actions needed
        because a jurisdiction in play has no registered rule.
        """
        claims: Dict[str, ResidencyClaim] = {}
        actions: List[str] = []

        def claim(country: str, basis: str) -> None:
            rule = self.corporate_rules.get(country)
            if rule is None:
                actions.append(
                    f"Register the corporate residence rule for {country}; it is the "
                    f"{basis.lower().replace('_', ' ')} country but its residence "
                    "basis is unknown, so no claim can be evaluated."
                )
                return
            asserts = (
                rule.taxes_on_incorporation if basis == BASIS_INCORPORATION
                else rule.taxes_on_effective_management
            )
            if not asserts:
                return
            existing = claims.get(country)
            if existing is None:
                claims[country] = ResidencyClaim(
                    country=country, bases=[basis], rule_source=rule.source
                )
            elif basis not in existing.bases:
                existing.bases.append(basis)

        claim(incorporation, BASIS_INCORPORATION)
        if poem is not None:
            claim(poem, BASIS_EFFECTIVE_MANAGEMENT)
        return [claims[c] for c in sorted(claims)], actions

    def _resolve_tie(
        self, profile: EntityProfile, countries: List[str], poem: Optional[str]
    ) -> Tuple[Optional[str], Optional[str], str, List[str], str]:
        """
        Applies the registered treaty tie-breaker to the two claiming countries.

        Only two jurisdictions can claim under the bases modelled here
        (incorporation and effective management), which is what makes a single
        bilateral tie-breaker sufficient. A third jurisdiction asserting
        residence on some other domestic basis is outside this engine's scope
        and has to be resolved pairwise with counsel.

        Returns (resolved_country, method, status, actions, note).
        """
        actions: List[str] = []
        pair = frozenset(countries)
        tb = self.tie_breakers.get(pair)
        if tb is None:
            return (
                None, None, STATUS_REVIEW_REQUIRED,
                [
                    f"Register the entity tie-breaker in the {countries[0]}-"
                    f"{countries[1]} treaty, checked against any protocol and both "
                    "parties' MLI positions."
                ],
                f"Dual residence in {countries[0]} and {countries[1]} with no "
                "registered tie-breaker. Nothing is assumed: pre-BEPS OECD and UN "
                "Model treaties break the tie on place of effective management, "
                "while the 2017 OECD Model and MLI Art. 4 refer it to the competent "
                "authorities.",
            )

        determination = profile.competent_authority_determination
        if determination is not None:
            determination = self._norm_country(
                determination, "profile.competent_authority_determination"
            )

        if tb.method == TIEBREAK_POEM:
            if poem is None:
                return (
                    None, tb.method, STATUS_DUAL_RESIDENCE_UNRESOLVED,
                    [
                        "Determine and evidence the place of effective management: "
                        "the applicable tie-breaker turns on it and it is not set."
                    ],
                    f"{countries[0]}-{countries[1]} treaty breaks the tie on place of "
                    "effective management, which has not been established.",
                )
            if poem not in pair:
                return (
                    None, tb.method, STATUS_DUAL_RESIDENCE_UNRESOLVED,
                    [
                        f"Reconcile the place of effective management ({poem}) with "
                        f"the claiming jurisdictions ({', '.join(countries)}) before "
                        "relying on the tie-breaker."
                    ],
                    f"Place of effective management is {poem}, which is neither "
                    f"{countries[0]} nor {countries[1]}.",
                )
            return (
                poem, tb.method, STATUS_DUAL_RESIDENCE_RESOLVED, actions,
                f"{countries[0]}-{countries[1]} treaty breaks the tie on place of "
                f"effective management, resolving residence to {poem} "
                f"({tb.source or 'source not recorded'}).",
            )

        # TIEBREAK_COMPETENT_AUTHORITY
        if determination is None:
            return (
                None, tb.method, STATUS_DUAL_RESIDENCE_UNRESOLVED,
                [
                    f"Open a mutual agreement procedure between {countries[0]} and "
                    f"{countries[1]}. Until it concludes, treat the entity as "
                    "entitled to NO relief or exemption under that treaty, and do "
                    "not book treaty withholding rates against it."
                ],
                f"{countries[0]}-{countries[1]} treaty refers dual-resident entities "
                "to the competent authorities, who determine residence by mutual "
                "agreement having regard to place of effective management, place of "
                "incorporation and other relevant factors. No determination is on "
                "file. Absent agreement the entity is not entitled to any relief or "
                "exemption under the treaty.",
            )
        if determination not in pair:
            raise ValueError(
                f"competent_authority_determination {determination!r} is not one of "
                f"the claiming jurisdictions {sorted(pair)}; a mutual agreement "
                "allocates residence to one of the two contracting states."
            )
        return (
            determination, tb.method, STATUS_DUAL_RESIDENCE_RESOLVED, actions,
            f"Competent authorities determined residence to be {determination} under "
            f"the {countries[0]}-{countries[1]} treaty.",
        )

    def _flag_permanent_establishments(
        self, profile: EntityProfile, residence: Optional[str]
    ) -> List[PermanentEstablishmentFlag]:
        """
        Flags fixed places of business outside the resolved residence country.

        Absence of staff is not a defence. The OECD concluded that human
        intervention is not a requirement for a permanent establishment, and that
        computer equipment at the enterprise's own disposal can be one where the
        functions performed through it exceed the preparatory or auxiliary
        threshold. For an algorithmic trading operation, a co-located server that
        the entity owns or leases and through which it executes its own strategy
        is precisely the fact pattern that has to be analysed rather than assumed
        away.
        """
        flags: List[PermanentEstablishmentFlag] = []
        for country, raw_country in self._norm_country_keys(
            profile.fixed_places_of_business, "fixed_places_of_business"
        ):
            description = profile.fixed_places_of_business[raw_country]
            if not isinstance(description, str) or not description.strip():
                raise ValueError(
                    f"fixed_places_of_business[{raw_country!r}] must be a non-empty "
                    "description of what the entity has there"
                )
            if residence is not None and country == residence:
                continue
            flags.append(PermanentEstablishmentFlag(
                country=country,
                description=description,
                assessment_required=(
                    f"Assess whether this constitutes a permanent establishment in "
                    f"{country} under Art. 5: is the place at the entity's own "
                    "disposal, is it fixed, and do the functions performed through it "
                    "exceed the preparatory or auxiliary threshold? Unattended "
                    "equipment is not excluded -- human intervention is not required "
                    "for a permanent establishment to exist."
                ),
            ))
        return flags

    def assess_entity(
        self,
        profile: EntityProfile,
        tax_year: int,
        individuals: Sequence[IndividualPresence] = (),
    ) -> TaxResidencyReport:
        """
        Assesses one entity's residence position for one tax year.

        Determines which registered jurisdictions claim the entity, resolves any
        dual residence through the registered treaty tie-breaker, flags permanent
        establishment exposure, and runs each supplied decision-maker through the
        registered individual presence tests.

        Raises on malformed input. An unregistered jurisdiction or tie-breaker
        produces REVIEW_REQUIRED with a required action, never a default answer.
        """
        if not isinstance(profile, EntityProfile):
            raise TypeError(f"profile must be EntityProfile, got {type(profile).__name__}")
        if not isinstance(profile.entity_id, str) or not profile.entity_id.strip():
            raise ValueError("profile.entity_id must be a non-empty string")
        if isinstance(tax_year, bool) or not isinstance(tax_year, int):
            raise TypeError("tax_year must be an int")
        if not isinstance(profile.fixed_places_of_business, dict):
            raise TypeError("profile.fixed_places_of_business must be a dict")

        incorporation = self._norm_country(
            profile.incorporation_country, "profile.incorporation_country"
        )
        poem = (
            self._norm_country(
                profile.effective_management_country,
                "profile.effective_management_country",
            )
            if profile.effective_management_country is not None
            else None
        )

        claims, actions = self._collect_claims(profile, incorporation, poem)
        claiming = [c.country for c in claims]

        tie_method: Optional[str] = None
        if not claiming:
            resolved = None
            status = (
                STATUS_REVIEW_REQUIRED if actions else STATUS_NO_RESIDENCE_CLAIMED
            )
            note = (
                "No registered jurisdiction asserts corporate residence over this "
                "entity. Verify this is genuinely the position rather than a gap in "
                "registered rules, and check any economic substance regime in the "
                "incorporation jurisdiction, which may require the entity to "
                "evidence tax residence elsewhere."
            )
        elif len(claiming) == 1:
            resolved = claiming[0]
            status = STATUS_SINGLE_RESIDENCE
            note = (
                f"{resolved} alone asserts residence, on the basis of "
                f"{', '.join(b.lower().replace('_', ' ') for b in claims[0].bases)}."
            )
        else:
            resolved, tie_method, status, tie_actions, note = self._resolve_tie(
                profile, claiming, poem
            )
            actions.extend(tie_actions)

        treaty_benefits_at_risk = status in (
            STATUS_DUAL_RESIDENCE_UNRESOLVED, STATUS_REVIEW_REQUIRED
        ) and len(claiming) > 1

        pe_flags = self._flag_permanent_establishments(profile, resolved)
        if pe_flags:
            actions.append(
                f"Assess permanent establishment exposure in "
                f"{', '.join(f.country for f in pe_flags)}."
            )

        individual_findings: List[ResidencyRuleOutcome] = []
        for person in individuals:
            individual_findings.extend(
                self.assess_individual_presence(person, tax_year)
            )

        # An unregistered jurisdiction where a decision-maker spent time is the
        # POEM risk this engine exists to surface, so it belongs in the action
        # list and not only in the per-person findings.
        unassessed = sorted({
            f.country for f in individual_findings
            if f.status == STATUS_REVIEW_REQUIRED
        })
        if unassessed:
            actions.append(
                "Register presence rules for "
                f"{', '.join(unassessed)}: decision-makers spent time there and "
                "their residency position is unassessed."
            )

        resident_elsewhere = sorted({
            f.country for f in individual_findings
            if f.meets_registered_test and f.country not in claiming
        })
        if resident_elsewhere:
            actions.append(
                "Review whether decision-makers resident in "
                f"{', '.join(resident_elsewhere)} shift the place of effective "
                "management or create a dependent agent permanent establishment "
                "there."
            )

        if treaty_benefits_at_risk:
            logger.warning(
                "UNRESOLVED DUAL RESIDENCE [%s]: %s claim residence for %d; treaty "
                "relief should not be assumed.",
                profile.entity_id, ", ".join(claiming), tax_year,
            )
        else:
            logger.info(
                "RESIDENCE ASSESSED [%s]: status=%s resolved=%s for %d.",
                profile.entity_id, status, resolved, tax_year,
            )

        return TaxResidencyReport(
            entity_id=profile.entity_id,
            tax_year=tax_year,
            status=status,
            residency_claims=claims,
            claiming_countries=claiming,
            resolved_residence_country=resolved,
            tie_breaker_method=tie_method,
            treaty_benefits_at_risk=treaty_benefits_at_risk,
            permanent_establishment_flags=pe_flags,
            individual_findings=individual_findings,
            required_actions=actions,
            audit_notes=note,
        )
