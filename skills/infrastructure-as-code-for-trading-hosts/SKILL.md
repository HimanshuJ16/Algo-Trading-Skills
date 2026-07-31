---
name: infrastructure-as-code-for-trading-hosts
description: >-
  Infrastructure-as-Code (IaC) engine for provisioning and auditing low-latency trading hosts via Terraform and Ansible, automating CPU core isolation (isolcpus), C-state disabling, socket buffer tuning, and PTP clock sync.
domain: Infrastructure & DevOps
subdomain: Low-Latency Host Provisioning & IaC Automation
tags: ["iac", "terraform", "ansible", "low-latency", "cpu-isolation", "isolcpus", "c-states", "ptp4l", "sysctl"]
brokers_frameworks: ["Terraform HCL", "Ansible Playbooks", "Linux PTP4L", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when provisioning bare-metal co-located servers (Equinix NY4/LD4/TY3) or high-performance cloud VMs (`c6i.metal`) for algorithmic trading systems. Standard OS defaults introduce jitter and latency spikes due to CPU power-saving C-states, context switching, and small network socket buffers. This module uses **Terraform** for infrastructure provisioning and **Ansible** for kernel tuning: isolating CPU cores (`isolcpus=2-15`), disabling C-states (`max_cstate=0`), tuning network socket buffers ($128\text{ MB}$ `rmem_max`), and configuring PTP IEEE 1588 time synchronization (`ptp4l`).

## Prerequisites

- Host specification parameters (`host_name`, `cpu_governor`, `isolated_cpu_cores`, `disable_cpu_cstates`, `net_rmem_max_bytes`, `enable_ptp_clock_sync`).
- Ansible / Terraform deployment target specifications.

## Workflow

1. **Host Spec Ingestion**:
   - Ingest proposed host specification (`cpu_governor = "performance"`, `isolated_cpu_cores = "2-15"`, `disable_cpu_cstates = True`).
2. **Low-Latency IaC Audit**:
   - Audit CPU Governor (`"performance"`).
   - Audit CPU Core Isolation (`isolcpus=2-15` or `nohz_full`).
   - Audit C-States Disabling (`max_cstate=0`).
   - Audit Network Buffer Sizes ($128\text{ MB}$ `rmem_max`/`wmem_max`).
   - Audit PTP IEEE 1588 Clock Sync status (`ptp4l` active).
3. **Terraform HCL & Ansible Playbook Generation**:
   - Generate Terraform HCL provisioner blocks and Ansible sysctl/GRUB playbooks.
4. **Audit Report Generation**: Output structured `IacTradingHostReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Leaving CPU Frequency Governor on Powersave**: Operating trading servers on default Linux `powersave` governor, causing CPU frequency throttle delays during market volume surges.
- **Neglecting C-State Disabling**: Leaving CPU C-states enabled, incurring $50\mu\text{s}$ CPU wake-up latency penalties when idle cores resume execution.
- **Manual In-Place Server Configurations**: Hand-editing `/etc/sysctl.conf` or GRUB via SSH without committing changes to Ansible playbooks, leading to configuration drift upon server reboots.

## Verification

- Instantiate `IacTradingHostManagerEngine`. Audit Valid Low-Latency Host Spec (`cpu_governor="performance"`, `isolated_cpu_cores="2-15"`, `disable_cstates=True`, `rmem_max=134217728`, `ptp=True`) $\implies$ verify `IAC_SPEC_APPROVED` and generate valid Ansible/Terraform HCL code blocks. Audit Flawed Spec (`powersave` governor, C-states enabled) $\implies$ verify `REJECTED_POWERSAVE_GOVERNOR` and `REJECTED_CSTATES_ENABLED`.
- Run `python scripts/test_iac_trading_host_manager.py`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `immutable-infrastructure-for-trading-bots`
---
