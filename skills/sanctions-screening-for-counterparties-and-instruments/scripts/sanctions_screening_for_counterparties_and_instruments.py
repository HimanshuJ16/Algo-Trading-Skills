"""
sanctions-screening-for-counterparties-and-instruments: pre-trade and onboarding
sanctions screening for trading counterparties and financial instrument issuers.

What this module is and is not
------------------------------
It is a **fail-closed screening gate** that compares a counterparty or instrument
issuer against a supplied sanctions list snapshot and returns an auditable,
reproducible decision with a full finding trail.

It is **not** a sanctions list feed, and it is **not** a commercial matching
engine. It ships no live data: the caller supplies the list snapshot, dated, and
the engine refuses to screen against a list it has no ``as_of`` date for. The
five entries in ``DEMO_SANCTIONED_DATABASE`` exist to make the tests and the
doctests runnable — they are **not** a sanctions list and must never be used as
one. ``build_engine`` will not silently fall back to them.

The matching here is normalisation + edit distance + sorted-token distance. That
catches punctuation, case, accent and word-order variants. It does **not** do
phonetic matching (Soundex/Metaphone/NYSIIS), script transliteration
(Cyrillic/Arabic/Han to Latin), or nickname expansion. Names arriving in a
non-Latin script will not match a Latin list entry here. If your risk assessment
requires those, you need a screening vendor; this engine is a control you can
reason about and test, not a replacement for one.

Why fail-closed, specifically
-----------------------------
Every "clear" this engine cannot justify is a false negative that looks exactly
like a clean screen in the audit file. The failure that actually happens in
production is not a clever evasion, it is a namespace mismatch or a missing
field: an upstream system emitting ``"IRAN"`` against a list keyed on ``"IR"``
clears Iran on every screen and reports itself healthy doing it. So every
unresolvable country, blank name, NaN ownership percentage and undated list
raises ``SanctionsScreeningError`` rather than returning a clean report.

Jurisdiction
------------
Thresholds and programmes here are **US (OFAC)** unless stated, with EU, UN and
UK list *types* supported as inputs. The OFAC 50 Percent Rule threshold is fixed
by OFAC guidance; the fuzzy match threshold is an **engineering default with no
regulatory basis** — no regulator or standards body prescribes a number. See
``references/standards.md`` for the citation behind every claim in this module.

Nothing here is legal advice. A screening hit is an input to a compliance
officer's determination, not the determination.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Outcome codes
# ---------------------------------------------------------------------------
STATUS_CLEARED = "CLEARED"
STATUS_BLOCKED_OFAC_50 = "BLOCKED_OFAC_50_PERCENT_RULE"
STATUS_BLOCKED_SANCTIONS_HIT = "BLOCKED_SANCTIONS_HIT"
STATUS_BLOCKED_EMBARGO = "BLOCKED_EMBARGO"
STATUS_RESTRICTED_SECTORAL = "RESTRICTED_SECTORAL"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

#: Deterministic precedence. The first status present decides ``report.status``;
#: every finding is still carried in ``report.hits``, so a block never conceals a
#: second problem from the reviewer. ``BLOCKED_OFAC_50_PERCENT_RULE`` outranks a
#: list hit because its legal consequence is different and stricter: the entity
#: *is* blocked property to be blocked and reported, not merely declined.
_STATUS_PRECEDENCE: Tuple[str, ...] = (
    STATUS_BLOCKED_OFAC_50,
    STATUS_BLOCKED_SANCTIONS_HIT,
    STATUS_BLOCKED_EMBARGO,
    STATUS_RESTRICTED_SECTORAL,
    STATUS_REVIEW_REQUIRED,
)

_BLOCKING_STATUSES: FrozenSet[str] = frozenset({
    STATUS_BLOCKED_OFAC_50,
    STATUS_BLOCKED_SANCTIONS_HIT,
    STATUS_BLOCKED_EMBARGO,
})


class SanctionsScreeningError(ValueError):
    """Raised when a screening payload, list snapshot, or engine config is unusable.

    A sanctions screen must fail loudly rather than quietly. A blank country, a
    NaN ownership percentage, an undated list snapshot or an empty database is a
    data or wiring error, and screening anyway emits an authoritative-looking
    ``CLEARED`` for a screen that never actually ran.
    """


class SanctionsListType(str, Enum):
    """Source list a designation came from.

    ``UK_HMT`` is retained as a stable identifier for UK designations. Note that
    the OFSI Consolidated List of Asset Freeze Targets **closed on 28 January
    2026**; the UK Sanctions List maintained by the FCDO is now the single source
    for UK designations. The member name is historical, the meaning is "UK".
    """

    OFAC_SDN = "OFAC_SDN"
    OFAC_SSI = "OFAC_SSI"
    EU_CONSOLIDATED = "EU_CONSOLIDATED"
    UN_SANCTIONS = "UN_SANCTIONS"
    UK_HMT = "UK_HMT"


class SanctionsProgram(str, Enum):
    """What a designation actually prohibits.

    The distinction is not cosmetic. A **blocking** designation (OFAC SDN and
    equivalents) freezes property and bars dealings outright. A **sectoral**
    designation (OFAC's Sectoral Sanctions Identifications, introduced with the
    2014 Ukraine/Russia programmes) prohibits only *certain types of
    transactions* — typically defined tenors of new debt and equity — with an
    otherwise tradable entity.

    Treating a sectoral designation as blocking over-blocks lawful business;
    treating a blocking designation as sectoral is a violation. The engine keeps
    them in separate statuses and never collapses one into the other.
    """

    BLOCKING = "BLOCKING"
    SECTORAL = "SECTORAL"


class ScreeningEntityKind(str, Enum):
    COUNTERPARTY = "COUNTERPARTY"
    INSTRUMENT_ISSUER = "INSTRUMENT_ISSUER"


class MatchMethod(str, Enum):
    """How a hit was produced — recorded so an analyst can triage the alert."""

    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"
    EXACT_NAME = "EXACT_NAME"
    FUZZY_EDIT_DISTANCE = "FUZZY_EDIT_DISTANCE"
    FUZZY_TOKEN_ORDER = "FUZZY_TOKEN_ORDER"
    OWNERSHIP_50_PERCENT = "OWNERSHIP_50_PERCENT"
    COUNTRY_EMBARGO = "COUNTRY_EMBARGO"
    TERRITORY_EMBARGO = "TERRITORY_EMBARGO"


# ---------------------------------------------------------------------------
# OFAC 50 Percent Rule
#
# OFAC Revised Guidance of 13 August 2014: an entity owned "in the aggregate,
# directly or indirectly, 50 percent or more by one or more blocked persons" is
# itself considered blocked, whether or not it appears on the SDN List. The 2014
# revision is precisely the *aggregation*: the earlier position looked at each
# blocked person separately, so two blocked persons at 25% each did not trigger.
# This threshold is fixed by OFAC guidance and is not a policy dial.
# ---------------------------------------------------------------------------
OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT = 50.0

#: Engineering default only. **No regulator or standards body prescribes a fuzzy
#: match threshold.** The Wolfsberg Group's 2019 sanctions screening guidance
#: treats "how exact or 'fuzzy' to set the screening filter" as a risk-based
#: decision that must be "documented and supported by analysis and testing", and
#: OFAC's 2019 Framework asks that technology solutions "be calibrated to the
#: organization's risk profile". Calibrate this against your own list and book,
#: and record the calibration.
DEFAULT_FUZZY_MATCH_THRESHOLD_PCT = 85.0

#: Engineering default only. The OFAC SDN List has **no predetermined update
#: timetable** — names are added and removed as necessary — so any snapshot age
#: is a risk decision, not a rule. OFAC's 2019 Framework names failure "to update
#: their screening software to incorporate updates to the SDN List or the
#: Sectoral Sanctions Identifications List" as a root cause of actual violations,
#: which is why staleness is surfaced rather than ignored.
DEFAULT_MAX_LIST_AGE_DAYS = 7

_PCT_TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# Comprehensive country embargoes
#
# These are the OFAC programmes that embargo an entire jurisdiction rather than
# designating named persons. As of this snapshot date the set is Cuba, Iran and
# North Korea.
#
# **Syria was removed.** Executive Order 14312 of 30 June 2025 ("Providing for
# the Revocation of Syria Sanctions") revoked the six executive orders underlying
# the Syrian Sanctions Program and terminated the underlying national emergency
# effective 1 July 2025, and OFAC removed the Syrian Sanctions Regulations
# (31 CFR part 542) from the CFR on 26 August 2025. Targeted Syria-related
# designations remain in force (Assad, captagon networks, human-rights and
# chemical-weapons actors) and are caught by list screening, not by a country
# embargo. A hard-coded "SY" here would over-block every Syrian counterparty on
# the authority of a programme that no longer exists.
# ---------------------------------------------------------------------------
DEFAULT_EMBARGOED_COUNTRIES: FrozenSet[str] = frozenset({"CU", "IR", "KP"})

#: Date the embargo defaults above were last verified against primary sources.
DEFAULT_EMBARGO_LISTS_AS_OF = date(2026, 8, 28)

# ---------------------------------------------------------------------------
# Territorial (sub-national) embargoes
#
# These are the reason a country code alone is not a sufficient embargo check.
# E.O. 13685 embargoes the Crimea region of Ukraine; E.O. 14065 embargoes the
# so-called DNR and LNR and any further "Covered Regions" designated by the
# Secretary of the Treasury — explicitly the covered regions, *not* the whole of
# the Donetsk and Luhansk oblasts. Every one of these territories is
# internationally recognised as part of Ukraine and reports the ISO 3166-1
# country code "UA". A screen that only compares country codes therefore clears
# a Sevastopol counterparty every single time, and the old "RU_CRIMEA"
# pseudo-code this skill used to ship could never fire, because no upstream
# system emits it.
#
# Keys are ISO 3166-2 subdivision codes, the namespace that can actually
# represent these regions.
# ---------------------------------------------------------------------------
DEFAULT_EMBARGOED_TERRITORIES: Dict[str, str] = {
    "UA-43": "Autonomous Republic of Crimea (E.O. 13685)",
    "UA-40": "Sevastopol (E.O. 13685)",
    "UA-14": "Donetsk oblast — Covered Regions only (E.O. 14065)",
    "UA-09": "Luhansk oblast — Covered Regions only (E.O. 14065)",
}

# ---------------------------------------------------------------------------
# Country normalisation
#
# Resolve everything to ISO 3166-1 alpha-2 and raise on anything unresolvable.
# An unrecognised jurisdiction must never be treated as "not on any list": that
# makes the screen fail open and it will never raise an alert about itself.
# ---------------------------------------------------------------------------
_COUNTRY_ALIASES: Dict[str, str] = {
    "IRAN": "IR", "ISLAMIC REPUBLIC OF IRAN": "IR", "IRN": "IR",
    "NORTH KOREA": "KP", "DPRK": "KP", "PRK": "KP",
    "DEMOCRATIC PEOPLES REPUBLIC OF KOREA": "KP",
    "KOREA DEMOCRATIC PEOPLES REPUBLIC OF": "KP",
    "CUBA": "CU", "CUB": "CU",
    "SYRIA": "SY", "SYRIAN ARAB REPUBLIC": "SY", "SYR": "SY",
    "RUSSIA": "RU", "RUSSIAN FEDERATION": "RU", "RUS": "RU",
    "UKRAINE": "UA", "UKR": "UA",
    "BELARUS": "BY", "BLR": "BY",
    "MYANMAR": "MM", "BURMA": "MM", "MMR": "MM",
    "VENEZUELA": "VE", "VEN": "VE",
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US",
    "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB", "GBR": "GB", "UK": "GB",
    "GERMANY": "DE", "DEU": "DE",
    "FRANCE": "FR", "FRA": "FR",
    "SWITZERLAND": "CH", "CHE": "CH",
    "SINGAPORE": "SG", "SGP": "SG",
    "HONG KONG": "HK", "HKG": "HK",
    "JAPAN": "JP", "JPN": "JP",
    "INDIA": "IN", "IND": "IN",
    "CHINA": "CN", "CHN": "CN",
}

#: Placeholder codes upstream systems emit for "unknown jurisdiction". Each is a
#: syntactically valid two-letter token, so without this set they sail through
#: the alpha-2 check and then screen clean against every list.
_UNKNOWN_COUNTRY_PLACEHOLDERS: FrozenSet[str] = frozenset({
    "XX", "ZZ", "QQ", "NA", "UN", "N/A", "NONE", "NULL", "UNKNOWN", "??",
})

_ALPHA2_RE = re.compile(r"^[A-Z]{2}$")
_ISO_3166_2_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")


def normalize_country(raw: Optional[str]) -> str:
    """Resolve a country identifier to an ISO 3166-1 alpha-2 code, or raise.

    Accepts alpha-2, common alpha-3 codes, and common English names. Raises
    :class:`SanctionsScreeningError` on anything it cannot resolve, including
    blanks and the ``XX``/``ZZ``-style placeholders upstream systems emit for
    "unknown" — because an unresolved jurisdiction screened as clean is a false
    negative that no alert will ever surface.

    >>> normalize_country("Iran"), normalize_country("ir"), normalize_country("IRN")
    ('IR', 'IR', 'IR')
    """
    if raw is None:
        raise SanctionsScreeningError("country is required; got None")
    if not isinstance(raw, str):
        raise SanctionsScreeningError(f"country must be a string; got {type(raw).__name__}")

    # Collapse punctuation and whitespace so "  KP  ", "K.P." and "Korea, North"
    # cannot slip past on formatting alone.
    folded = _strip_accents(raw).upper().strip()
    compact = re.sub(r"[^A-Z0-9]+", " ", folded).strip()

    if not compact:
        raise SanctionsScreeningError("country is required; got a blank value")
    if compact in _UNKNOWN_COUNTRY_PLACEHOLDERS or folded in _UNKNOWN_COUNTRY_PLACEHOLDERS:
        raise SanctionsScreeningError(
            f"country {raw!r} is an 'unknown jurisdiction' placeholder, not a country. "
            "Resolve it upstream; it cannot be screened."
        )
    if compact in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[compact]
    if _ALPHA2_RE.match(compact):
        return compact

    raise SanctionsScreeningError(
        f"country {raw!r} could not be resolved to an ISO 3166-1 alpha-2 code. "
        "Screening an unresolved jurisdiction would fail open; extend "
        "_COUNTRY_ALIASES or fix the upstream mapping."
    )


def normalize_territory(raw: Optional[str]) -> Optional[str]:
    """Normalise an optional ISO 3166-2 subdivision code, or raise if malformed.

    ``None`` and blank mean "no subdivision supplied" and are allowed — most
    counterparties have no territorial exposure. A *malformed* value is rejected,
    because a subdivision the engine silently drops is a territorial embargo that
    silently does not apply.

    >>> normalize_territory("ua-43")
    'UA-43'
    >>> normalize_territory(None) is None
    True
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise SanctionsScreeningError(
            f"region_code must be a string or None; got {type(raw).__name__}")

    compact = raw.upper().strip().replace("_", "-")
    if not compact:
        return None
    if not _ISO_3166_2_RE.match(compact):
        raise SanctionsScreeningError(
            f"region_code {raw!r} is not an ISO 3166-2 subdivision code (e.g. 'UA-43'). "
            "Territorial embargoes cannot be evaluated from a free-text region."
        )
    return compact


# ---------------------------------------------------------------------------
# Name normalisation
#
# OFAC's 2019 Framework for Compliance Commitments names, as a root cause of
# actual sanctions violations, screening software that "did not account for
# alternative spellings of prohibited parties or countries (i.e., Habana instead
# of Havana)". Wolfsberg's 2019 screening guidance lists the same problem space:
# "alphabets, languages, cultures, spelling, abbreviations, acronyms and
# aliases".
#
# Normalising *before* measuring distance is what makes the canonical legal-form
# variant match: "VTB BANK P.J.S.C." and "VTB BANK PJSC" differ by four
# punctuation characters, which is a 76% edit similarity on the raw strings —
# under any sane threshold, and therefore a clean pass for a designated bank.
# After normalisation they are identical.
# ---------------------------------------------------------------------------
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    """Fold accents and compatibility forms to plain ASCII letters where possible.

    ``NFKD`` decomposes "Ç" into "C" + combining cedilla; dropping the combining
    marks leaves "C". Characters with no Latin decomposition (Cyrillic, Arabic,
    Han) are left intact rather than mangled — this engine does not transliterate
    across scripts, and pretending otherwise would be worse than not trying.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw: str) -> str:
    """Normalise an entity name for comparison: accents, case, punctuation, spacing.

    Punctuation is dropped and whitespace collapsed. Alphanumerics are kept for
    **every** script, not just Latin: an ASCII-only filter here would normalise a
    Cyrillic or Arabic name to the empty string, which turns a designated entity
    into a blank that matches nothing.

    >>> normalize_name("VTB Bank P.J.S.C.")
    'VTB BANK PJSC'
    >>> normalize_name("  Société   Générale  ")
    'SOCIETE GENERALE'
    >>> normalize_name("Сбербанк")
    'СБЕРБАНК'
    """
    folded = _strip_accents(raw).upper()
    # Punctuation is *deleted*, not replaced by a space. That is what collapses
    # the dotted legal form "P.J.S.C." onto "PJSC"; substituting spaces would
    # instead produce "P J S C" and leave the two spellings as far apart as
    # before.
    kept = "".join(ch for ch in folded if ch.isalnum() or ch.isspace())
    return _WHITESPACE_RE.sub(" ", kept).strip()


def _levenshtein_distance(str1: str, str2: str) -> int:
    """Levenshtein edit distance with unit insert/delete/substitute costs.

    Two-row dynamic programme: O(len1 * len2) time, O(min(len1, len2)) space.
    The full matrix is never needed because only the distance is returned, and a
    realistic screening run evaluates one subject against every list entry.
    """
    if str1 == str2:
        return 0
    if not str1:
        return len(str2)
    if not str2:
        return len(str1)

    # Iterate over the shorter string in the inner dimension to bound memory.
    if len(str1) < len(str2):
        str1, str2 = str2, str1

    previous = list(range(len(str2) + 1))
    for i, ch1 in enumerate(str1, start=1):
        current = [i]
        for j, ch2 in enumerate(str2, start=1):
            current.append(min(
                previous[j] + 1,                              # deletion
                current[j - 1] + 1,                           # insertion
                previous[j - 1] + (0 if ch1 == ch2 else 1),   # substitution
            ))
        previous = current
    return previous[-1]


def _edit_similarity_pct(str1: str, str2: str) -> float:
    """Normalised Levenshtein similarity in percent, on already-normalised strings."""
    if str1 == str2:
        return 100.0
    if not str1 or not str2:
        return 0.0
    max_len = max(len(str1), len(str2))
    return round((1.0 - _levenshtein_distance(str1, str2) / max_len) * 100.0, 2)


def _sorted_token_similarity_pct(str1: str, str2: str) -> float:
    """Edit similarity after alphabetically sorting whitespace-delimited tokens.

    Real list entries and real onboarding forms disagree about where the legal
    form goes: "PJSC SBERBANK OF RUSSIA" against a list holding "SBERBANK OF
    RUSSIA PJSC" is a word-order variant, not a different bank, but plain edit
    distance charges the full length of the moved token twice.
    """
    tokens1 = " ".join(sorted(str1.split()))
    tokens2 = " ".join(sorted(str2.split()))
    return _edit_similarity_pct(tokens1, tokens2)


def _max_possible_similarity_pct(len1: int, len2: int) -> float:
    """Upper bound on edit similarity implied by lengths alone.

    Edit distance is at least the length difference, so similarity can never
    exceed ``min/max``. Screening one subject against a real list — the OFAC SDN
    List alone runs to tens of thousands of names once aliases are counted —
    this bound skips the O(n*m) inner loop for entries that could not clear the
    threshold at any spelling. It is a pure short-circuit: it can only skip
    candidates the full computation would have scored below the bound.
    """
    if len1 == 0 or len2 == 0:
        return 0.0
    return min(len1, len2) / max(len1, len2) * 100.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SanctionedEntry:
    """One designation on a sanctions list.

    ``aliases`` carries the a.k.a. names the source list publishes for the same
    designation. They matter more than they look: OFAC publishes many designated
    entities under several names, and the name a counterparty gives you is often
    the a.k.a. rather than the primary. Screening the primary name only is the
    "alternative spellings" root cause in OFAC's Framework, in its most literal
    form.
    """

    entity_id: str
    name: str
    country_iso: str
    list_type: SanctionsListType
    program: SanctionsProgram = SanctionsProgram.BLOCKING
    aliases: Tuple[str, ...] = ()

    def all_names(self) -> Tuple[str, ...]:
        return (self.name,) + tuple(self.aliases)


@dataclass(frozen=True)
class SanctionsListSnapshot:
    """A dated list snapshot. The date is mandatory, and that is the point.

    An undated list cannot be checked for staleness, and a stale list mis-screens
    silently for as long as nobody looks. OFAC's Framework names failure to
    incorporate SDN/SSI updates as a root cause of real violations, and the SDN
    List has no predetermined update timetable, so "when was this pulled" is not
    metadata — it is part of the screening result.
    """

    entries: Tuple[SanctionedEntry, ...]
    as_of: date
    source: str = "caller-supplied"

    def __post_init__(self) -> None:
        if not isinstance(self.as_of, date):
            raise SanctionsScreeningError(
                f"SanctionsListSnapshot.as_of must be a datetime.date; "
                f"got {type(self.as_of).__name__}")
        if not self.entries:
            raise SanctionsScreeningError(
                "SanctionsListSnapshot.entries is empty. An empty list screens "
                "every subject as clean; if the feed load failed, fail the screen, "
                "do not run it.")
        for entry in self.entries:
            if not entry.entity_id.strip():
                raise SanctionsScreeningError(
                    f"designation {entry.name!r} has a blank entity_id")
            # A designation whose every name normalises away is unmatchable: it
            # sits in the list looking like coverage while screening nothing.
            if not any(normalize_name(n) for n in entry.all_names()):
                raise SanctionsScreeningError(
                    f"designation {entry.entity_id!r} has no usable name; it could "
                    "never match and would be silent coverage in the list")


@dataclass(frozen=True)
class SanctionedOwner:
    """A blocked person holding equity in the subject, for the 50 Percent Rule."""

    owner_id: str
    name: str
    ownership_pct: float


@dataclass
class ScreeningSubject:
    """A counterparty or instrument issuer to screen.

    ``region_code`` is the ISO 3166-2 subdivision (e.g. ``"UA-43"`` for Crimea).
    Supply it whenever you have it: the Crimea/DNR/LNR embargoes are territorial,
    every affected entity reports country ``UA``, and a country-code screen alone
    therefore cannot see them.

    Ownership may be given either pre-aggregated via
    ``ownership_pct_by_sanctioned`` or itemised via ``sanctioned_owners``. Prefer
    the itemised form: OFAC aggregates across blocked persons, so two blocked
    persons at 25% each block the entity, and a caller who compares each holder
    to 50% individually never triggers the rule at all.
    """

    subject_id: str
    name: str
    country_iso: str
    entity_kind: ScreeningEntityKind
    ownership_pct_by_sanctioned: float = 0.0
    sanctioned_owners: Tuple[SanctionedOwner, ...] = ()
    region_code: Optional[str] = None


@dataclass(frozen=True)
class SanctionsHit:
    subject_id: str
    matched_sanctioned_name: str
    list_type: Optional[SanctionsListType]
    program: SanctionsProgram
    match_score_pct: float
    match_method: MatchMethod
    reason: str
    matched_entity_id: Optional[str] = None


@dataclass(frozen=True)
class SanctionsScreeningReport:
    subject_id: str
    name: str
    is_cleared: bool
    has_sanctions_hit: bool
    has_embargo_violation: bool
    has_sectoral_restriction: bool
    aggregate_sanctioned_ownership_pct: float
    hits: Tuple[SanctionsHit, ...]
    advisories: Tuple[str, ...]
    status: str
    screened_on: date
    list_as_of: date
    audit_notes: str

    @property
    def requires_ofac_blocking_report(self) -> bool:
        """True when the subject is blocked property under the 50 Percent Rule.

        This is a materially different obligation from declining a counterparty:
        an entity owned 50% or more in the aggregate by blocked persons *is*
        blocked property, so the property must be blocked and reported to OFAC.
        Quietly walking away is not sufficient.
        """
        return self.status == STATUS_BLOCKED_OFAC_50


# ---------------------------------------------------------------------------
# Demonstration data — NOT a sanctions list
# ---------------------------------------------------------------------------
#: Fixtures so the tests and doctests run. Five hand-written rows are not a
#: sanctions list and screening real counterparties against them is worse than
#: not screening, because it produces a clean report. ``build_engine`` refuses to
#: default to these; you must pass them explicitly.
DEMO_SANCTIONED_DATABASE: Tuple[SanctionedEntry, ...] = (
    SanctionedEntry(
        "LEI_DEMO_001", "VTB BANK PJSC", "RU", SanctionsListType.OFAC_SDN,
        SanctionsProgram.BLOCKING, aliases=("BANK VTB PAO", "VNESHTORGBANK"),
    ),
    SanctionedEntry(
        "LEI_DEMO_002", "SBERBANK OF RUSSIA", "RU", SanctionsListType.EU_CONSOLIDATED,
        SanctionsProgram.BLOCKING, aliases=("SBERBANK ROSSII",),
    ),
    SanctionedEntry(
        "ISIN_DEMO_RU0001", "RUSSIAN FEDERAL BOND", "RU", SanctionsListType.OFAC_SDN,
        SanctionsProgram.BLOCKING,
    ),
    SanctionedEntry(
        "LEI_DEMO_003", "IRAN NATIONAL OIL CO", "IR", SanctionsListType.UN_SANCTIONS,
        SanctionsProgram.BLOCKING,
    ),
    SanctionedEntry(
        "LEI_DEMO_004", "DEMO SECTORAL ENERGY OJSC", "RU", SanctionsListType.OFAC_SSI,
        SanctionsProgram.SECTORAL,
    ),
)

DEMO_LIST_AS_OF = date(2026, 8, 28)


def demo_snapshot(as_of: Optional[date] = None) -> SanctionsListSnapshot:
    """Build a snapshot over :data:`DEMO_SANCTIONED_DATABASE` for tests and demos."""
    return SanctionsListSnapshot(
        entries=DEMO_SANCTIONED_DATABASE,
        as_of=as_of or DEMO_LIST_AS_OF,
        source="DEMO FIXTURE — not a sanctions list",
    )


# ---------------------------------------------------------------------------
# Legacy shim
# ---------------------------------------------------------------------------
@dataclass
class ComplianceResult:
    """Legacy result type, retained so existing imports keep working."""

    is_compliant: bool
    reason: str


class SanctionsScreeningForCounterpartiesAndInstrumentsEngine:
    """Fail-closed sanctions screening gate for counterparties and instrument issuers.

    Screens a subject against a dated list snapshot on identifier, primary name
    and published aliases; applies the OFAC 50 Percent Rule with aggregation
    across blocked owners; and applies comprehensive country embargoes plus
    territorial (ISO 3166-2) embargoes.

    The engine holds no mutable state between calls and does no I/O, so a given
    (subject, snapshot, screened_on) triple always produces the same report.

    >>> engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(demo_snapshot())
    >>> subject = ScreeningSubject("LEI_X", "VTB Bank P.J.S.C.", "RU",
    ...                            ScreeningEntityKind.COUNTERPARTY)
    >>> report = engine.screen_subject(subject, screened_on=date(2026, 8, 28))
    >>> report.status
    'BLOCKED_SANCTIONS_HIT'
    """

    def __init__(
        self,
        sanctions_list: Optional[SanctionsListSnapshot] = None,
        fuzzy_match_threshold_pct: float = DEFAULT_FUZZY_MATCH_THRESHOLD_PCT,
        embargoed_countries: Optional[Iterable[str]] = None,
        embargoed_territories: Optional[Dict[str, str]] = None,
        max_list_age_days: int = DEFAULT_MAX_LIST_AGE_DAYS,
    ) -> None:
        if sanctions_list is None:
            raise SanctionsScreeningError(
                "A dated SanctionsListSnapshot is required. This engine ships no "
                "sanctions data; passing none previously fell back to demo rows and "
                "reported real counterparties as CLEARED. Use demo_snapshot() "
                "explicitly for tests."
            )
        if not isinstance(sanctions_list, SanctionsListSnapshot):
            raise SanctionsScreeningError(
                f"sanctions_list must be a SanctionsListSnapshot; "
                f"got {type(sanctions_list).__name__}")

        self.sanctions_list = sanctions_list
        self.fuzzy_threshold = self._validate_threshold(fuzzy_match_threshold_pct)
        self.max_list_age_days = self._validate_age(max_list_age_days)

        raw_countries = (DEFAULT_EMBARGOED_COUNTRIES if embargoed_countries is None
                         else embargoed_countries)
        self.embargoed_countries: FrozenSet[str] = frozenset(
            normalize_country(c) for c in raw_countries)

        raw_territories = (DEFAULT_EMBARGOED_TERRITORIES if embargoed_territories is None
                           else embargoed_territories)
        self.embargoed_territories: Dict[str, str] = {}
        for code, description in raw_territories.items():
            normalized = normalize_territory(code)
            if normalized is None:
                raise SanctionsScreeningError(
                    f"embargoed_territories key {code!r} is blank")
            self.embargoed_territories[normalized] = description

        # Precompute normalised list names once, not once per screen.
        self._normalized_index: Tuple[Tuple[SanctionedEntry, str, str], ...] = tuple(
            (entry, raw_name, normalize_name(raw_name))
            for entry in self.sanctions_list.entries
            for raw_name in entry.all_names()
        )

    # -- validation helpers -------------------------------------------------
    @staticmethod
    def _validate_threshold(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SanctionsScreeningError(
                f"fuzzy_match_threshold_pct must be a number; got {type(value).__name__}")
        value = float(value)
        if math.isnan(value):
            raise SanctionsScreeningError("fuzzy_match_threshold_pct must not be NaN")
        # A threshold at or below 0 matches every list entry against every
        # subject; above 100 can never match at all. Both silently disable the
        # control, in opposite directions.
        if not 0.0 < value <= 100.0:
            raise SanctionsScreeningError(
                f"fuzzy_match_threshold_pct must be in (0, 100]; got {value}")
        return value

    @staticmethod
    def _validate_age(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SanctionsScreeningError(
                f"max_list_age_days must be an int; got {type(value).__name__}")
        if value < 0:
            raise SanctionsScreeningError(
                f"max_list_age_days must be >= 0; got {value}")
        return value

    @staticmethod
    def _validate_pct(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SanctionsScreeningError(
                f"{label} must be a number; got {type(value).__name__}")
        value = float(value)
        # NaN is the dangerous one: every comparison against it is False, so a
        # NaN ownership percentage silently passes a `>= 50.0` gate and the
        # subject is reported CLEARED.
        if math.isnan(value) or math.isinf(value):
            raise SanctionsScreeningError(f"{label} must be a finite number; got {value}")
        if not -_PCT_TOLERANCE <= value <= 100.0 + _PCT_TOLERANCE:
            raise SanctionsScreeningError(
                f"{label} must be between 0 and 100; got {value}")
        return min(max(value, 0.0), 100.0)

    def _validate_subject(self, subject: ScreeningSubject) -> Tuple[str, Optional[str], float]:
        if not isinstance(subject, ScreeningSubject):
            raise SanctionsScreeningError(
                f"subject must be a ScreeningSubject; got {type(subject).__name__}")
        if not isinstance(subject.subject_id, str) or not subject.subject_id.strip():
            raise SanctionsScreeningError("subject_id is required and must be non-blank")
        if not isinstance(subject.name, str) or not normalize_name(subject.name):
            raise SanctionsScreeningError(
                f"subject name is required and must contain at least one "
                f"alphanumeric character; got {subject.name!r}")
        if not isinstance(subject.entity_kind, ScreeningEntityKind):
            raise SanctionsScreeningError(
                f"entity_kind must be a ScreeningEntityKind; got {subject.entity_kind!r}")

        country = normalize_country(subject.country_iso)
        territory = normalize_territory(subject.region_code)
        if territory is not None and not territory.startswith(country + "-"):
            raise SanctionsScreeningError(
                f"region_code {territory!r} does not belong to country {country!r}. "
                "A mismatched subdivision would evaluate the wrong territory.")

        declared = self._validate_pct(
            subject.ownership_pct_by_sanctioned, "ownership_pct_by_sanctioned")

        itemised = 0.0
        seen_owner_ids: set = set()
        for owner in subject.sanctioned_owners:
            if not isinstance(owner, SanctionedOwner):
                raise SanctionsScreeningError(
                    f"sanctioned_owners entries must be SanctionedOwner; "
                    f"got {type(owner).__name__}")
            if not owner.owner_id.strip():
                raise SanctionsScreeningError("SanctionedOwner.owner_id must be non-blank")
            # OFAC aggregates a person's holdings; the same blocked person listed
            # twice under the same id is a data duplicate, not 2x the stake.
            if owner.owner_id in seen_owner_ids:
                raise SanctionsScreeningError(
                    f"duplicate SanctionedOwner.owner_id {owner.owner_id!r}; "
                    "aggregate each blocked person's holdings into one record")
            seen_owner_ids.add(owner.owner_id)
            itemised += self._validate_pct(
                owner.ownership_pct, f"ownership_pct for owner {owner.owner_id!r}")

        if itemised > 100.0 + _PCT_TOLERANCE:
            raise SanctionsScreeningError(
                f"sanctioned_owners hold {itemised}% in aggregate, which exceeds 100%")

        # Take the larger of the two channels rather than summing: a caller who
        # supplies both is almost certainly expressing the same stake twice, and
        # summing would double-count it into a spurious block.
        aggregate = max(declared, itemised)
        return country, territory, aggregate

    # -- matching -----------------------------------------------------------
    def _best_name_match(
        self, subject_name_norm: str, entry_name_norm: str
    ) -> Tuple[float, MatchMethod]:
        """Best score for one (subject, list name) pair, with the method that won."""
        if subject_name_norm == entry_name_norm:
            return 100.0, MatchMethod.EXACT_NAME

        # Length bound: skip the O(n*m) work when no spelling could clear the bar.
        # Sorted-token comparison permutes the same characters plus the same
        # separators, so the two strings keep their lengths and the bound holds
        # for that variant too.
        if _max_possible_similarity_pct(
                len(subject_name_norm), len(entry_name_norm)) < self.fuzzy_threshold:
            return 0.0, MatchMethod.FUZZY_EDIT_DISTANCE

        edit_score = _edit_similarity_pct(subject_name_norm, entry_name_norm)
        token_score = _sorted_token_similarity_pct(subject_name_norm, entry_name_norm)
        if token_score > edit_score:
            return token_score, MatchMethod.FUZZY_TOKEN_ORDER
        return edit_score, MatchMethod.FUZZY_EDIT_DISTANCE

    def _screen_against_list(
        self, subject: ScreeningSubject, subject_name_norm: str
    ) -> List[SanctionsHit]:
        subject_id_norm = subject.subject_id.strip().upper()

        # One hit per designation, keeping the strongest evidence. Without this,
        # an entry matched on both identifier and name reports twice and every
        # "how many hits" figure in the audit trail is inflated.
        #
        # The key is (entity_id, list_type), not entity_id alone: the same legal
        # entity is routinely designated on several lists under one identifier,
        # and collapsing on the identifier would drop every list after the first
        # from the audit trail.
        best: Dict[Tuple[str, SanctionsListType], SanctionsHit] = {}

        def offer(entry: SanctionedEntry, hit: SanctionsHit) -> None:
            key = (entry.entity_id, entry.list_type)
            existing = best.get(key)
            if existing is None or hit.match_score_pct > existing.match_score_pct:
                best[key] = hit

        for entry, raw_name, entry_name_norm in self._normalized_index:
            if subject_id_norm and subject_id_norm == entry.entity_id.strip().upper():
                offer(entry, SanctionsHit(
                    subject_id=subject.subject_id,
                    matched_sanctioned_name=entry.name,
                    list_type=entry.list_type,
                    program=entry.program,
                    match_score_pct=100.0,
                    match_method=MatchMethod.EXACT_IDENTIFIER,
                    matched_entity_id=entry.entity_id,
                    reason=(f"Exact identifier match with designated entity "
                            f"{entry.entity_id!r} on {entry.list_type.value}"),
                ))

            score, method = self._best_name_match(subject_name_norm, entry_name_norm)
            if score >= self.fuzzy_threshold:
                via_alias = raw_name != entry.name
                alias_note = f" (via alias {raw_name!r})" if via_alias else ""
                offer(entry, SanctionsHit(
                    subject_id=subject.subject_id,
                    matched_sanctioned_name=entry.name,
                    list_type=entry.list_type,
                    program=entry.program,
                    match_score_pct=score,
                    match_method=(MatchMethod.EXACT_NAME
                                  if method is MatchMethod.EXACT_NAME else method),
                    matched_entity_id=entry.entity_id,
                    reason=(f"Name match {score}% ({method.value}) against "
                            f"{entry.name!r}{alias_note} on {entry.list_type.value}"),
                ))

        return sorted(
            best.values(),
            key=lambda h: (-h.match_score_pct, h.matched_entity_id or "",
                           h.list_type.value if h.list_type else ""),
        )

    # -- public API ---------------------------------------------------------
    def screen_subject(
        self,
        subject: ScreeningSubject,
        screened_on: Optional[date] = None,
    ) -> SanctionsScreeningReport:
        """Screen one counterparty or instrument issuer. Raises rather than fails open.

        ``screened_on`` defaults to today only as a convenience; pass it
        explicitly whenever the report goes into an audit file, so the run
        reproduces.
        """
        if screened_on is None:
            screened_on = date.today()
        elif isinstance(screened_on, datetime):
            # datetime subclasses date, so an unguarded datetime passes the
            # isinstance check and then blows up on the date arithmetic below
            # with a bare TypeError. Narrow it to the calendar day instead.
            screened_on = screened_on.date()
        elif not isinstance(screened_on, date):
            raise SanctionsScreeningError(
                f"screened_on must be a datetime.date; got {type(screened_on).__name__}")

        country, territory, aggregate_ownership = self._validate_subject(subject)
        subject_name_norm = normalize_name(subject.name)

        hits: List[SanctionsHit] = []
        advisories: List[str] = []

        # 1. OFAC 50 Percent Rule, aggregated across blocked owners.
        if aggregate_ownership >= OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT:
            owner_detail = ", ".join(
                f"{o.name} ({o.ownership_pct}%)" for o in subject.sanctioned_owners
            ) or "pre-aggregated ownership supplied by caller"
            hits.append(SanctionsHit(
                subject_id=subject.subject_id,
                matched_sanctioned_name="OFAC 50 Percent Rule (aggregate ownership)",
                list_type=None,
                program=SanctionsProgram.BLOCKING,
                match_score_pct=100.0,
                match_method=MatchMethod.OWNERSHIP_50_PERCENT,
                reason=(
                    f"Blocked persons hold {aggregate_ownership}% in the aggregate "
                    f"(>= {OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT}%). Under OFAC's "
                    f"Revised Guidance of 2014-08-13 the entity is itself blocked "
                    f"property whether or not it appears on the SDN List: block and "
                    f"report to OFAC, do not merely decline. Owners: {owner_detail}"
                ),
            ))

        # 2. List screening on identifier, primary name and aliases.
        hits.extend(self._screen_against_list(subject, subject_name_norm))

        # 3. Comprehensive country embargo.
        has_embargo = country in self.embargoed_countries
        if has_embargo:
            hits.append(SanctionsHit(
                subject_id=subject.subject_id,
                matched_sanctioned_name=f"Comprehensive embargo: {country}",
                list_type=None,
                program=SanctionsProgram.BLOCKING,
                match_score_pct=100.0,
                match_method=MatchMethod.COUNTRY_EMBARGO,
                reason=f"Jurisdiction {country} is under a comprehensive OFAC embargo",
            ))

        # 4. Territorial embargo — invisible to a country-code-only screen.
        if territory is not None and territory in self.embargoed_territories:
            has_embargo = True
            hits.append(SanctionsHit(
                subject_id=subject.subject_id,
                matched_sanctioned_name=f"Territorial embargo: {territory}",
                list_type=None,
                program=SanctionsProgram.BLOCKING,
                match_score_pct=100.0,
                match_method=MatchMethod.TERRITORY_EMBARGO,
                reason=(f"Subdivision {territory} is an embargoed territory: "
                        f"{self.embargoed_territories[territory]}"),
            ))
        elif territory is None and country in {
            t.split("-")[0] for t in self.embargoed_territories
        }:
            # UA has embargoed subdivisions; without one we cannot say which.
            advisories.append(
                f"NO_REGION_SUPPLIED: {country} contains embargoed territories and no "
                f"region_code was supplied. A country-code screen alone cannot detect "
                f"Crimea/DNR/LNR exposure — resolve the subdivision before relying on "
                f"this result."
            )

        # 5. List staleness.
        list_age_days = (screened_on - self.sanctions_list.as_of).days
        if list_age_days < 0:
            raise SanctionsScreeningError(
                f"list snapshot as_of {self.sanctions_list.as_of} is after "
                f"screened_on {screened_on}; refusing to screen against a future list")
        if list_age_days > self.max_list_age_days:
            advisories.append(
                f"STALE_SANCTIONS_LIST: snapshot is {list_age_days} days old "
                f"(as_of {self.sanctions_list.as_of}, limit {self.max_list_age_days}). "
                f"The SDN List has no fixed update timetable; refresh before relying "
                f"on a clear."
            )

        blocking_hits = [h for h in hits if h.program is SanctionsProgram.BLOCKING]
        sectoral_hits = [h for h in hits if h.program is SanctionsProgram.SECTORAL]
        list_hits = [h for h in blocking_hits if h.list_type is not None]
        ownership_hits = [h for h in hits
                          if h.match_method is MatchMethod.OWNERSHIP_50_PERCENT]

        # 6. Status by fixed precedence; every hit stays in report.hits regardless.
        #
        # Driven off _STATUS_PRECEDENCE rather than an if/elif ladder so the
        # documented order and the implemented order cannot drift apart.
        # `advisories` is deliberately last before CLEARED: a stale list or an
        # unresolved embargoed-territory country is not a hit, but it is not a
        # clear either — it is a screen whose *negative* result cannot be relied
        # on.
        triggered = {
            STATUS_BLOCKED_OFAC_50: bool(ownership_hits),
            STATUS_BLOCKED_SANCTIONS_HIT: bool(list_hits),
            STATUS_BLOCKED_EMBARGO: has_embargo,
            STATUS_RESTRICTED_SECTORAL: bool(sectoral_hits),
            STATUS_REVIEW_REQUIRED: bool(advisories),
        }
        status = next(
            (candidate for candidate in _STATUS_PRECEDENCE if triggered[candidate]),
            STATUS_CLEARED,
        )

        is_cleared = status == STATUS_CLEARED

        notes = (
            f"SANCTIONS SCREENING [{status}] ({subject.name}): id={subject.subject_id}, "
            f"country={country}, region={territory or '-'}, hits={len(hits)}, "
            f"sanctioned_ownership={aggregate_ownership}%, "
            f"list_as_of={self.sanctions_list.as_of}, screened_on={screened_on}, "
            f"advisories={len(advisories)}."
        )
        if status in _BLOCKING_STATUSES:
            logger.warning(notes)
        elif is_cleared:
            logger.info(notes)
        else:
            logger.warning("%s REVIEW: %s", notes, "; ".join(advisories) or "sectoral")

        return SanctionsScreeningReport(
            subject_id=subject.subject_id,
            name=subject.name,
            is_cleared=is_cleared,
            has_sanctions_hit=bool(list_hits or ownership_hits),
            has_embargo_violation=has_embargo,
            has_sectoral_restriction=bool(sectoral_hits),
            aggregate_sanctioned_ownership_pct=aggregate_ownership,
            hits=tuple(hits),
            advisories=tuple(advisories),
            status=status,
            screened_on=screened_on,
            list_as_of=self.sanctions_list.as_of,
            audit_notes=notes,
        )

    def check(self, data: dict) -> ComplianceResult:
        """Deprecated no-op shim. **Performs no sanctions screening whatsoever.**

        It reads ``data["valid"]`` and echoes it back. It predates this skill's
        real API and is retained only so existing imports and call sites keep
        working. Wiring it into an onboarding or pre-trade gate produces a
        confident "compliant" for a subject that was never screened against
        anything — use :meth:`screen_subject`.
        """
        warnings.warn(
            "SanctionsScreeningForCounterpartiesAndInstrumentsEngine.check() performs "
            "no sanctions screening and must not be used as a compliance gate. "
            "Use screen_subject().",
            DeprecationWarning,
            stacklevel=2,
        )
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")


def build_engine(
    entries: Sequence[SanctionedEntry],
    as_of: date,
    source: str = "caller-supplied",
    **engine_kwargs,
) -> SanctionsScreeningForCounterpartiesAndInstrumentsEngine:
    """Convenience constructor from a raw entry sequence plus a mandatory ``as_of``.

    >>> engine = build_engine(DEMO_SANCTIONED_DATABASE, date(2026, 8, 28))
    >>> engine.sanctions_list.as_of
    datetime.date(2026, 8, 28)
    """
    snapshot = SanctionsListSnapshot(
        entries=tuple(entries), as_of=as_of, source=source)
    return SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
        snapshot, **engine_kwargs)


if __name__ == "__main__":  # pragma: no cover - manual smoke entry point
    import doctest

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    failures, _ = doctest.testmod()
    raise SystemExit(1 if failures else 0)
