# Standards for Immutable Trading Bot Deployments

| Metric | Engineering Standard |
|---|---|
| Read-Only Root Filesystem | Live trading containers MUST run with `--read-only` rootfs. |
| Image Signature | Images MUST be cryptographically signed via Cosign/Sigstore before deployment. |
| In-Place Modification | In-place SSH hot-patching of code on running servers is STRICTLY PROHIBITED. |
