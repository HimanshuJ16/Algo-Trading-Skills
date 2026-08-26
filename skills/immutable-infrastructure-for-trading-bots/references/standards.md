# Standards for Immutable Trading Bot Deployments

Every control the audit engine enforces, with the source it rests on. Where a source
could not be verified to a primary document, that is stated rather than implied.

## Control-to-source map

| Control (violation code) | Standard / source | What it actually says |
|---|---|---|
| `UNPINNED_DIGEST_REJECTED` | OCI Image Spec, *Descriptor* — digest grammar | For `sha256` "the _encoded_ portion MUST match `/[a-f0-9]{64}/`"; for `sha512`, `/[a-f0-9]{128}/`. Lowercase only. |
| `UNPINNED_DIGEST_REJECTED` | Kubernetes, *Images* | "Digests are hashes of the image's content, and are immutable." Deploying by tag means "you might end up with a mix of Pods running the old and new code." |
| `MUTABLE_TAG_REJECTED` | NIST SP 800-190 §4.2.2 | A `latest` tag "is only a label attached to the image and not a guarantee of freshness"; organizations "should be cautious to not overly trust it." |
| `INVALID_GIT_SHA_REJECTED` | Git, *hash-function-transition* | "Objects can be named by their 40 hexadecimal digit SHA-1 name or 64 hexadecimal digit SHA-256 name." Both are accepted; abbreviations are not. |
| `SOURCE_REVISION_MISMATCH_REJECTED` | OCI Image Spec, *Annotations* | `org.opencontainers.image.revision` is the "Source control revision identifier for the packaged software." |
| `MUTABLE_ROOTFS_REJECTED` | Docker CLI reference, `docker container run` | `--read-only`: "Mount the container's root filesystem as read only prohibiting writes to locations other than the specified volumes for the container." |
| `MUTABLE_ROOTFS_REJECTED` | NIST SP 800-190 §2.1 | Containers "should be operated as stateless entities that are deployed but not changed"; when one needs changing it "is simply destroyed and replaced with a new container that has the updates." |
| `WRITABLE_HOST_MOUNT_REJECTED` | Docker CLI reference (same clause as above) | The `--read-only` guarantee explicitly excludes "the specified volumes" — a read-write mount is outside the control. |
| `WRITABLE_HOST_MOUNT_REJECTED` | NIST SP 800-190 §4.5.5 | "Very rarely should containers mount local file systems on a host." Organizations "should use tools that can monitor what directories are being mounted by containers and prevent the deployment of containers that violate these policies." |
| `UNSIGNED_IMAGE_REJECTED` | NIST SP 800-190 §4.1.5 | Calls for "Discrete identification of each image by cryptographic signature" and "Validation of image signatures before image execution to ensure images are from trusted sources and have not been tampered with." |
| `UNSIGNED_IMAGE_REJECTED` | Sigstore, *Cosign 2.0 Released* | "Verification now requires identity flags, `--certificate-identity` and `--certificate-oidc-issuer`. Like verifying a signature with a public key, it's critical to specify who you trust to generate a signature for identity-based signing." |
| `PRIVILEGE_HARDENING_REJECTED` | Kubernetes, *Configure a Security Context* | `allowPrivilegeEscalation` "directly controls whether the `no_new_privs` flag gets set on the container process", and is "always true when the container is run as privileged, or has `CAP_SYS_ADMIN`." |
| `PRIVILEGE_HARDENING_REJECTED` | Docker CLI reference | `--security-opt no-new-privileges` — "Disable container processes from gaining new privileges"; "commands that raise privileges such as `su` or `sudo` no longer work." |
| `EXECUTABLE_TMPFS_REJECTED` | Docker CLI reference, `--tmpfs` | Accepts `rw`, `noexec`, `nosuid`, `nodev`, `size`; "The options that you can pass to `--tmpfs` are identical to the Linux `mount -t tmpfs -o` command." |

## Signing toolchain currency

Docker Content Trust and the Notary v1 server are being retired, and this skill does not
reference them as a live option:

- Docker: "DCT relies on the upstream Notary v1 server, the original TUF-based
  implementation that was first released in 2015, and the project is no longer maintained."
- Full shutdown of `notary.docker.io` is scheduled for **8 December 2026**, following read
  brownouts in August 2026.
- Docker's stated migration targets are **Sigstore / Cosign** (identity-based, short-lived
  certificates tied to an OIDC identity, signatures stored as OCI artifacts in the same
  registry) and **Notation** (the Notary Project CLI, certificate-based PKI model). Either
  satisfies the `is_image_signed_cosign` attestation; the field name reflects the more
  common choice in this domain, not an exclusive requirement.

## What this engine does not establish

State these limits wherever an audit report is presented as evidence:

- **No verification is performed.** `is_image_signed_cosign` and
  `source_revision_annotation` are attestations recorded by the pipeline. The report field
  is deliberately named `is_signature_attested`.
- **No registry, network, or runtime inspection.** The audit reads a declared spec. A spec
  that passes and a container that was actually launched with those flags are two different
  claims; only admission control on the cluster closes that gap.
- **Undeclared is not proven safe.** A tmpfs mount with no declared options, or an absent
  `org.opencontainers.image.revision`, produces a warning rather than a pass, and the
  distinction is preserved in `report.warnings`.

## Regulatory context

Immutable, version-controlled deployment supports the change-control and record-keeping
expectations that apply to firms running trading algorithms, but the specific obligations
are jurisdictional and are not restated here. In the EU/UK, controlled deployment and
testing of trading algorithms is addressed by Commission Delegated Regulation (EU)
2017/589 (RTS 6); the article numbering and current applicable text should be read from
the regulation itself rather than inferred from this document. For jurisdiction-specific
requirements see `mifid-ii-algo-trading-compliance-eu`,
`uk-fca-algorithmic-trading-systems-controls`, and `sec-rule-15c3-5-risk-controls-us`.

## Sources

- OCI Image Format Specification — Descriptor: https://github.com/opencontainers/image-spec/blob/main/descriptor.md
- OCI Image Format Specification — Annotations: https://github.com/opencontainers/image-spec/blob/main/annotations.md
- Docker CLI reference, `docker container run`: https://docs.docker.com/reference/cli/docker/container/run/
- Docker blog, *Docker Content Trust: Retirement and Migration Guidance*: https://www.docker.com/blog/docker-content-trust-retirement-and-migration-guidance/
- Sigstore blog, *Cosign 2.0 Released!*: https://blog.sigstore.dev/cosign-2-0-released/
- Sigstore docs, *Verifying signatures*: https://docs.sigstore.dev/cosign/verifying/verify/
- Kubernetes, *Images*: https://kubernetes.io/docs/concepts/containers/images/
- Kubernetes, *Configure a Security Context for a Pod or Container*: https://kubernetes.io/docs/tasks/configure-pod-container/security-context/
- NIST SP 800-190, *Application Container Security Guide*: https://doi.org/10.6028/NIST.SP.800-190
- Git, *hash-function-transition*: https://git-scm.com/docs/hash-function-transition
