# Standards and Sources for IaC Trading Host Provisioning

No regulator or exchange publishes a kernel-tuning standard for trading hosts. Everything in
the "site policy" table below is a **house threshold**, chosen by the firm and enforced by
`IacTradingHostManagerEngine`. Everything in the "verified behaviour" table is documented
upstream behaviour, cited, and must not be restated as policy.

## Site policy (configurable — not a published standard)

| Control | Policy enforced by the engine | Notes |
|---|---|---|
| CPU governor | MUST be `performance` | Any other value yields `REJECTED_POWERSAVE_GOVERNOR`. |
| CPU isolation | `isolated_cpu_cores` MUST parse to a non-empty set excluding CPU 0 | Also bounded by `total_cpu_count` when supplied. |
| C-states | `disable_cpu_cstates` MUST be `True` | Renders `intel_idle.max_cstate=0 processor.max_cstate=1`. |
| Socket buffers | `net.core.rmem_max` and `net.core.wmem_max` MUST be ≥ `min_socket_buffer_bytes` (default 134,217,728 B = 128 MiB) | Constructor parameter. Size it from peak burst rate × tolerable drain stall; the default is a convention, not a requirement. |
| Time sync | `enable_ptp_clock_sync` MUST be `True`, with both `ptp4l` and `phc2sys` units | Offset evidence belongs to `clock-synchronization-ptp-for-trading-hosts`. |

## Verified upstream behaviour (do not paraphrase away the source)

| Claim | Source |
|---|---|
| `intel_idle.max_cstate=0` "disables intel_idle and fall back on acpi_idle"; `1` to `9` specify the maximum C-state depth. | Linux `Documentation/admin-guide/kernel-parameters.txt` |
| acpi_idle clamps the parameter: `if (max_cstate == 0) max_cstate = 1;` in `acpi_processor_setup_cpuidle_cx()` and `acpi_processor_setup_cstates()`. `processor.max_cstate=0` therefore means C1, not "no C-states". | Linux `drivers/acpi/processor_idle.c` |
| C6 exit latency: 133 µs on Skylake-SP (`skx_cstates`) and Haswell (`hsw_cstates`), 290 µs on Sapphire Rapids (`spr_cstates`). C1 is 1–2 µs. | Linux `drivers/idle/intel_idle.c` |
| The kernel clears the boot CPU from the `nohz_full` mask — `pr_warn("NO_HZ: Clearing %d from nohz_full range for timekeeping")` — and `tick_nohz_cpu_hotpluggable()` keeps the timekeeping CPU online because it "handles housekeeping duty (unbound timers, workqueues, timekeeping, ...) on behalf of full dynticks CPUs". | Linux `kernel/time/tick-sched.c` |
| `isolcpus=` is a boot-time-only, static CPU set; it carried a `[Deprecated - use cpusets instead]` tag in kernel documentation, and an April 2026 patch (Acked-by Waiman Long) removes that tag in favour of pointing at `Documentation/admin-guide/cpu-isolation.rst`. Treat it as supported but immutable without a reboot; cpuset partitions are the runtime-adjustable alternative. | Linux kernel documentation and LKML |
| `SO_RCVBUF`: "The kernel doubles this value (to allow space for bookkeeping overhead) when it is set using setsockopt(2)… the maximum allowed value is set by the /proc/sys/net/core/rmem_max file." Same wording for `SO_SNDBUF`/`wmem_max`. `rmem_max` is a ceiling on requests, not an applied buffer size. | `socket(7)`, man-pages |
| `vm.nr_hugepages` sizes the pool of the **default** huge page size. A non-default size is preallocated with `hugepagesz=` plus `hugepages=`, or adjusted at runtime under `/sys/kernel/mm/hugepages/hugepages-<size>kB/`. | Linux `Documentation/admin-guide/mm/hugetlbpage.rst` |
| `ansible.posix.sysctl`: `sysctl_set` defaults to `false`; only with `sysctl_set: true` does the module "verify token value with the sysctl command and set with `-w` if necessary". `reload` defaults to `true` but only fires when the file changed. | Ansible `ansible.posix.sysctl` module documentation |
| Editing `/etc/default/grub` does not update existing boot entries. RHEL 8+/9 use BLS entries — apply with `grubby --update-kernel=ALL --args=...`, or refresh BLS from `GRUB_CMDLINE_LINUX` with `grub2-mkconfig --update-bls-cmdline`. Debian/Ubuntu require `update-grub`. | Red Hat, *Managing, monitoring and updating the kernel* (RHEL 9), ch. "Configuring kernel command-line parameters" |
| EC2 processor state control: "The following instance types provide the ability for an operating system to control C-states and P-states: … **Bare metal**: All bare metal instances with Intel and AMD processors", plus an enumerated list of other types. "AWS Graviton processors have built-in power saving modes and operate at a fixed frequency. Therefore, they do not provide the ability for the operating system to control C-states and P-states." | AWS, *Processor state control for Amazon EC2 Linux instances* |
| Terraform identifiers "can contain letters, digits, underscores (`_`), and hyphens (`-`). The first character of an identifier must not be a digit". | HashiCorp, *Terraform configuration syntax* |
| `linuxptp` unit naming differs by distro: RHEL configures `ptp4l`/`phc2sys` via `/etc/sysconfig/*`; Debian/Ubuntu ship template units enabled as `phc2sys@<interface>`. `phc2sys` is required with hardware timestamping to synchronize the system clock to the NIC's PHC. | Red Hat *System Administrator's Guide*, ch. "Configuring PTP Using ptp4l"; `linuxptp` packaging |

## Regulatory touchpoint

Clock synchronization on a trading host may be subject to a divergence ceiling — MiFID II
RTS 25 in the EU, FINRA CAT clock-sync obligations in the US. Those ceilings bind the *time
evidence*, not this module's provisioning output, and they differ by activity and
jurisdiction. Do not encode a ceiling here; see
`clock-synchronization-ptp-for-trading-hosts` and `mifid-ii-algo-trading-compliance-eu`.
