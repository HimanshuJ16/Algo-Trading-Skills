"""Blameless post-mortem review engine for trading-system incidents.

Scans every free-text section of a proposed post-mortem for personal-blame and
counterfactual language, enforces minimum completeness (systemic contributing
factors and corrective actions), and renders an approved post-mortem as a
Markdown document suitable for retention as an incident record.

Terminology follows Google's SRE Book, Ch. 15 "Postmortem Culture: Learning
from Failure", and John Allspaw, "Blameless PostMortems and a Just Culture"
(Etsy Code as Craft, 2012). Neither source defines the numeric thresholds used
here; those are configurable house defaults. See ``references/standards.md``.

This module performs lexical screening only. It cannot determine whether a
narrative is *substantively* blameless, and it is not a substitute for a
facilitated review meeting.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Citation embedded in generated documents. Do not present this as a formal
#: standard: it is a published practice reference, not a normative document.
SRE_POSTMORTEM_REFERENCE = (
    "Google SRE Book Ch. 15, 'Postmortem Culture: Learning from Failure'"
)

#: Canonical blame terms, retained as the module's public vocabulary. Matching
#: is performed by the compiled patterns below, which also cover inflections.
BLAME_KEYWORDS: List[str] = [
    "forgot", "careless", "human error", "blame", "fault",
    "stupid", "lazy", "negligent", "trader error", "developer error",
]

# (canonical term, regex). Inflections are matched, but never at the cost of a
# collision with the surrounding word: 'blame' must not fire on 'blameless',
# and 'fault' must not fire on 'default'.
_BLAME_PATTERN_SOURCES: List[Tuple[str, str]] = [
    ("forgot", r"\bforgot\b|\bforgets\b|\bforgetting\b|\bforgetful(?:ness)?\b"),
    ("careless", r"\bcareless(?:ly|ness)?\b"),
    ("human error", r"\bhuman[\s\-_]+errors?\b"),
    ("blame", r"\bblam(?:e|es|ed|ing)\b|\bblameworthy\b"),
    ("fault", r"\bfaults?\b"),
    ("stupid", r"\bstupid(?:ly|ity)?\b"),
    ("lazy", r"\blazy\b"),
    ("negligent", r"\bnegligen(?:t|tly|ce)\b"),
    ("trader error", r"\btraders?[\s\-_]+errors?\b"),
    ("developer error", r"\bdevelopers?[\s\-_]+errors?\b"),
]

#: Counterfactual phrasing ("the operator should have noticed") describes a
#: history that did not happen and, per Allspaw (2012), crowds out the account
#: of what actually did. It is reported as an advisory, never as a blocker:
#: the same phrasing is legitimate when applied to a system ("the alert should
#: have fired"), and a blocking check here would train reviewers to disable it.
_COUNTERFACTUAL_PATTERN_SOURCES: List[Tuple[str, str]] = [
    ("should have", r"\bshould(?:\s+have|'ve)\b"),
    ("could have", r"\bcould(?:\s+have|'ve)\b"),
    ("would have", r"\bwould(?:\s+have|'ve)\b"),
    ("failed to", r"\bfailed\s+to\b"),
]

#: Established engineering terms that contain a blame token. A keyword match
#: falling inside one of these spans is suppressed. Without this, "the
#: fault-tolerant failover did not engage" and "lazy loading delayed startup"
#: are rejected as accusatory, which is both wrong and corrosive to adoption.
_EXEMPT_PHRASES: List[str] = [
    "fault tolerance", "fault tolerant", "fault domain", "fault injection",
    "fault isolation", "fault detection", "fault containment", "fault code",
    "segmentation fault", "page fault", "bus fault", "no fault",
    "lazy loading", "lazy load", "lazy evaluation", "lazy initialization",
    "lazy initialisation",
]


def _compile(sources: Sequence[Tuple[str, str]]) -> List[Tuple[str, re.Pattern[str]]]:
    return [(term, re.compile(rx, re.IGNORECASE)) for term, rx in sources]


_BLAME_PATTERNS = _compile(_BLAME_PATTERN_SOURCES)
_COUNTERFACTUAL_PATTERNS = _compile(_COUNTERFACTUAL_PATTERN_SOURCES)
_EXEMPT_PATTERN = re.compile(
    "|".join(r"\b" + r"[\s\-_]+".join(re.escape(w) for w in phrase.split()) + r"\b"
             for phrase in _EXEMPT_PHRASES),
    re.IGNORECASE,
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTEXT_RADIUS = 40
_MAX_FIELD_CHARS = 200_000  # guards against pathological inputs

# Status constants (also documented in SKILL.md).
STATUS_APPROVED = "BLAMELESS_POSTMORTEM_APPROVED"
STATUS_APPROVED_WITH_ADVISORIES = "BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES"
STATUS_BLAME_DETECTED = "BLAME_LANGUAGE_DETECTED"
STATUS_INCOMPLETE = "POSTMORTEM_INCOMPLETE"


@dataclass
class Config:
    """Review policy. Thresholds are house defaults, not published standards."""

    name: str = "default"
    strict_blame_check: bool = True
    min_systemic_factors: int = 2
    min_corrective_actions: int = 1

    def __post_init__(self) -> None:
        if self.min_systemic_factors < 0:
            raise ValueError("min_systemic_factors must be >= 0")
        if self.min_corrective_actions < 0:
            raise ValueError("min_corrective_actions must be >= 0")


@dataclass
class BlameFinding:
    """One lexical hit, located so a reviewer can act on it."""

    section: str          # 'summary', 'narrative', 'systemic_factors[0]', ...
    term: str             # canonical term from BLAME_KEYWORDS / counterfactuals
    category: str         # 'BLAME' | 'COUNTERFACTUAL'
    context: str          # surrounding text, whitespace-collapsed

    def __str__(self) -> str:
        return f"{self.section}: '{self.term}' -> \"{self.context}\""


@dataclass
class BlamelessPostmortemInput:
    incident_id: str
    incident_date: str                   # ISO-8601 calendar date, 'YYYY-MM-DD'
    summary: str
    systemic_factors: List[str]          # Process/Tooling/Architecture gaps
    narrative: str
    proposed_actions: List[str]


@dataclass
class BlamelessPostmortemReport:
    incident_id: str
    blame_detected: bool
    detected_blame_terms: List[str]
    is_approved: bool
    markdown_document: str
    status: str
    audit_notes: str
    blame_findings: List[BlameFinding] = field(default_factory=list)
    advisory_findings: List[BlameFinding] = field(default_factory=list)
    completeness_gaps: List[str] = field(default_factory=list)


class BlamelessPostmortemGenerator:
    """Screens a proposed post-mortem and renders it once it passes.

    The screen covers *every* free-text section that reaches the document --
    summary, systemic factors, narrative and proposed actions -- because a
    document is not blameless when only one of its four sections was checked.
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config("default")

    def process(self) -> bool:
        """Legacy no-op retained for backward compatibility."""
        return True

    # -- screening -------------------------------------------------------

    @staticmethod
    def _exempt_spans(text: str) -> List[Tuple[int, int]]:
        return [m.span() for m in _EXEMPT_PATTERN.finditer(text)]

    @staticmethod
    def _context(text: str, start: int, end: int) -> str:
        lo = max(0, start - _CONTEXT_RADIUS)
        hi = min(len(text), end + _CONTEXT_RADIUS)
        snippet = " ".join(text[lo:hi].split())
        prefix = "..." if lo > 0 else ""
        suffix = "..." if hi < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    def _scan(
        self,
        section: str,
        text: str,
        patterns: Sequence[Tuple[str, re.Pattern[str]]],
        category: str,
    ) -> List[BlameFinding]:
        """Return one finding per (section, term), with located context."""
        if not text:
            return []
        exempt = self._exempt_spans(text)
        findings: List[BlameFinding] = []
        for term, pattern in patterns:
            for match in pattern.finditer(text):
                if any(lo <= match.start() and match.end() <= hi
                       for lo, hi in exempt):
                    continue
                findings.append(BlameFinding(
                    section=section,
                    term=term,
                    category=category,
                    context=self._context(text, match.start(), match.end()),
                ))
                break  # one finding per term per section keeps reports actionable
        return findings

    def _scan_all_sections(
        self, inp: BlamelessPostmortemInput
    ) -> Tuple[List[BlameFinding], List[BlameFinding]]:
        sections: List[Tuple[str, str]] = [
            ("summary", inp.summary),
            ("narrative", inp.narrative),
        ]
        sections += [(f"systemic_factors[{i}]", text)
                     for i, text in enumerate(inp.systemic_factors)]
        sections += [(f"proposed_actions[{i}]", text)
                     for i, text in enumerate(inp.proposed_actions)]

        blame: List[BlameFinding] = []
        counterfactual: List[BlameFinding] = []
        for name, text in sections:
            blame += self._scan(name, text, _BLAME_PATTERNS, "BLAME")
            counterfactual += self._scan(
                name, text, _COUNTERFACTUAL_PATTERNS, "COUNTERFACTUAL")
        return blame, counterfactual

    # -- validation ------------------------------------------------------

    @staticmethod
    def _require_text(value: object, name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string, got {type(value).__name__}")
        if not value.strip():
            raise ValueError(f"{name} must be non-empty")
        if len(value) > _MAX_FIELD_CHARS:
            raise ValueError(f"{name} exceeds {_MAX_FIELD_CHARS} characters")
        return value

    @classmethod
    def _validate_structure(cls, inp: BlamelessPostmortemInput) -> None:
        """Raise ValueError on malformed input.

        Malformed structure is a caller bug and raises. Insufficient *content*
        (too few systemic factors or actions) is a review finding, not a bug,
        and is reported as POSTMORTEM_INCOMPLETE instead.
        """
        cls._require_text(inp.incident_id, "incident_id")
        cls._require_text(inp.summary, "summary")
        cls._require_text(inp.narrative, "narrative")
        cls._require_text(inp.incident_date, "incident_date")
        if not _ISO_DATE.fullmatch(inp.incident_date.strip()):
            raise ValueError(
                f"incident_date must be an ISO-8601 date 'YYYY-MM-DD', "
                f"got {inp.incident_date!r}")
        try:
            date.fromisoformat(inp.incident_date.strip())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"incident_date must be an ISO-8601 date 'YYYY-MM-DD', "
                f"got {inp.incident_date!r}") from exc

        for name, items in (("systemic_factors", inp.systemic_factors),
                            ("proposed_actions", inp.proposed_actions)):
            if not isinstance(items, (list, tuple)):
                raise ValueError(f"{name} must be a list of strings")
            for i, item in enumerate(items):
                cls._require_text(item, f"{name}[{i}]")

    def _completeness_gaps(self, inp: BlamelessPostmortemInput) -> List[str]:
        gaps: List[str] = []
        n_factors = len(inp.systemic_factors)
        n_actions = len(inp.proposed_actions)
        if n_factors < self.config.min_systemic_factors:
            gaps.append(
                f"systemic_factors: {n_factors} provided, "
                f"{self.config.min_systemic_factors} required -- stopping at a "
                f"single cause usually means the review stopped at the trigger "
                f"rather than the conditions that let it reach production")
        if n_actions < self.config.min_corrective_actions:
            gaps.append(
                f"proposed_actions: {n_actions} provided, "
                f"{self.config.min_corrective_actions} required -- a post-mortem "
                f"with no CAPA item records the incident without reducing the "
                f"chance of recurrence")
        return gaps

    # -- rendering -------------------------------------------------------

    @staticmethod
    def _escape_block(text: str) -> str:
        """Neutralise headings inside author-supplied prose.

        A narrative line beginning with '#' would otherwise forge a section
        heading in a retained incident record. The backslash escape renders as
        a literal '#', so the author's wording is preserved verbatim.
        """
        return "\n".join(
            re.sub(r"^(\s{0,3})(#{1,6}\s)", r"\1\\\2", line)
            for line in text.splitlines()
        )

    @staticmethod
    def _as_list_item(text: str) -> str:
        """Collapse embedded newlines so one entry stays one bullet."""
        return " ".join(text.split())

    def _render(self, inp: BlamelessPostmortemInput,
                advisories: Sequence[BlameFinding]) -> str:
        md: List[str] = [
            f"# BLAMELESS POST-MORTEM REPORT: {inp.incident_id}",
            f"**Date**: {inp.incident_date} | **Method**: {SRE_POSTMORTEM_REFERENCE}",
            "",
            "## 1. Executive Summary",
            self._escape_block(inp.summary),
            "",
            "## 2. Systemic & Architectural Factors",
        ]
        md += [f"- {self._as_list_item(factor)}" for factor in inp.systemic_factors]
        md += ["", "## 3. Incident Narrative (Blameless)",
               self._escape_block(inp.narrative)]
        md += ["", "## 4. Corrective & Preventative Actions (CAPA)"]
        md += [f"- [ ] {self._as_list_item(act)}" for act in inp.proposed_actions]
        if advisories:
            md += ["", "## 5. Reviewer Advisories (not blocking)"]
            md += [f"- {advisory}" for advisory in advisories]
        return "\n".join(md)

    # -- entry point -----------------------------------------------------

    def generate_blameless_postmortem(
        self, inp: BlamelessPostmortemInput
    ) -> BlamelessPostmortemReport:
        """Screen every section, then render the document if it passes.

        Raises:
            ValueError: if the input is structurally malformed.
        """
        self._validate_structure(inp)

        blame_findings, advisory_findings = self._scan_all_sections(inp)
        detected_terms = sorted({f.term for f in blame_findings})
        blame_detected = bool(blame_findings)
        gaps = self._completeness_gaps(inp)

        blocking_blame = blame_detected and self.config.strict_blame_check
        if not blocking_blame:
            # Non-strict mode downgrades blame hits to advisories rather than
            # discarding them: an advisory-mode run must not look clean.
            advisory_findings = blame_findings + advisory_findings

        if blocking_blame or gaps:
            if blocking_blame:
                status = STATUS_BLAME_DETECTED
                headline = (
                    f"BLAME LANGUAGE DETECTED in post-mortem [{inp.incident_id}]: "
                    f"terms = {detected_terms}. Reframing required -- replace "
                    f"personal-fault phrasing with the systemic conditions that "
                    f"allowed the action to reach production.")
                detail = [str(f) for f in blame_findings] + gaps
            else:
                status = STATUS_INCOMPLETE
                headline = (
                    f"POST-MORTEM INCOMPLETE [{inp.incident_id}]: "
                    f"{len(gaps)} completeness gap(s).")
                detail = gaps
            notes = " | ".join([headline] + detail)
            logger.warning(notes)
            return BlamelessPostmortemReport(
                incident_id=inp.incident_id,
                blame_detected=blame_detected,
                detected_blame_terms=detected_terms,
                is_approved=False,
                markdown_document="",
                status=status,
                audit_notes=notes,
                blame_findings=blame_findings,
                advisory_findings=advisory_findings,
                completeness_gaps=gaps,
            )

        markdown_doc = self._render(inp, advisory_findings)
        status = (STATUS_APPROVED_WITH_ADVISORIES if advisory_findings
                  else STATUS_APPROVED)
        notes = (
            f"{status} [{inp.incident_id}]: systemic factors = "
            f"{len(inp.systemic_factors)}, actions = {len(inp.proposed_actions)}, "
            f"advisories = {len(advisory_findings)}.")
        logger.info(notes)

        return BlamelessPostmortemReport(
            incident_id=inp.incident_id,
            blame_detected=blame_detected,
            detected_blame_terms=detected_terms,
            is_approved=True,
            markdown_document=markdown_doc,
            status=status,
            audit_notes=notes,
            blame_findings=blame_findings,
            advisory_findings=advisory_findings,
            completeness_gaps=[],
        )
