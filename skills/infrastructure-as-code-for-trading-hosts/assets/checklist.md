# Pre-Flight Checklist — Low-Latency Trading Host Provisioning

## Spec

- [ ] `host_name` is a valid RFC 1123 label (it is interpolated into root-run Ansible and HCL).
- [ ] `isolated_cpu_cores` parses as a cpulist, excludes CPU 0, and every index exists on the host.
- [ ] `total_cpu_count` supplied so out-of-range isolation is caught.
- [ ] Socket buffer policy chosen deliberately (burst rate × tolerable stall), not inherited as 128 MiB by default.
- [ ] `hugepage_size_kb` set to 1048576 if 1 GiB pages were intended — `vm.nr_hugepages` sizes the 2 MiB pool.
- [ ] `ptp_service_units` match the target distro (`ptp4l`/`phc2sys` on RHEL; `@<iface>` template units on Debian/Ubuntu).

## Platform

- [ ] Target is bare metal, or an instance type documented as supporting OS C-state control.
- [ ] Not Graviton (no OS control of C-states or P-states at all).
- [ ] Audit `warnings` reviewed, not just `status`.

## Audit

- [ ] `status == IAC_SPEC_APPROVED` and `violations` is empty.
- [ ] Artifacts reviewed by a human before `terraform apply` / `ansible-playbook`.
- [ ] Terraform AMI supplied via `var.trading_host_ami`; `prevent_destroy` left in place.

## Application

- [ ] Reboot scheduled out of session — the host may be holding positions.
- [ ] After reboot, `/proc/cmdline` contains `isolcpus=`, `nohz_full=`, `rcu_nocbs=`, `intel_idle.max_cstate=0`.
- [ ] Governor still `performance` after the reboot (persistence unit enabled).
- [ ] `sysctl net.core.rmem_max` correct **and** the feed handler observed requesting it via `SO_RCVBUF`.
- [ ] Both `ptp4l` and `phc2sys` active; `CLOCK_REALTIME` offset evidenced, not assumed.
