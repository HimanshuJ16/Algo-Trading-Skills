---
name: immutable-infrastructure-for-trading-bots
description: >-
  Use before a container that can send orders reaches a host, to check the running code
  cannot be edited in place: read-only root filesystem, pinned digest rather than a tag,
  no interactive shell path, and a signature attestation.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, immutable-infrastructure, container-security, docker, kubernetes, cosign-sigstore, supply-chain-integrity
  brokers_frameworks: "Docker; Kubernetes; Cosign (Sigstore); OCI Image Spec"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a container spec is about to be applied to a host that can send
orders, and you need to know whether the code inside it can still be altered after
deployment. The failure it prevents is specific: an operator SSHes into a production
node during an incident, edits `strategy.py` in place, and the fix survives until the
next restart silently reverts it — or does not survive, and nobody knows which build is
actually running when the P&L diverges.

NIST SP 800-190 states the principle directly: containers "should be operated as
stateless entities that are deployed but not changed", and when one needs updating it
"is simply destroyed and replaced with a new container that has the updates." This skill
audits whether your spec actually delivers that, or only claims to.

Use it in two places: as a CI/CD gate before an artifact is promoted, and again in the
deploy job on the host, against the spec that will really be applied.

## When NOT to Use

- **You want the signature actually verified.** This engine reads a boolean your
  pipeline sets. It runs no registry call and no cryptography. The real check is
  `cosign verify <image>@<digest> --certificate-identity=... --certificate-oidc-issuer=...`,
  and this audit is downstream of it, not a substitute for it.
- **You need admission control that cannot be bypassed.** A Python function in your
  deploy job is advisory — anyone with cluster access can `kubectl run` around it.
  Enforce at the admission layer (Sigstore policy-controller, Kyverno, Gatekeeper) and
  use this to fail fast and explain why, before the cluster refuses the manifest.
- **You are comparing two environments' configuration.** This audits one spec against
  fixed immutability policy; it does not diff DEV against PROD. See
  `configuration-drift-detection-across-environments`.
- **You are sequencing the rollout itself.** Whether to shift traffic gradually, and how
  to roll back, belongs to `blue-green-deployment-for-live-strategy-updates` and
  `canary-releases-for-strategy-code-changes`. This skill only decides whether the
  artifact is fit to be rolled out at all.
- **The build is not reproducible in the first place.** Pinning a digest freezes
  whatever went into that build, including an unpinned transitive dependency resolved on
  build day. See `dependency-pinning-and-reproducible-builds`.

## Prerequisites

- A deployment spec you can read before it is applied — Compose service, Kubernetes
  `PodSpec`, or the arguments your deploy script passes to `docker run`.
- The image **content digest**, resolved once in CI (`docker buildx imagetools inspect`,
  or the `digest` output of `docker/build-push-action`) and threaded through unchanged.
- A signing and verification step already in the pipeline. Cosign (Sigstore) or Notation
  (Notary Project) are the current options; **Docker Content Trust and Notary v1 are
  not** — the upstream Notary v1 server "is no longer maintained" and Docker has
  scheduled full shutdown of `notary.docker.io` for 8 December 2026.
- Optionally, the image's `org.opencontainers.image.revision` annotation read back from
  the manifest, so the commit you claim to have deployed is a property of the artifact
  rather than a string your pipeline typed into a report.

## Workflow

1. **Pin to a digest, and treat the tag as a label.**
   - `ImmutableContainerSpec` requires `image_sha256_digest`; the audit validates it
     against the OCI descriptor grammar (`sha256:` plus exactly 64 lowercase hex).
     A malformed or absent digest is `UNPINNED_DIGEST_REJECTED`.
   - Kubernetes documents why: "Tags can be moved to point to different images, but
     digests are fixed", and deploying by tag means "you might end up with a mix of Pods
     running the old and new code."
   - A floating tag name (`latest`, `prod`, `main`, …) additionally raises
     `MUTABLE_TAG_REJECTED`. This is hygiene, not the control — the digest is the control.
     Override `mutable_tag_names` if your convention differs.

2. **Bind the artifact to a reviewed commit — do not assert it.**
   - `git_commit_sha` must be a full Git object id: 40 hex under SHA-1, or 64 hex under
     the SHA-256 object format. Abbreviated ids are rejected because a deployment record
     must name exactly one commit.
   - Set `source_revision_annotation` from the image's own
     `org.opencontainers.image.revision` annotation ("source control revision identifier
     for the packaged software"). A mismatch is `SOURCE_REVISION_MISMATCH_REJECTED` —
     the artifact was not built from the commit under review.
   - With no annotation available the audit **warns** and proceeds, because `git_commit_sha`
     is then unverified pipeline metadata. Once your builder sets the label, construct the
     engine with `require_source_revision_annotation=True` to turn that warning into a gate.
   - There is no cryptographic check of a digest "against" a Git SHA — those are hashes of
     different things. The annotation, and the provenance attestation behind it, is what
     actually links them.

3. **Close the rootfs, then close the mounts that reopen it.**
   - `read_only_rootfs=False` is `MUTABLE_ROOTFS_REJECTED`.
   - **A read-only rootfs alone is not enough.** Docker's own reference says `--read-only`
     prohibits "writes to locations other than the specified volumes". A single
     `-v /opt/bot:/app` restores in-place editing completely, with the read-only flag
     still set and still reported as set. List every read-write bind mount and volume in
     `writable_volumes`; a non-empty list is `WRITABLE_HOST_MOUNT_REJECTED`. NIST SP
     800-190: "Very rarely should containers mount local file systems on a host."

4. **Record signature verification as an attestation, and label it as one.**
   - `is_image_signed_cosign=False` is `UNSIGNED_IMAGE_REJECTED`. The report field is
     named `is_signature_attested`, not `..._verified`, because that is all this engine
     can honestly claim.
   - Set the flag from the exit status of a real `cosign verify` that named an expected
     identity and issuer. Cosign 2.0 made those flags mandatory: "it's critical to
     specify who you trust to generate a signature for identity-based signing." A
     pipeline that hard-codes `True` has built a gate that always opens.

5. **Harden privileges, or the read-only flag is decorative.**
   - `no_new_privileges` and `run_as_non_root_user` must both hold, or
     `PRIVILEGE_HARDENING_REJECTED`. A root process able to escalate can remount the
     rootfs read-write.
   - On Kubernetes this is `allowPrivilegeEscalation: false` plus `runAsNonRoot: true`.
     Note that Kubernetes forces `allowPrivilegeEscalation` to true for a privileged
     container or one holding `CAP_SYS_ADMIN`, so setting the field is not sufficient if
     those are also present.

6. **Give the process scratch space it cannot execute from.**
   - A read-only rootfs leaves nowhere to write. Declare `tmpfs_mounts` (`/tmp`, `/run`);
     a missing required path is a **warning**, because it crashes the bot at first write
     rather than weakening immutability.
   - Declare the options per mount in `tmpfs_options`. A declared mount missing `noexec`
     or `nosuid` is `EXECUTABLE_TMPFS_REJECTED` — writable *and* executable scratch space
     is somewhere to drop and run a payload despite the read-only rootfs. A mount with no
     declared options warns instead of failing: the engine cannot see the real runtime,
     and unknown is not the same as unsafe.

7. **Act on the whole report, not the first line.**
   - Branch on `report.approved`. `report.violations` holds every breach, ordered most
     severe first, each with a `severity` and a `remediation` naming the exact flag.
     Print all of them — a gate that reveals one violation per rebuild burns a
     deployment window per finding.
   - `report.status` is the code of that first, most severe violation, so it is safe to
     alert on: it never reports a `HIGH` while a `CRITICAL` breach is further down the
     list. Log `report.violation_codes` for the full picture.
   - A malformed spec raises `ImmutableSpecError` rather than returning a report. Catch it
     as a **failure**, never as a pass: an unevaluable spec is not an approved spec.

> Full step-by-step procedure and CI/CD wiring: see `references/workflows.md`.
> Control-by-control sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bind-mounting the code directory into a `--read-only` container.** The most common
  way an immutable deployment turns out to be mutable. The flag is set, the audit field
  reads True, and `vi /opt/bot/strategy.py` on the host still edits live code. Only the
  `writable_volumes` check catches this.
- **Hard-coding `is_image_signed_cosign=True`.** A signature field that is never derived
  from a verification exit status is a comment, not a control. Equally: running
  `cosign verify` without `--certificate-identity` and `--certificate-oidc-issuer`, which
  Cosign 2.0 refuses in keyless mode for exactly this reason.
- **Verifying a tag and then deploying that tag.** Verification resolves the tag to a
  digest at that instant; the pull resolves it again later. Verify and deploy the same
  digest string.
- **Treating "no digest" as "digest not required".** An empty or malformed digest field
  previously produced an image URI ending in a bare `@` and an approved report. Absence
  of a pin is a rejection, not a default.
- **Believing a read-only rootfs stops privilege escalation.** A root process with
  `CAP_SYS_ADMIN` can remount it. Read-only, non-root, and no-new-privileges are one
  control in three parts.
- **Mounting `/tmp` as tmpfs and stopping there.** Without `noexec,nosuid` you have
  replaced a read-only filesystem with a writable, executable one and called it hardening.
- **Assuming `latest` is pinned because it was pulled once.** NIST SP 800-190 warns the
  tag "is only a label attached to the image and not a guarantee of freshness."
- **Passing a YAML-loaded spec straight in.** `read_only_rootfs: "false"` parses as a
  truthy string. The spec now rejects non-`bool` values rather than approving them.
- **Patching the container instead of the image.** Any fix applied to a running trading
  container is lost on restart and invisible to the next audit. Rebuild, re-sign,
  redeploy — even for a one-line hotfix, and especially during an incident.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/immutable-infrastructure-for-trading-bots/scripts` — all tests must pass.
- Audit a fully compliant spec (digest-pinned, `read_only_rootfs=True`,
  `is_image_signed_cosign=True`, `writable_volumes=[]`, `tmpfs_options` carrying
  `noexec,nosuid`, annotation matching the commit) and confirm
  `status == "IMMUTABLE_SPEC_APPROVED"` with both `violations` and `warnings` empty.
- Audit a spec that is both mutable and unsigned and confirm `violation_codes` contains
  **both** `MUTABLE_ROOTFS_REJECTED` and `UNSIGNED_IMAGE_REJECTED` from one call.
- Combine a `HIGH` breach evaluated early (an invalid `git_commit_sha`) with a `CRITICAL`
  one evaluated later (`writable_volumes=["/app"]`) and confirm `status` reports the
  `CRITICAL` code, not the first one checked.
- Take an approved spec, add `writable_volumes=["/app"]`, and confirm it flips to
  `WRITABLE_HOST_MOUNT_REJECTED` while `is_read_only_rootfs_enforced` stays True — this is
  the case a rootfs-only audit misses.
- Set `tmpfs_options={"/tmp": ["rw", "size=64m"]}` and confirm `EXECUTABLE_TMPFS_REJECTED`;
  remove the key entirely and confirm a warning and approval instead.
- Pass `read_only_rootfs="false"` and confirm `ImmutableSpecError` is raised at
  construction rather than a report being returned.
- Against your real pipeline: change the deployed tag to point at a different image,
  re-resolve the digest, and confirm the audit input changes. If it does not, your deploy
  path is not digest-pinned regardless of what the spec says.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `canary-releases-for-strategy-code-changes`
- `configuration-drift-detection-across-environments`
- `dependency-pinning-and-reproducible-builds`
- `infrastructure-as-code-for-trading-hosts`
- `secrets-rotation-without-bot-downtime`
