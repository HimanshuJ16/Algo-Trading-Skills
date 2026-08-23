import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Hash algorithms accepted in pip's hash-checking mode, mapped to their hex digest length.
# pip accepts "all those supported by hashlib", but explicitly excludes md5, sha1 and
# sha224 "to avoid giving a false sense of security".
# See https://pip.pypa.io/en/stable/topics/secure-installs/
ALLOWED_HASH_ALGORITHMS = {
    "sha256": 64,
    "sha384": 96,
    "sha512": 128,
    "sha3_256": 64,
    "sha3_384": 96,
    "sha3_512": 128,
    "blake2s": 64,
    "blake2b": 128,
}

# Explicitly rejected by pip in hash-checking mode.
WEAK_HASH_ALGORITHMS = frozenset({"md5", "sha1", "sha224"})

# Line prefixes that are pip options/control directives, not package requirements.
# Counting these as packages produces false "unpinned" findings.
PIP_CONTROL_PREFIXES = (
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable",
    "-f", "--find-links", "-i", "--index-url", "--extra-index-url",
    "--no-index", "--no-binary", "--only-binary", "--prefer-binary",
    "--require-hashes", "--pre", "--trusted-host", "--use-feature",
    "--global-option", "--config-settings", "--no-deps",
)

_HASH_OPTION_RE = re.compile(r"--hash[=\s]+([A-Za-z0-9_]+):([A-Za-z0-9]+)")
_INLINE_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
_NAME_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?\s*(?P<rest>.*)$", re.DOTALL)

# Default weights for the composite score. Internal engineering heuristic, NOT a standard.
DEFAULT_PIN_WEIGHT = 0.5
DEFAULT_HASH_WEIGHT = 0.5


@dataclass
class PackageRequirement:
    package_name: str
    raw_spec: str
    is_pinned_exact: bool                   # True only for an exact '==X.Y.Z' pin (or '===' / direct URL)
    pinned_version: Optional[str]
    sha256_hash: Optional[str]              # first VALID strong hash, as 'algo:digest'; None if none valid
    all_hashes: List[str] = field(default_factory=list)      # every valid strong hash on the requirement
    invalid_hashes: List[str] = field(default_factory=list)  # malformed or weak-algorithm hashes
    notes: List[str] = field(default_factory=list)


@dataclass
class DependencyAuditReport:
    total_packages_audited: int
    pinned_packages_count: int
    unpinned_packages_count: int
    missing_hashes_count: int               # packages with no VALID strong hash
    reproducibility_score: float            # 0.0 to 100.0
    all_requirements_pinned_and_hashed: bool
    unpinned_packages: List[str]
    packages_with_invalid_hashes: List[str]
    skipped_directive_lines: List[str]
    warnings: List[str]
    requirements: List[PackageRequirement]
    generated_lockfile_lines: List[str]


class ReproducibleBuildPinnerEngine:
    """
    Auditor for pip requirements files: reports which requirements are pinned to an exact
    version and carry a valid strong package hash, so a lockfile can be checked before it
    is used with pip's hash-checking mode (``pip install --require-hashes``).

    Scope and limits -- read before relying on the verdict:

    * This audits **requirements.txt-style** lines only. It does not parse ``poetry.lock``,
      ``Pipfile.lock``, or the PEP 751 ``pylock.toml`` standard lock format.
    * Pinning plus hash verification gives a **reproducible install** -- the same artifacts
      are fetched and integrity-checked every time. It does **not** give a reproducible
      *build* in the reproducible-builds.org sense (bit-for-bit identical artifacts), which
      additionally requires a controlled build environment, instructions, and handling of
      timestamps, locales, build paths and file ordering.
    * It **cannot** verify that every transitive dependency is present. pip's hash-checking
      mode requires hashes for *all* dependencies and errors on any that is not spelled out.
      A file can pass this audit and still fail ``pip install --require-hashes``. Only a
      resolver (``pip-compile --generate-hashes``, ``uv pip compile``) can establish that.
    * This engine never invents a version or a hash. Deficient requirements are reported
      with the command needed to resolve them; they are not silently "remediated".
    """

    def __init__(
        self,
        target_python_version: str = "3.11.8",
        pin_weight: float = DEFAULT_PIN_WEIGHT,
        hash_weight: float = DEFAULT_HASH_WEIGHT,
    ) -> None:
        """
        Args:
            target_python_version: recorded in the generated lockfile header for traceability.
            pin_weight / hash_weight: composite score weights; must be non-negative and sum
                to 1.0. These are internal heuristics, not an external standard.

        Raises:
            ValueError: on a blank python version or weights that do not sum to 1.0.
        """
        if not isinstance(target_python_version, str) or not target_python_version.strip():
            raise ValueError("target_python_version must be a non-empty string")
        for name, w in (("pin_weight", pin_weight), ("hash_weight", hash_weight)):
            if not isinstance(w, (int, float)) or isinstance(w, bool):
                raise ValueError(f"{name} must be a real number, got {w!r}")
            if w < 0.0:
                raise ValueError(f"{name} must be >= 0.0, got {w}")
        if abs((pin_weight + hash_weight) - 1.0) > 1e-9:
            raise ValueError(
                f"pin_weight + hash_weight must equal 1.0, got {pin_weight + hash_weight}"
            )

        self.target_python_version = target_python_version.strip()
        self.pin_weight = float(pin_weight)
        self.hash_weight = float(hash_weight)

    @staticmethod
    def _join_continuations(requirements_lines: List[str]) -> List[str]:
        """
        Joins backslash-continued lines into one logical requirement.

        ``pip-compile --generate-hashes`` emits one requirement across several physical
        lines::

            numpy==1.26.4 \\
                --hash=sha256:<64 hex> \\
                --hash=sha256:<64 hex>

        Treating those physical lines independently reads the ``--hash`` lines as separate
        nameless packages, so a correctly locked file audits as mostly unpinned.
        """
        logical: List[str] = []
        buffer = ""
        for raw in requirements_lines:
            if raw is None:
                raise ValueError("requirements_lines must not contain None entries")
            if not isinstance(raw, str):
                raise ValueError(f"requirements_lines must contain strings, got {raw!r}")
            stripped = raw.strip()
            if stripped.endswith("\\"):
                buffer += stripped[:-1].strip() + " "
                continue
            buffer += stripped
            logical.append(buffer.strip())
            buffer = ""
        if buffer.strip():
            logical.append(buffer.strip())
        return logical

    @staticmethod
    def _validate_hash(algorithm: str, digest: str) -> bool:
        """A hash counts only if the algorithm is strong AND the digest is hex of full length."""
        algo = algorithm.lower()
        if algo not in ALLOWED_HASH_ALGORITHMS:
            return False
        expected_len = ALLOWED_HASH_ALGORITHMS[algo]
        if len(digest) != expected_len:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in digest)

    def audit_requirements_file(self, requirements_lines: List[str]) -> DependencyAuditReport:
        """
        Audits requirements lines for exact version pinning and valid strong hashes.

        Raises:
            ValueError: if requirements_lines is not a list of strings.
        """
        if not isinstance(requirements_lines, list):
            raise ValueError(
                f"requirements_lines must be a list of strings, got {type(requirements_lines).__name__}"
            )

        parsed_reqs: List[PackageRequirement] = []
        unpinned: List[str] = []
        invalid_hash_pkgs: List[str] = []
        skipped: List[str] = []
        warnings: List[str] = []

        for line in self._join_continuations(requirements_lines):
            line_clean = _INLINE_COMMENT_RE.sub("", line).strip()
            if not line_clean or line_clean.startswith("#"):
                continue

            first_token = line_clean.split()[0]
            if first_token in PIP_CONTROL_PREFIXES or first_token.split("=")[0] in PIP_CONTROL_PREFIXES:
                skipped.append(line_clean)
                continue

            if not _HASH_OPTION_RE.sub("", line_clean).strip():
                # A line of nothing but hash options: a continuation whose requirement is
                # missing. Counting it as a nameless package is what made the old parser
                # mis-audit pip-compile output, so record it as malformed instead.
                skipped.append(line_clean)
                warnings.append(f"malformed line (hash options with no requirement): {line_clean}")
                continue

            req = self._parse_requirement(line_clean)
            parsed_reqs.append(req)

            if not req.is_pinned_exact:
                unpinned.append(req.package_name)
            if req.invalid_hashes:
                invalid_hash_pkgs.append(req.package_name)
            for note in req.notes:
                warnings.append(f"{req.package_name}: {note}")

        total = len(parsed_reqs)
        pinned_cnt = sum(1 for r in parsed_reqs if r.is_pinned_exact)
        unpinned_cnt = total - pinned_cnt
        hashed_cnt = sum(1 for r in parsed_reqs if r.sha256_hash is not None)
        missing_hash_cnt = total - hashed_cnt

        # Proportional composite score. Absolute per-package penalties saturate at zero on
        # any realistic lockfile -- pip's hash-checking mode requires every transitive
        # dependency to be listed, so production files routinely hold hundreds of entries.
        if total == 0:
            score = 100.0
        else:
            score = round(
                100.0 * (
                    self.pin_weight * (pinned_cnt / total)
                    + self.hash_weight * (hashed_cnt / total)
                ),
                2,
            )

        all_pinned_and_hashed = total > 0 and unpinned_cnt == 0 and missing_hash_cnt == 0
        if total == 0:
            all_pinned_and_hashed = True

        if not all_pinned_and_hashed:
            logger.warning(
                "REPRODUCIBILITY RISK: Score=%s/100. Unpinned=%d, Missing valid hashes=%d.",
                score, unpinned_cnt, missing_hash_cnt,
            )
        for w in warnings:
            logger.warning("REQUIREMENT WARNING: %s", w)

        return DependencyAuditReport(
            total_packages_audited=total,
            pinned_packages_count=pinned_cnt,
            unpinned_packages_count=unpinned_cnt,
            missing_hashes_count=missing_hash_cnt,
            reproducibility_score=score,
            all_requirements_pinned_and_hashed=all_pinned_and_hashed,
            unpinned_packages=unpinned,
            packages_with_invalid_hashes=invalid_hash_pkgs,
            skipped_directive_lines=skipped,
            warnings=warnings,
            requirements=parsed_reqs,
            generated_lockfile_lines=self._render_lockfile(parsed_reqs),
        )

    def _render_lockfile(self, reqs: List[PackageRequirement]) -> List[str]:
        """
        Renders a lockfile draft.

        Compliant requirements are passed through verbatim. Deficient ones are emitted as
        commented TODOs naming the command that resolves them. A version or hash is NEVER
        fabricated: a synthesised hash either fails ``pip install --require-hashes`` or, if
        it were ever trusted, would defeat the integrity check the hash exists to provide.
        """
        out: List[str] = [
            f"# Lockfile draft from ReproducibleBuildPinnerEngine (target Python {self.target_python_version})",
            "# Compliant requirements are passed through verbatim.",
            "# Deficient requirements are commented out -- resolve them with:",
            "#   pip-compile --generate-hashes  (or: uv pip compile --generate-hashes)",
            "# This draft is NOT installable until every TODO below is resolved.",
        ]
        for r in reqs:
            if r.is_pinned_exact and r.sha256_hash:
                hashes = " ".join(f"--hash={h}" for h in r.all_hashes)
                out.append(f"{r.package_name}=={r.pinned_version} {hashes}")
            elif not r.is_pinned_exact:
                out.append(
                    f"# TODO(unpinned): {r.raw_spec}  "
                    f"-- resolve to an exact '==' version and add hashes"
                )
            else:
                out.append(
                    f"# TODO(missing-hash): {r.package_name}=={r.pinned_version}  "
                    f"-- add hashes via 'pip hash <downloaded-file>'"
                )
        return out

    def _parse_requirement(self, line: str) -> PackageRequirement:
        """Parses one logical requirement line into a PackageRequirement."""
        raw_spec = line
        notes: List[str] = []

        valid_hashes: List[str] = []
        invalid_hashes: List[str] = []
        for algo, digest in _HASH_OPTION_RE.findall(line):
            token = f"{algo}:{digest}"
            if self._validate_hash(algo, digest):
                valid_hashes.append(token.lower())
            else:
                invalid_hashes.append(token)
                if algo.lower() in WEAK_HASH_ALGORITHMS:
                    notes.append(
                        f"hash algorithm '{algo}' is rejected by pip's hash-checking mode as too weak"
                    )
                else:
                    notes.append(f"malformed or unsupported hash '{token}'")

        # Strip hash options and environment markers before reading the version specifier.
        spec_part = _HASH_OPTION_RE.sub("", line).strip()
        if ";" in spec_part:
            spec_part = spec_part.split(";", 1)[0].strip()

        if not spec_part:
            # An orphan '--hash=...' line: a continuation whose requirement line is missing.
            # Counting it as a nameless package is what made the old parser mis-audit
            # pip-compile output, so surface it as a malformed line instead.
            notes.append("hash option with no requirement; the file may be malformed")
            return PackageRequirement(
                package_name="", raw_spec=raw_spec, is_pinned_exact=False,
                pinned_version=None, sha256_hash=None,
                all_hashes=valid_hashes, invalid_hashes=invalid_hashes, notes=notes,
            )

        match = _NAME_RE.match(spec_part)
        if match is None:
            notes.append("could not parse a package name from this line")
            return PackageRequirement(
                package_name=spec_part, raw_spec=raw_spec, is_pinned_exact=False,
                pinned_version=None, sha256_hash=valid_hashes[0] if valid_hashes else None,
                all_hashes=valid_hashes, invalid_hashes=invalid_hashes, notes=notes,
            )

        pkg_name = match.group("name")
        if match.group("extras"):
            notes.append(f"extras {match.group('extras')} requested; their deps must also be locked")
        rest = match.group("rest").strip()

        is_pinned = False
        version: Optional[str] = None

        if rest.startswith("@"):
            # PEP 508 direct reference. pip accepts a URL/path as "pinned", but only an
            # immutable URL actually pins anything.
            is_pinned = True
            version = rest[1:].strip()
            notes.append("pinned by direct URL/path reference; ensure the target is immutable")
        elif rest.startswith("==="):
            is_pinned = True
            version = rest[3:].strip()
            notes.append("uses PEP 440 arbitrary equality '==='; its use is heavily discouraged")
        elif rest.startswith("=="):
            version = rest[2:].strip()
            # Split off any additional clause, e.g. '==2.2.1,!=2.2.0'.
            version = version.split(",")[0].strip()
            if version.endswith(".*"):
                # PEP 440 prefix matching: '==2.2.*' accepts any 2.2.x. Not an exact pin.
                is_pinned = False
                notes.append(
                    f"'=={version}' is PEP 440 prefix matching, not an exact pin; "
                    f"it accepts any matching release"
                )
                version = None
            elif not version:
                is_pinned = False
                version = None
                notes.append("'==' with no version")
            else:
                is_pinned = True

        return PackageRequirement(
            package_name=pkg_name,
            raw_spec=raw_spec,
            is_pinned_exact=is_pinned,
            pinned_version=version,
            sha256_hash=valid_hashes[0] if valid_hashes else None,
            all_hashes=valid_hashes,
            invalid_hashes=invalid_hashes,
            notes=notes,
        )
