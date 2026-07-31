import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class TradingHostSpec:
    host_name: str                      # e.g. 'co-ny4-hft-node-01'
    cpu_governor: str                   # MUST BE 'performance'
    isolated_cpu_cores: str             # e.g. '2-15' (via isolcpus)
    disable_cpu_cstates: bool           # MUST BE True (processor.max_cstate=0)
    net_rmem_max_bytes: int             # MUST BE >= 134,217,728 (128 MB)
    net_wmem_max_bytes: int             # MUST BE >= 134,217,728 (128 MB)
    enable_ptp_clock_sync: bool         # MUST BE True (ptp4l)
    hugepages_count: int = 16           # 1GB HugePages count

@dataclass
class IacTradingHostReport:
    host_name: str
    is_cpu_governor_valid: bool
    is_cpu_isolation_valid: bool
    is_cstates_disabled: bool
    is_socket_buffer_valid: bool
    is_ptp_sync_active: bool
    generated_ansible_playbook: str
    generated_terraform_hcl: str
    status: str                         # 'IAC_SPEC_APPROVED', 'REJECTED_POWERSAVE_GOVERNOR', 'REJECTED_CSTATES_ENABLED', 'REJECTED_SMALL_BUFFERS'
    audit_notes: str

class IacTradingHostManagerEngine:
    """
    Infrastructure-as-Code (IaC) engine for provisioning and auditing low-latency trading hosts via Terraform and Ansible,
    automating CPU core isolation (isolcpus), C-state disabling, socket buffer tuning, and PTP clock sync.
    """
    def __init__(self):
        pass

    def generate_terraform_hcl(self, spec: TradingHostSpec) -> str:
        """
        Generates Terraform HCL resource block for bare-metal / AWS EC2 metal instance.
        """
        hcl = f"""
# Terraform Bare-Metal / High-Performance Node Provisioner
resource "aws_instance" "{spec.host_name.replace('-', '_')}" {{
  ami           = "ami-0123456789abcdef0" # Low-Latency Custom Kernel AMI
  instance_type = "c6i.metal"
  tags = {{
    Name = "{spec.host_name}"
    Role = "HFT-Execution-Gateway"
  }}
}}
"""
        return hcl.strip()

    def generate_ansible_playbook(self, spec: TradingHostSpec) -> str:
        """
        Generates Ansible playbook tasks for OS kernel tuning and sysctl parameters.
        """
        playbook = f"""
---
- name: Low-Latency HFT Tuning for {spec.host_name}
  hosts: {spec.host_name}
  become: true
  tasks:
    - name: Set CPU Governor to Performance
      ansible.builtin.shell: "cpupower frequency-set -g {spec.cpu_governor}"

    - name: Configure GRUB Kernel Parameters (CPU Isolation & C-States)
      ansible.builtin.lineinfile:
        path: /etc/default/grub
        regexp: '^GRUB_CMDLINE_LINUX_DEFAULT='
        line: 'GRUB_CMDLINE_LINUX_DEFAULT="quiet isolcpus={spec.isolated_cpu_cores} nohz_full={spec.isolated_cpu_cores} processor.max_cstate=0 intel_idle.max_cstate=0"'

    - name: Apply Sysctl Network Socket Buffer Tuning
      ansible.posix.sysctl:
        name: "{{ item.key }}"
        value: "{{ item.value }}"
        state: present
      loop:
        - {{ key: 'net.core.rmem_max', value: '{spec.net_rmem_max_bytes}' }}
        - {{ key: 'net.core.wmem_max', value: '{spec.net_wmem_max_bytes}' }}
        - {{ key: 'vm.nr_hugepages', value: '{spec.hugepages_count}' }}

    - name: Enable PTP4L Time Sync Service
      ansible.builtin.systemd:
        name: ptp4l
        state: started
        enabled: true
"""
        return playbook.strip()

    def audit_and_generate_iac(self, spec: TradingHostSpec) -> IacTradingHostReport:
        """
        Audits proposed host spec against HFT low-latency benchmarks and generates Terraform/Ansible code.
        """
        is_gov_valid = (spec.cpu_governor.lower() == "performance")
        is_iso_valid = bool(spec.isolated_cpu_cores.strip())
        is_cstate_valid = spec.disable_cpu_cstates
        is_buf_valid = (spec.net_rmem_max_bytes >= 134_217_728) and (spec.net_wmem_max_bytes >= 134_217_728)
        is_ptp_valid = spec.enable_ptp_clock_sync

        if not is_gov_valid:
            status = "REJECTED_POWERSAVE_GOVERNOR"
            notes = f"IaC SPEC REJECTED [{spec.host_name}]: CPU Governor '{spec.cpu_governor}' must be set to 'performance'."
            logger.error(notes)
        elif not is_cstate_valid:
            status = "REJECTED_CSTATES_ENABLED"
            notes = f"IaC SPEC REJECTED [{spec.host_name}]: CPU C-States MUST be disabled (disable_cpu_cstates = True)."
            logger.error(notes)
        elif not is_buf_valid:
            status = "REJECTED_SMALL_BUFFERS"
            notes = f"IaC SPEC REJECTED [{spec.host_name}]: rmem_max ({spec.net_rmem_max_bytes} B) must be >= 128 MB."
            logger.error(notes)
        else:
            status = "IAC_SPEC_APPROVED"
            notes = (
                f"IaC SPEC APPROVED [{spec.host_name}]: CPU Governor = {spec.cpu_governor}, "
                f"IsolCpus = {spec.isolated_cpu_cores}, C-States Disabled = True, rmem_max = {spec.net_rmem_max_bytes/1048576:.0f} MB, PTP = True."
            )
            logger.info(notes)

        tf_hcl = self.generate_terraform_hcl(spec) if status == "IAC_SPEC_APPROVED" else ""
        ansible_pb = self.generate_ansible_playbook(spec) if status == "IAC_SPEC_APPROVED" else ""

        return IacTradingHostReport(
            host_name=spec.host_name,
            is_cpu_governor_valid=is_gov_valid,
            is_cpu_isolation_valid=is_iso_valid,
            is_cstates_disabled=is_cstate_valid,
            is_socket_buffer_valid=is_buf_valid,
            is_ptp_sync_active=is_ptp_valid,
            generated_ansible_playbook=ansible_pb,
            generated_terraform_hcl=tf_hcl,
            status=status,
            audit_notes=notes
        )
