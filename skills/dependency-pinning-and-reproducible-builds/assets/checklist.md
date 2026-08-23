# Pre-Flight Checklist

- [ ] Are all top-level **and transitive** dependencies present, each pinned with an exact `==` version?
- [ ] Has the lockfile been produced by a resolver (`pip-compile --generate-hashes` / `uv pip compile --generate-hashes`) rather than hand-edited?
- [ ] Are there any `==X.Y.*` specifiers, which are PEP 440 prefix matches and not pins?
- [ ] Does every requirement carry at least one `--hash`, with all archives for the platforms you install on covered?
- [ ] Are all hash algorithms strong (`sha256` or better), with no `md5`, `sha1` or `sha224`?
- [ ] Is every digest hex and full length (64 characters for sha256), not a placeholder?
- [ ] Has any hash in the file been copied from a generator that could have synthesised it, rather than taken from the real artifact?
- [ ] Does CI actually install with `pip install --require-hashes`, so a hash mismatch fails the build?
- [ ] Is the Python runtime version pinned in CI/CD, and is the base image pinned by digest (so `openblas`/`libgomp`/CUDA are pinned too)?
- [ ] Is the lockfile committed to version control alongside the code it locks?
- [ ] Are direct URL/path references (`pkg @ https://...`) pointing at immutable artifacts?
- [ ] Is it understood that passing this audit shows a reproducible *install*, not a reproducible *build*?
