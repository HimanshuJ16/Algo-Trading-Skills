---
name: immutable-infrastructure-for-trading-bots
description: >-
  DevOps engine for auditing and building immutable trading bot container images (Docker, Packer, Cosign signing, read-only rootfs) to eliminate configuration drift and prevent live hot-patching.
domain: Infrastructure & DevOps
subdomain: Immutable Container Deployments & Image Verification
tags: ["immutable-infrastructure", "docker", "packer", "cosign", "container-security", "read-only-rootfs", "no-hot-patching"]
brokers_frameworks: ["Docker", "Packer", "Cosign (Sigstore)", "Kubernetes / Docker Compose", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying live trading bots, market gateways, or execution daemons to cloud servers or Docker/Kubernetes clusters. Live trading bots must NEVER be updated in-place via SSH or hot-patching. Hot-patching introduces configuration drift, untracked code state, and non-reproducible execution bugs. This module enforces an **Immutable Container Infrastructure Model**: building versioned Docker/Packer images, verifying **Cosign cryptographic signatures**, enforcing `--read-only` root filesystems, and routing temporary state to `tmpfs`.

## Prerequisites

- Container deployment spec (`image_name`, `image_tag`, `image_sha256_digest`, `git_commit_sha`, `read_only_rootfs`, `is_image_signed_cosign`, `tmpfs_mounts`).
- Cosign / Notary public key verification configuration.

## Workflow

1. **Read-Only Root Filesystem Enforce Audit**:
   - Audit `read_only_rootfs == True`. Reject mutable container specs allowing runtime file system modifications.
2. **Cosign Cryptographic Signature Verification**:
   - Audit `is_image_signed_cosign == True` and verify SHA256 digest against Git commit SHA.
3. **Ephemeral State Isolation Audit**:
   - Verify `/tmp` and `/run` are mounted via `tmpfs` memory buffers.
4. **Security Hardening Verification**:
   - Audit `no_new_privileges == True` and non-root execution user.
5. **Audit Report Generation**: Output structured `ImmutableInfrastructureReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hot-Patching Running Servers via SSH**: Modifying Python code files directly on live production servers, creating untracked configuration drift.
- **Running Containers with Writable Root Filesystems**: Failing to pass `--read-only` to Docker, allowing malicious scripts or memory leaks to modify container code.
- **Deploying Unsigned Container Images**: Pulling unverified container tags (`latest`) without verifying Cosign cryptographic signatures and SHA256 digests.

## Verification

- Instantiate `ImmutableInfrastructureAuditEngine`. Audit Valid Immutable Spec (`read_only_rootfs=True`, `is_image_signed_cosign=True`, `git_sha="a1b2c3d4..."`, `tmpfs=['/tmp']`) $\implies$ verify engine approves `IMMUTABLE_SPEC_APPROVED`. Audit Violating Spec (`read_only_rootfs=False`, unsigned) $\implies$ verify engine rejects `MUTABLE_ROOTFS_REJECTED` and `UNSIGNED_IMAGE_REJECTED`.
- Run `python scripts/test_immutable_bot_image_builder.py`.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `execution-algorithm-regression-testing-suite`
---
