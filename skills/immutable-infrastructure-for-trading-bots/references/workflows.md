# Workflows for Immutable Infrastructure Audit

Deep procedure for wiring the audit into a trading-bot deployment path. `SKILL.md` has
the decision points; this file has the mechanics and the failure handling.

## 0. Where the gate belongs

Run the audit **twice**, on two different inputs:

1. **In CI, before promotion.** Input: the spec template plus the digest just produced by
   the build. Catches the mistake early and cheaply.
2. **In the deploy job, on the host.** Input: the fully rendered spec that will actually be
   applied. This is the run that protects capital, because it sees the values after
   templating, environment substitution, and any operator override.

Neither is admission control. If the platform supports it, also enforce signature and
digest policy at the cluster admission layer, where a human with `kubectl` cannot skip it.

## 1. Resolve the digest exactly once

Everything downstream depends on a single digest string flowing unchanged from build to
deploy.

```bash
# In the build job, after push:
DIGEST=$(docker buildx imagetools inspect "$IMAGE:$TAG" --format '{{.Manifest.Digest}}')
GIT_SHA=$(git rev-parse HEAD)
```

Emit `DIGEST` and `GIT_SHA` as job outputs. Do not re-resolve the tag in the deploy job:
re-resolution is the bug, not a safety net. Between the two resolutions someone can move
the tag, and the deploy will run content that was never audited.

Stamp provenance at build time so the commit is a property of the artifact:

```bash
docker buildx build \
  --label "org.opencontainers.image.revision=$GIT_SHA" \
  --label "org.opencontainers.image.source=https://github.com/<org>/<repo>" \
  --push -t "$IMAGE:$TAG" .
```

Read it back in the deploy job and pass it to the audit as `source_revision_annotation`.
If the value you read back does not match `GIT_SHA`, the artifact was not built from the
commit under review — that is `SOURCE_REVISION_MISMATCH_REJECTED`, and it usually means a
tag was re-pointed at an older image.

## 2. Verify the signature for real, then attest the result

```bash
if cosign verify "$IMAGE@$DIGEST" \
      --certificate-identity="$EXPECTED_SIGNER" \
      --certificate-oidc-issuer="$EXPECTED_ISSUER" > /dev/null; then
  SIGNED=true
else
  SIGNED=false
fi
```

Two rules:

- **Verify the digest, not the tag.** Verifying `$IMAGE:$TAG` and then pulling `$IMAGE:$TAG`
  resolves the reference twice; the signature you checked is not necessarily bound to the
  bytes you run.
- **Never hard-code `SIGNED=true`.** The audit trusts this flag completely. A pipeline that
  sets it unconditionally has a gate that always opens, which is worse than no gate because
  it produces a passing report.

Notation is an equivalent choice (`notation verify "$IMAGE@$DIGEST"`); set the same flag
from its exit status.

## 3. Enumerate the read-write mounts honestly

This is the step most audits get wrong. `--read-only` covers the rootfs and nothing else,
so every read-write bind mount or volume is an exception to the immutability guarantee.

Walk the spec and list every mount that is not `:ro`:

- Compose: entries under `volumes:` without the `:ro` suffix, and `read_only: false` on a
  long-form mount.
- Kubernetes: `volumeMounts` entries without `readOnly: true`.
- `docker run`: every `-v` / `--mount` without `readonly`.

Put the container-side path of each into `writable_volumes`. Then remove the ones that
should not exist:

- **Code, config, and dependency paths belong in the image.** A `-v ./src:/app` is a
  development convenience that reinstates hot-patching in production.
- **Genuine state** — a SQLite journal, a position cache — is legitimate, but it should be
  a named volume on a dedicated path, never a mount that overlays code. If it must stay,
  it is a documented, signed-off exception; record it, do not silence the check.
- **Secrets** should be mounted `:ro`, and are then invisible to this check by construction.

## 4. Choose tmpfs options deliberately

A read-only rootfs leaves the process nowhere to write. Give it tmpfs, and give it tmpfs
that cannot be executed from:

```bash
docker run --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /run:rw,noexec,nosuid,size=16m \
  --security-opt no-new-privileges \
  --user 10001:10001 \
  "$IMAGE@$DIGEST"
```

Size every tmpfs. An unbounded tmpfs is host RAM: a log loop or a runaway temp-file writer
in a strategy can exhaust the node and take down every co-located bot, which is a
correlated outage across strategies that were supposed to be independent.

Mirror the same options into `tmpfs_options` so the audit can check them. Leave a path out
of `tmpfs_options` only when you genuinely do not know its options — the audit will warn
rather than assert the mount is safe.

## 5. Harden privileges as one control

`--read-only`, non-root, and `no-new-privileges` are three parts of one control, not three
independent nice-to-haves. A root process holding `CAP_SYS_ADMIN` can remount the rootfs
read-write, at which point the first setting means nothing.

Kubernetes equivalent:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
```

Setting `allowPrivilegeEscalation: false` is not sufficient on its own: Kubernetes forces
it true for a privileged container and for one holding `CAP_SYS_ADMIN`, so those must be
absent too. Dropping all capabilities is the reliable way to be sure.

## 6. Run the audit and act on the whole report

```python
import logging
from immutable_bot_image_builder import (
    ImmutableContainerSpec, ImmutableInfrastructureAuditEngine, ImmutableSpecError,
)

logging.basicConfig(level=logging.INFO)
engine = ImmutableInfrastructureAuditEngine(
    required_tmpfs_paths=("/tmp", "/run"),
    require_source_revision_annotation=True,   # once the builder sets the label
)

try:
    report = engine.audit_container_spec(ImmutableContainerSpec(...))
except ImmutableSpecError as exc:
    # Unevaluable is not approved. Fail the deploy.
    raise SystemExit(f"immutability audit could not run: {exc}")

for warning in report.warnings:
    print(f"WARN  {warning}")

if not report.approved:
    for v in report.violations:
        print(f"{v.severity:8} {v.code}\n         {v.detail}\n         fix: {v.remediation}")
    raise SystemExit(1)
```

Print **every** violation. The engine evaluates all controls on every call precisely so an
operator does not discover the second problem only after rebuilding to fix the first —
during a market session that costs a deployment window per finding.

`report.status` holds the first violation code in evaluation order and is stable for a
given spec, which makes it safe to alert on. `report.violation_codes` is what you log.

## 7. Incident handling: the rule that gets broken

When a strategy is misbehaving at 14:30 and the fix is one line, the temptation is to edit
the file in the running container. Do not.

- A change applied to a running container is lost on restart, invisible to the next audit,
  and absent from every record of what was deployed. Post-incident reconstruction of what
  the bot was actually running becomes guesswork.
- The correct emergency path is **stop trading first, then rebuild**: hit the kill switch
  (`kill-switch-and-drawdown-circuit-breakers`), flatten or hold as the runbook requires,
  then rebuild, re-sign, re-audit, redeploy. Losing a session is recoverable; not knowing
  what code held a position is not.
- If your rebuild-to-deploy path is too slow to be usable under incident pressure, that is
  the defect to fix. An immutability policy that is impractical during an incident is a
  policy that will be bypassed during an incident.

Make it enforceable, not just documented: no interactive shell on production trading
hosts, no writable code mounts, and images built `FROM scratch` or a distroless base so
there is no shell in the container to exec into in the first place.
