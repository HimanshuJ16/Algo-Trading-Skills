# Pre-Flight Checklist — Immutable Trading Bot Deployment

Sign off before the spec is applied to a host that can send orders.

## Identity and provenance

- [ ] Image is referenced by **content digest** (`name@sha256:<64 lowercase hex>`), not by tag.
- [ ] The digest was resolved **once** in CI and passed through unchanged — the deploy job does not re-resolve the tag.
- [ ] `image_tag` is an immutable build tag (`v1.4.2`, `<short-sha>`), not `latest` / `prod` / `main`.
- [ ] `git_commit_sha` is a full Git object id (40 hex SHA-1, or 64 hex SHA-256 object format), not abbreviated.
- [ ] The image carries `org.opencontainers.image.revision`, read back from the manifest and **equal** to the deployed commit.
- [ ] That commit is on a reviewed, merged branch — not a local build from a dirty working tree.

## Signature

- [ ] `cosign verify <image>@<digest>` was run against the **digest**, not the tag.
- [ ] The verification named an expected `--certificate-identity` **and** `--certificate-oidc-issuer` (or the Notation equivalent).
- [ ] `is_image_signed_cosign` is set from that command's exit status — it is **not** hard-coded `true` anywhere in the pipeline.
- [ ] The pipeline no longer depends on Docker Content Trust or Notary v1 (shutting down 8 Dec 2026).

## Immutability at runtime

- [ ] Root filesystem is read-only (`--read-only` / `readOnlyRootFilesystem: true`).
- [ ] **Every** read-write bind mount and volume is enumerated in `writable_volumes`.
- [ ] No read-write mount overlays code, config, or dependency paths — those live in the image.
- [ ] Remaining read-write mounts are genuine state only, on dedicated paths, each with a named owner.
- [ ] Secrets are mounted `:ro` (or delivered as read-only secret mounts), never baked into the image.

## Ephemeral state

- [ ] `/tmp` (and `/run`, if the process needs it) are tmpfs mounts.
- [ ] Every tmpfs carries `noexec,nosuid`.
- [ ] Every tmpfs carries an explicit `size=` limit — an unbounded tmpfs is host RAM shared with co-located bots.
- [ ] The declared `tmpfs_options` match what the runtime is actually given.

## Privilege hardening

- [ ] Container runs as a non-root UID.
- [ ] `no-new-privileges` / `allowPrivilegeEscalation: false` is set.
- [ ] Container is not `privileged` and does not hold `CAP_SYS_ADMIN` (either forces privilege escalation back on).
- [ ] Capabilities are dropped (`--cap-drop ALL` / `capabilities.drop: ["ALL"]`).

## Audit result

- [ ] `report.approved` is True; `report.violations` is empty.
- [ ] Every entry in `report.warnings` has been read and consciously accepted, not skimmed.
- [ ] Any `ImmutableSpecError` was treated as a **failed** deploy, not a skipped check.
- [ ] The full report is written to the deployment record, alongside the digest and commit.

## Operational preconditions

- [ ] Production trading hosts have no interactive SSH path for editing application code.
- [ ] The image has no shell to exec into (distroless / `FROM scratch`), or shell access is audited and alerted.
- [ ] The rebuild → re-sign → redeploy path is fast enough to use during a live incident; if not, that is the item to fix.
- [ ] The incident runbook says: kill switch first, then rebuild. It does **not** contain a step that edits a running container.
