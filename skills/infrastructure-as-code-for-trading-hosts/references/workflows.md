# Workflows for IaC Trading Host Management

## 1. Spec ingestion and structural validation

Ingest the proposed `TradingHostSpec`. Run `validate_spec()` first: every string field is
interpolated into a playbook that runs as root and into Terraform that provisions real
infrastructure, so a `host_name` that is not an RFC 1123 label, a governor containing shell
metacharacters, or a cpulist carrying extra kernel arguments is a hard rejection
(`REJECTED_INVALID_SPEC`), never a sanitised pass-through. `generate_terraform_hcl()` and
`generate_ansible_playbook()` raise `SpecValidationError` if called directly with such a spec.

## 2. Policy audit

Five gates, all of which must pass:

| Gate | Rejection status |
|---|---|
| Governor is `performance` | `REJECTED_POWERSAVE_GOVERNOR` |
| C-states requested disabled | `REJECTED_CSTATES_ENABLED` |
| `rmem_max` and `wmem_max` ≥ policy | `REJECTED_SMALL_BUFFERS` |
| Isolated CPU set valid, excludes CPU 0, exists on the host | `REJECTED_NO_CPU_ISOLATION` |
| PTP enabled with daemon units named | `REJECTED_PTP_DISABLED` |

`status` carries the highest-priority failure; `violations` carries all of them so a spec can
be corrected in one pass. `warnings` carries advisory findings that do not block: a non-metal
`instance_type` (the platform may be unable to honour the C-state/governor request at all) and
the real amount of memory a default-size hugepage request reserves.

## 3. Kernel command-line construction

`kernel_command_line_args()` renders:

- `isolcpus=<list> nohz_full=<list> rcu_nocbs=<list>` — RCU callback offload accompanies
  full-dynticks isolation.
- `intel_idle.max_cstate=0 processor.max_cstate=1` when C-states are to be disabled. Both are
  required: `processor.*` is inert while `intel_idle` is loaded, and acpi_idle clamps `0` to
  `1`. C1/POLL remain available — say so rather than claiming "C-states off".
- `default_hugepagesz=/hugepagesz=/hugepages=` when `hugepage_size_kb` is not the 2 MiB
  default, because gigantic pages must be reserved at boot.

## 4. Ansible playbook generation

The playbook, in order:

1. Sets the governor with `cpupower`, then installs a systemd oneshot unit so it survives the
   reboot that the kernel arguments require.
2. Stages kernel arguments — `grubby --update-kernel=ALL` on the RHEL family, a `backrefs`
   `lineinfile` **append** on the Debian family. The append is guarded by a negative lookahead
   so re-runs do not duplicate arguments, and it never rewrites the whole
   `GRUB_CMDLINE_LINUX_DEFAULT` line.
3. Runs `update-grub` on the Debian family when the defaults file changed.
4. Applies sysctl buffer values with `sysctl_set: true`, so the running kernel is verified and
   not just the file.
5. Enables the PTP units — `ptp4l` **and** `phc2sys`.
6. Reads `/proc/cmdline` and asserts the isolation arguments are live.

The playbook does not reboot. A trading host may be holding positions; scheduling the reboot is
an operator decision, and the assertion in step 6 is what prevents "staged but inert" from
passing as success.

## 5. Terraform generation

A `trading_host_ami` variable (never a baked-in AMI ID), the spec's `instance_type`, and
`lifecycle { prevent_destroy = true }` so an AMI bump becomes a plan error rather than a
destroyed live execution gateway.

## 6. Report

`IacTradingHostReport` carries per-check booleans, `status`, `violations`, `warnings`,
`audit_notes` built from the spec's actual values, and the artifacts — empty strings whenever
the spec was rejected.

## 7. Post-provisioning verification (on the host)

- `cat /proc/cmdline` — the arguments are live only after a reboot.
- `cpupower frequency-info` and `cat /sys/devices/system/cpu/cpu*/cpuidle/state*/disable`.
- `sysctl net.core.rmem_max` sets the ceiling; confirm the feed handler actually requests it —
  the kernel doubles what `setsockopt(SO_RCVBUF)` asks for, and a process that never calls it
  keeps the default. Check drops in `netstat -su` / `/proc/net/udp`, not in `sysctl` output.
- `pmc`/`ptp4l` telemetry and `phc2sys` offsets — hand off to
  `clock-synchronization-ptp-for-trading-hosts`.
