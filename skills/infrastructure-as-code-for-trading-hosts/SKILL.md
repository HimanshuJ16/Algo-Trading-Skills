---
name: infrastructure-as-code-for-trading-hosts
description: >-
  Use when a co-located or bare-metal trading host is provisioned as code, to validate a
  CPU isolation, C-state, socket buffer and PTP specification before rendering it into
  Terraform and Ansible. It audits a spec, not a live machine.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: iac, terraform, ansible, low-latency, cpu-isolation, isolcpus, c-states, ptp4l, sysctl
  brokers_frameworks: "Terraform HCL; Ansible Playbooks; linuxptp (ptp4l / phc2sys); AWS EC2 bare metal; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a co-located bare-metal server (Equinix NY4/LD4/TY3) or a bare-metal cloud
instance is being provisioned **as code** for order entry, feed handling, or execution, and the
kernel/OS tuning must be reviewable, reproducible, and provably applied.

`IacTradingHostManagerEngine` in `scripts/` takes a `TradingHostSpec`, audits it against site
policy, and — only if every check passes — renders a Terraform resource block and an Ansible
playbook. It fails closed: a rejected spec produces no artifacts at all.

The default OS configuration is wrong for this workload in specific, measurable ways. Deep
C-states cost real wake-up latency (the `intel_idle` driver advertises a **133 µs** C6 exit
latency on Skylake-SP and Haswell, and **290 µs** on Sapphire Rapids), the default frequency
governor throttles between bursts, and default socket buffer ceilings cap what a multicast feed
handler is allowed to request.

## When NOT to Use

- **As proof that a host is tuned.** This module audits a *proposed spec* and renders code. It
  reads nothing from the target machine. Approval means "this specification is coherent and
  compliant with policy", never "the host is running this way". The generated playbook's
  `/proc/cmdline` assertion is what closes that gap, on the host, after a reboot.
- **On a virtualized or shared-tenancy instance, without checking the platform first.** AWS
  documents OS control of C-states and P-states for **all Intel/AMD bare metal instances** plus
  an enumerated list of large instance types; **Graviton exposes neither** — those processors
  "have built-in power saving modes and operate at a fixed frequency". Applying this spec to a
  Graviton or small shared instance yields a green audit and no effect. The engine emits a
  warning for any non-`.metal` instance type; treat it as a blocking question, not noise.
- **As the time-synchronization design.** This skill enables the daemons. Choosing the PTP
  profile, transport, and domain, and evidencing the resulting offset, belongs to
  `clock-synchronization-ptp-for-trading-hosts`.
- **To reconfigure a host that is holding positions.** Every kernel argument here needs a
  reboot. The generated playbook deliberately does *not* reboot; rebooting an execution gateway
  is a scheduled, out-of-session operator decision.
- **As a drift detector.** It renders desired state. Comparing desired against observed state
  across environments is `configuration-drift-detection-across-environments`.

## Prerequisites

- A host specification: `host_name` (RFC 1123 label), `cpu_governor`, `isolated_cpu_cores`
  (kernel cpulist), `disable_cpu_cstates`, `net_rmem_max_bytes`, `net_wmem_max_bytes`,
  `enable_ptp_clock_sync`. Optionally `total_cpu_count`, `instance_type`, `hugepages_count` /
  `hugepage_size_kb`, and `ptp_service_units`.
- **Your** socket-buffer policy. The 128 MiB default is a house threshold, not a published
  standard — size it from burst rate × tolerable drain stall and pass it to the engine
  constructor. See `references/standards.md`.
- Confirmation that the target platform can honour the spec (bare metal, or an instance type
  AWS lists as supporting C-state control).
- The PTP grandmaster's profile, and the correct `linuxptp` unit names for the target distro —
  RHEL ships `ptp4l.service`/`phc2sys.service`; Debian/Ubuntu ship template units requiring
  `@<interface>`.
- Ansible collections `ansible.posix` (for `sysctl`) on the control node.

## Workflow

1. **Ingest the spec and validate it structurally before anything else.** `validate_spec()`
   rejects any value that cannot be safely interpolated: a `host_name` that is not an RFC 1123
   label, a governor carrying shell metacharacters, a cpulist carrying extra kernel arguments
   (`"2-15 init=/bin/sh"`), negative sizes. These strings end up inside a playbook that runs as
   root; a malformed one is a rejection, never an escape-and-continue.

2. **Audit the isolated CPU set as a set, not as a string.** An empty cpulist previously passed
   a truthiness check and emitted a bare `isolcpus=` — audit the parsed set instead. Reject
   CPU 0: `tick_nohz_init()` clears the boot CPU from the `nohz_full` mask
   (`"NO_HZ: Clearing %d from nohz_full range for timekeeping"`) and the timekeeping CPU must
   remain online, so isolating it is silently undone at best. When `total_cpu_count` is known,
   reject indices that do not exist on the host.

3. **Audit policy: governor, C-states, buffers, PTP.** All five checks gate approval, and every
   violation is recorded in `report.violations` — `status` is only the highest-priority one.
   Fix the spec in one pass rather than re-running to discover the next rejection.

4. **Render the kernel command line to match real kernel behaviour, not folklore.** Emit
   `intel_idle.max_cstate=0` **and** `processor.max_cstate=1`: `processor.*` is inert while
   `intel_idle` is the active driver, and acpi_idle clamps the parameter
   (`if (max_cstate == 0) max_cstate = 1`), so `=0` and `=1` are the same instruction — `=1`
   states the honest outcome, that C1/POLL remain. Emit `rcu_nocbs` alongside `nohz_full`.
   Route gigantic pages to `hugepagesz=`/`hugepages=` boot parameters; `vm.nr_hugepages` sizes
   only the default (2 MiB on x86_64) pool.

5. **Append to the boot arguments; never replace the line.** The playbook uses `grubby
   --update-kernel=ALL` on the RHEL family and a `backrefs` `lineinfile` on the Debian family,
   then runs `update-grub`. Writing a fixed `GRUB_CMDLINE_LINUX_DEFAULT=` discards `console=`,
   `root=`, and `crashkernel=` — on a headless co-located host that can cost you the serial
   console you would need to recover it.

6. **Regenerate the bootloader config, then prove the arguments are live.** Editing
   `/etc/default/grub` changes nothing until `grub.cfg` is regenerated, and nothing takes effect
   until reboot. The final task reads `/proc/cmdline` and asserts `isolcpus=` is present, so a
   staged-but-never-applied host fails loudly instead of reporting tuned and running untuned.

7. **Enable both PTP daemons.** `ptp4l` disciplines the NIC's PTP hardware clock only; without
   `phc2sys`, `CLOCK_REALTIME` — the clock the application actually stamps with — stays
   free-running. Then hand off to `clock-synchronization-ptp-for-trading-hosts` for the offset
   evidence.

8. **Review the rendered artifacts before applying them.** They are templates: the AMI is a
   required Terraform variable, and `prevent_destroy` is set so an incidental plan diff cannot
   replace a live gateway.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Staging kernel arguments that never take effect.** A `lineinfile` on `/etc/default/grub`
  with no `update-grub`/`grubby` and no reboot is a playbook that reports `changed` and tunes
  nothing. Every host then looks provisioned and runs stock. Assert against `/proc/cmdline`.
- **Replacing `GRUB_CMDLINE_LINUX_DEFAULT` wholesale.** It carries `console=`, `root=`, and
  distro-specific arguments. Overwriting it on a remote co-located host can leave you with a
  machine that boots without a console you can reach.
- **Believing `processor.max_cstate=0` disables C-states on an Intel host.** It does not:
  `intel_idle` ignores `processor.*` entirely, and acpi_idle clamps `0` to `1`. Without
  `intel_idle.max_cstate=0` the deep states stay available and you keep paying the C6 exit
  latency you thought you had removed.
- **Assuming `vm.nr_hugepages` gives you 1 GiB pages.** It sizes the *default* pool — 16 pages
  is 32 MiB on x86_64, not 16 GiB. Gigantic pages are reserved at boot via `hugepagesz=1G
  hugepages=N`, because they cannot be allocated reliably once memory is fragmented.
- **Treating `net.core.rmem_max` as the buffer size.** It is a *ceiling* on what an application
  may request with `SO_RCVBUF`, and the kernel doubles whatever the application sets "to allow
  space for bookkeeping overhead". Raising the ceiling changes no socket's buffer on its own —
  a feed handler that never calls `setsockopt` still drops on burst. Verify with the process,
  not with `sysctl`.
- **Setting the governor with `cpupower` and calling it provisioned.** `cpupower frequency-set`
  does not survive a reboot. Without a persistence unit, the very reboot required to apply the
  kernel arguments also reverts the governor — a drift the audit cannot see.
- **Approving a spec for a platform that cannot honour it.** C-state and P-state control needs
  bare metal (or an AWS-listed instance type); Graviton offers neither. A green audit on a
  Graviton host is a green audit on tuning that does not exist.
- **Enabling `ptp4l` alone.** The PHC gets disciplined, `CLOCK_REALTIME` does not, and the
  timestamps in your order records come from the clock nobody synchronized.
- **Isolating CPU 0, or isolating every core.** The kernel needs a housekeeping/timekeeping CPU
  and will quietly take one back.
- **Applying a Terraform change to a live gateway mid-session.** An AMI or user-data change
  forces instance replacement. `prevent_destroy` turns that into a plan error instead of a
  destroyed execution host.

## Verification

- Audit a compliant spec (`performance`, `isolcpus=2-15`, C-states disabled, 128 MiB buffers,
  PTP on) ⟹ `IAC_SPEC_APPROVED`, empty `violations`, both artifacts rendered.
- Audit the same spec with `enable_ptp_clock_sync=False` ⟹ `REJECTED_PTP_DISABLED` and **no**
  generated code. Audit it with `isolated_cpu_cores=""` ⟹ `REJECTED_INVALID_SPEC`. Both were
  previously approved, with the report text asserting `PTP = True` regardless.
- Audit a spec failing three checks at once ⟹ `violations` lists all three
  (`REJECTED_POWERSAVE_GOVERNOR`, `REJECTED_CSTATES_ENABLED`, `REJECTED_PTP_DISABLED`).
- Pass `host_name='node" }\nresource "null_resource" "pwn" {'` ⟹ `REJECTED_INVALID_SPEC`, and
  `generate_terraform_hcl` / `generate_ansible_playbook` raise `SpecValidationError` rather than
  emitting attacker-controlled HCL or root-run YAML.
- Confirm the generated playbook parses as YAML, contains `update-grub`, `grubby
  --update-kernel=ALL`, `sysctl_set: true`, both `ptp4l` and `phc2sys`, a `/proc/cmdline`
  assertion, and **no** `ansible.builtin.reboot`.
- Confirm `kernel_command_line_args()` emits `intel_idle.max_cstate=0 processor.max_cstate=1`
  (never `processor.max_cstate=0`), and that `hugepage_size_kb=1048576` produces
  `hugepagesz=1G` on the command line instead of a `vm.nr_hugepages` sysctl.
- Run `python -m unittest discover -s skills/infrastructure-as-code-for-trading-hosts/scripts`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `immutable-infrastructure-for-trading-bots`
- `configuration-drift-detection-across-environments`
- `environment-parity-dev-staging-production`
- `feed-handler-cpu-pinning-and-numa-awareness`
- `co-location-provider-selection-and-network-topology`
