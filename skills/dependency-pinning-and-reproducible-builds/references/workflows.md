# Workflows for Dependency Pinning and Reproducible Builds

1. **Engine Configuration**:
   - Set `target_python_version` (recorded in the lockfile header) and the score weights
     `pin_weight` / `hash_weight`, which must be non-negative and sum to 1.0.
2. **Line Reassembly**:
   - Join backslash continuations into logical requirements *before* parsing, so a
     multi-line `pip-compile --generate-hashes` entry is read as one package.
3. **Directive Filtering**:
   - Skip pip options (`-r`, `-c`, `-e`, `-f`, `-i`, `--index-url`, `--extra-index-url`,
     `--require-hashes`, ...) and record them in `skipped_directive_lines`.
   - A line consisting only of `--hash=` options is malformed (an orphan continuation);
     record it as a warning rather than scoring it as a nameless package.
4. **Specifier Classification**:
   - Strip environment markers (`; ...`) and extras (`[...]`) before reading the version.
   - Exact pin: `==X.Y.Z`, `===X.Y.Z` (warn: discouraged), or `@ <url>` (warn: only an
     immutable URL pins anything).
   - Not a pin: `==X.Y.*` (PEP 440 prefix matching), `~=`, `>=`, `<=`, `<`, `>`, `!=`,
     or a bare package name.
5. **Hash Validation**:
   - Extract every `--hash=<algo>:<digest>` on the requirement.
   - Accept only strong algorithms (`sha256`, `sha384`, `sha512`, `sha3_*`, `blake2*`);
     reject `md5`, `sha1`, `sha224`.
   - Require the digest to be hex of the algorithm's full length.
   - A requirement counts as hashed only if at least one hash passes both checks.
6. **Proportional Scoring**:
   - $\text{Score} = 100 \times \left(w_{\text{pin}} \frac{N_{\text{pinned}}}{N} + w_{\text{hash}} \frac{N_{\text{hashed}}}{N}\right)$.
   - `all_requirements_pinned_and_hashed` is the strict boolean: every audited requirement
     both exactly pinned and validly hashed. It does **not** assert transitive completeness.
7. **Remediation Drafting**:
   - Pass compliant requirements through verbatim, preserving all of their hashes.
   - Emit deficient ones as commented `# TODO(unpinned:)` / `# TODO(missing-hash:)` lines.
   - Never synthesise a version or a digest. Resolve with `pip-compile --generate-hashes`
     (or `uv pip compile --generate-hashes`), and obtain individual digests with `pip hash`.
