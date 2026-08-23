# Standards for Dependency Pinning and Reproducible Builds

## What is actually specified, and what is this tool's own heuristic

The pinning and hashing rules below come from pip's documented hash-checking mode and from
PEP 440. The **Reproducibility Score is this engine's own internal heuristic** — no
standards body defines a numeric dependency-reproducibility score, and a score of 100.0
is not a compliance statement. Do not cite it as an external requirement.

| Rule | Source | Status |
|---|---|---|
| In hash-checking mode, requirements must be pinned — "either to a URL, filesystem path or using `==`" | [pip, Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/) | Enforced by pip; the install fails otherwise |
| "Hashes are required for _all_ dependencies. If there is a dependency that is not spelled out and hashed in the requirements file, it will result in an error." | [pip, Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/) | Enforced by pip |
| sha256 recommended, "stronger ones are allowed"; **md5, sha1 and sha224 are excluded** "to avoid giving a false sense of security" | [pip, Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/) | Enforced by pip |
| Multiple hashes per requirement, one per distribution archive, joined by backslash continuations | [pip, Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/) | Supported syntax |
| A trailing `.*` on `==` requests **prefix matching**, not exact equality | [PyPA, Version specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/) | PEP 440 semantics |
| `===` is arbitrary equality doing "simple string equality operations"; its use is "heavily discouraged" | [PyPA, Version specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/) | PEP 440 semantics |
| Reproducibility Score $\ge$ some threshold | *(none)* | **This engine's heuristic — not a standard** |

## Reproducible installs are not reproducible builds

reproducible-builds.org defines a reproducible build as: "given the same source code,
build environment and build instructions, any party can recreate bit-by-bit identical
copies of all specified artifacts."

Pinning versions and verifying hashes secures the *inputs* — the same artifacts are
fetched and integrity-checked on every install. That is a **reproducible install**. It does
not by itself produce bit-for-bit identical build outputs, which additionally requires
controlling the build environment and instructions and managing timestamps, timezones,
locales, build paths, file ordering and system randomness.

Source: <https://reproducible-builds.org/docs/definition/>

## Standard lock file format

PEP 751, "A file format to record Python dependencies for installation reproducibility",
reached **Final** status on 2025-03-31 and defines `pylock.toml` (or `pylock.<name>.toml`).
It supersedes the withdrawn PEP 665. It requires at least one secure algorithm from
`hashlib.algorithms_guaranteed`, with SHA-256 recommended, rather than mandating a single
algorithm.

This engine does **not** parse `pylock.toml`; it audits requirements.txt-style lines only.

Source: <https://peps.python.org/pep-0751/>
