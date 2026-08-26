"""Audit a proposed low-latency trading-host specification and emit the
Terraform / Ansible artifacts that implement it.

The engine is an **advisory generator**: it validates a spec, records every
violation it finds, and only renders infrastructure code for a spec that passed
every check. It never touches a host, and the artifacts it produces are
templates for human review — not something to pipe straight into
``terraform apply`` on a machine that is holding positions.

Kernel-behaviour claims encoded here are sourced in ``references/standards.md``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Audit status codes -----------------------------------------------------
STATUS_APPROVED = "IAC_SPEC_APPROVED"
STATUS_INVALID_SPEC = "REJECTED_INVALID_SPEC"
STATUS_BAD_GOVERNOR = "REJECTED_POWERSAVE_GOVERNOR"
STATUS_CSTATES_ENABLED = "REJECTED_CSTATES_ENABLED"
STATUS_SMALL_BUFFERS = "REJECTED_SMALL_BUFFERS"
STATUS_NO_ISOLATION = "REJECTED_NO_CPU_ISOLATION"
STATUS_PTP_DISABLED = "REJECTED_PTP_DISABLED"

# Site policy, not a published standard. See references/standards.md.
DEFAULT_MIN_SOCKET_BUFFER_BYTES = 134_217_728  # 128 MiB

REQUIRED_CPU_GOVERNOR = "performance"

# RFC 1123 host label: letters/digits/hyphen, no leading or trailing hyphen.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
# Kernel cpulist subset accepted here: "2", "2-15", "2-7,10,12-15".
_CPU_LIST_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")
# systemd unit names, including template units such as "phc2sys@ens1f0".
_UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+(@[A-Za-z0-9_.:-]+)?$")
# EC2 / bare-metal instance type identifiers.
_INSTANCE_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SpecValidationError(ValueError):
    """Raised when a spec cannot be rendered into safe infrastructure code."""


@dataclass
class TradingHostSpec:
    """A proposed low-latency trading host.

    Every string field is interpolated into generated Ansible/Terraform, so all
    of them are validated before rendering (see :func:`validate_spec`).
    """

    host_name: str                      # RFC 1123 label, e.g. 'co-ny4-hft-node-01'
    cpu_governor: str                   # MUST BE 'performance'
    isolated_cpu_cores: str             # kernel cpulist, e.g. '2-15'; CPU 0 must stay housekeeping
    disable_cpu_cstates: bool           # MUST BE True (intel_idle.max_cstate=0 processor.max_cstate=1)
    net_rmem_max_bytes: int             # ceiling on SO_RCVBUF; see standards.md
    net_wmem_max_bytes: int             # ceiling on SO_SNDBUF; see standards.md
    enable_ptp_clock_sync: bool         # MUST BE True (ptp4l AND phc2sys)
    hugepages_count: int = 16           # pages of hugepage_size_kb, not necessarily 1 GB pages
    hugepage_size_kb: int = 2048        # x86_64 default pool size; 1048576 for 1 GB pages
    total_cpu_count: Optional[int] = None   # when known, enforces a housekeeping CPU remains
    instance_type: str = "c6i.metal"    # OS C-state/P-state control needs bare metal on EC2
    ptp_service_units: Sequence[str] = ("ptp4l", "phc2sys")


@dataclass
class IacTradingHostReport:
    """Outcome of one host-spec audit."""

    host_name: str
    is_cpu_governor_valid: bool
    is_cpu_isolation_valid: bool
    is_cstates_disabled: bool
    is_socket_buffer_valid: bool
    is_ptp_sync_active: bool
    generated_ansible_playbook: str
    generated_terraform_hcl: str
    status: str
    audit_notes: str
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED


def parse_cpu_list(cpu_list: str) -> FrozenSet[int]:
    """Expand a kernel cpulist such as ``'2-7,10'`` into the set of CPU indices.

    Only the plain ``N`` / ``N-M`` comma forms are accepted. The kernel also
    understands stride forms (``0-15:2/4``); they are rejected here rather than
    silently mis-parsed, because the expansion feeds a safety check.
    """
    text = cpu_list.strip()
    if not text:
        raise SpecValidationError("isolated_cpu_cores is empty")
    if not _CPU_LIST_RE.match(text):
        raise SpecValidationError(
            f"isolated_cpu_cores {cpu_list!r} is not a supported cpulist "
            "(expected forms like '2', '2-15' or '2-7,10,12-15')"
        )

    cpus: set = set()
    for chunk in text.split(","):
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise SpecValidationError(
                    f"isolated_cpu_cores range {chunk!r} is inverted (start > end)"
                )
            cpus.update(range(start, end + 1))
        else:
            cpus.add(int(chunk))
    return frozenset(cpus)


def validate_spec(spec: TradingHostSpec) -> List[str]:
    """Return every structural problem that makes ``spec`` unsafe to render.

    Structural validity is separate from policy compliance: a spec can be
    perfectly well-formed and still be rejected for running ``powersave``.
    """
    errors: List[str] = []

    if not isinstance(spec.host_name, str) or not _HOSTNAME_RE.match(spec.host_name):
        errors.append(
            f"host_name {spec.host_name!r} is not a valid RFC 1123 host label; "
            "it is interpolated into generated Ansible and Terraform"
        )
    if not isinstance(spec.cpu_governor, str) or not spec.cpu_governor.strip().isalpha():
        errors.append(
            f"cpu_governor {spec.cpu_governor!r} must be a bare alphabetic governor name"
        )
    if not isinstance(spec.instance_type, str) or not _INSTANCE_TYPE_RE.match(spec.instance_type):
        errors.append(f"instance_type {spec.instance_type!r} contains unsupported characters")

    for label, value in (
        ("net_rmem_max_bytes", spec.net_rmem_max_bytes),
        ("net_wmem_max_bytes", spec.net_wmem_max_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"{label} must be a positive integer, got {value!r}")

    if not isinstance(spec.hugepages_count, int) or isinstance(spec.hugepages_count, bool) \
            or spec.hugepages_count < 0:
        errors.append(f"hugepages_count must be a non-negative integer, got {spec.hugepages_count!r}")
    if not isinstance(spec.hugepage_size_kb, int) or isinstance(spec.hugepage_size_kb, bool) \
            or spec.hugepage_size_kb <= 0:
        errors.append(f"hugepage_size_kb must be a positive integer, got {spec.hugepage_size_kb!r}")

    if spec.total_cpu_count is not None:
        if not isinstance(spec.total_cpu_count, int) or isinstance(spec.total_cpu_count, bool) \
                or spec.total_cpu_count <= 0:
            errors.append(f"total_cpu_count must be a positive integer, got {spec.total_cpu_count!r}")

    if spec.enable_ptp_clock_sync:
        units = list(spec.ptp_service_units or [])
        if not units:
            errors.append("enable_ptp_clock_sync is True but ptp_service_units is empty")
        for unit in units:
            if not isinstance(unit, str) or not _UNIT_NAME_RE.match(unit):
                errors.append(f"ptp_service_units entry {unit!r} is not a valid systemd unit name")

    # cpulist is validated even when the value looks empty, so the caller gets a
    # structural error rather than a silent 'isolcpus=' on the kernel cmdline.
    try:
        parse_cpu_list(spec.isolated_cpu_cores)
    except SpecValidationError as exc:
        errors.append(str(exc))

    return errors


def _isolation_policy_errors(spec: TradingHostSpec) -> List[str]:
    """Policy checks on the isolated CPU set (assumes the spec parses)."""
    errors: List[str] = []
    isolated = parse_cpu_list(spec.isolated_cpu_cores)

    if 0 in isolated:
        # tick_nohz_init() clears the boot CPU from the nohz_full mask and warns
        # "NO_HZ: Clearing %d from nohz_full range for timekeeping"; the
        # timekeeping CPU must stay online and un-isolated. Keeping CPU 0 out of
        # the set is also what guarantees a housekeeping CPU always remains.
        errors.append(
            "CPU 0 is in isolated_cpu_cores; the kernel keeps the boot CPU for "
            "timekeeping and housekeeping, so isolating it is silently undone at best"
        )
    if spec.total_cpu_count is not None:
        out_of_range = sorted(c for c in isolated if c >= spec.total_cpu_count)
        if out_of_range:
            errors.append(
                f"isolated CPUs {out_of_range} do not exist on a {spec.total_cpu_count}-CPU host"
            )
    return errors


def _format_hugepage_size(size_kb: int) -> str:
    """Render a hugepage size in the ``hugepagesz=`` units the kernel accepts."""
    if size_kb % (1024 * 1024) == 0:
        return f"{size_kb // (1024 * 1024)}G"
    if size_kb % 1024 == 0:
        return f"{size_kb // 1024}M"
    return f"{size_kb}K"


class IacTradingHostManagerEngine:
    """Audits low-latency trading host specs and renders Terraform/Ansible.

    The engine is stateless and safe to reuse across specs. Thresholds are
    constructor parameters because they are site policy, not published
    standards — see ``references/standards.md``.
    """

    def __init__(self, min_socket_buffer_bytes: int = DEFAULT_MIN_SOCKET_BUFFER_BYTES) -> None:
        if min_socket_buffer_bytes <= 0:
            raise ValueError("min_socket_buffer_bytes must be positive")
        self.min_socket_buffer_bytes = min_socket_buffer_bytes

    # --- artifact rendering -------------------------------------------------

    @staticmethod
    def _terraform_identifier(host_name: str) -> str:
        """Derive a Terraform identifier from a validated host name.

        Terraform identifiers may contain letters, digits, underscores and
        hyphens, but "the first character of an identifier must not be a digit".
        """
        identifier = host_name.replace("-", "_")
        if identifier[0].isdigit():
            identifier = f"host_{identifier}"
        return identifier

    def kernel_command_line_args(self, spec: TradingHostSpec) -> str:
        """Build the low-latency kernel arguments implied by ``spec``.

        ``processor.max_cstate=1`` rather than ``0``: acpi_idle clamps the
        parameter (``if (max_cstate == 0) max_cstate = 1``), so ``0`` and ``1``
        are the same instruction, and ``1`` states the real outcome — C1/POLL
        remain available.
        """
        args = [
            f"isolcpus={spec.isolated_cpu_cores}",
            f"nohz_full={spec.isolated_cpu_cores}",
            f"rcu_nocbs={spec.isolated_cpu_cores}",
        ]
        if spec.disable_cpu_cstates:
            # intel_idle.max_cstate=0 disables intel_idle and falls back to
            # acpi_idle; processor.max_cstate has no effect while intel_idle is
            # the active driver, so both are required on Intel hosts.
            args.extend(["intel_idle.max_cstate=0", "processor.max_cstate=1"])
        if spec.hugepages_count > 0 and spec.hugepage_size_kb != 2048:
            # Gigantic pages cannot be reliably allocated at runtime once memory
            # is fragmented, and vm.nr_hugepages only sizes the *default* pool.
            size = _format_hugepage_size(spec.hugepage_size_kb)
            args.extend([
                f"default_hugepagesz={size}",
                f"hugepagesz={size}",
                f"hugepages={spec.hugepages_count}",
            ])
        return " ".join(args)

    def generate_terraform_hcl(self, spec: TradingHostSpec) -> str:
        """Render the Terraform resource block for the host.

        Raises :class:`SpecValidationError` for a spec that cannot be rendered
        safely — the alternative is emitting attacker-controlled HCL.
        """
        errors = validate_spec(spec)
        if errors:
            raise SpecValidationError("; ".join(errors))

        identifier = self._terraform_identifier(spec.host_name)
        hcl = f"""
# Terraform bare-metal / high-performance node provisioner.
# The AMI is a required input: pin the reviewed low-latency kernel image per
# environment rather than baking an identifier into the module.
variable "trading_host_ami" {{
  description = "AMI ID of the reviewed low-latency kernel image"
  type        = string
}}

resource "aws_instance" "{identifier}" {{
  ami           = var.trading_host_ami
  instance_type = "{spec.instance_type}"

  tags = {{
    Name = "{spec.host_name}"
    Role = "HFT-Execution-Gateway"
  }}

  # A live execution gateway must never be replaced by an incidental plan diff
  # (an AMI bump forces replacement). Destroy it deliberately, out of session.
  lifecycle {{
    prevent_destroy = true
  }}
}}
"""
        return hcl.strip()

    def generate_ansible_playbook(self, spec: TradingHostSpec) -> str:
        """Render the Ansible playbook implementing the kernel/OS tuning.

        The playbook deliberately does **not** reboot: it stages the kernel
        arguments and then asserts them against ``/proc/cmdline``, so an
        un-rebooted host fails loudly instead of reporting success while running
        untuned. Rebooting a host that may hold positions is an operator
        decision, not a provisioning side effect.

        Raises :class:`SpecValidationError` for an unrenderable spec.
        """
        errors = validate_spec(spec)
        if errors:
            raise SpecValidationError("; ".join(errors))

        kernel_args = self.kernel_command_line_args(spec)
        governor = REQUIRED_CPU_GOVERNOR if spec.cpu_governor.strip().lower() == \
            REQUIRED_CPU_GOVERNOR else spec.cpu_governor.strip().lower()

        sysctl_entries = [
            f"- {{ key: 'net.core.rmem_max', value: '{spec.net_rmem_max_bytes}' }}",
            f"- {{ key: 'net.core.wmem_max', value: '{spec.net_wmem_max_bytes}' }}",
        ]
        if spec.hugepages_count > 0 and spec.hugepage_size_kb == 2048:
            # vm.nr_hugepages sizes the default-size pool only; non-default
            # sizes are handled on the kernel command line above.
            sysctl_entries.append(
                f"- {{ key: 'vm.nr_hugepages', value: '{spec.hugepages_count}' }}"
            )
        sysctl_block = "\n        ".join(sysctl_entries)

        ptp_units = list(spec.ptp_service_units) if spec.enable_ptp_clock_sync else []
        ptp_task = ""
        if ptp_units:
            unit_list = "\n        ".join(f"- {unit}" for unit in ptp_units)
            ptp_task = f"""

    # ptp4l disciplines the NIC's PTP hardware clock only. Without phc2sys,
    # CLOCK_REALTIME -- what the application actually stamps with -- stays
    # free-running. On Debian/Ubuntu these are template units: pass
    # ptp_service_units=('ptp4l@ens1f0', 'phc2sys@ens1f0').
    - name: Enable PTP time synchronisation daemons
      ansible.builtin.systemd:
        name: "{{{{ item }}}}"
        state: started
        enabled: true
      loop:
        {unit_list}"""

        playbook = f"""
---
- name: Low-latency trading host tuning for {spec.host_name}
  hosts: {spec.host_name}
  become: true
  vars:
    trading_kernel_args: "{kernel_args}"
  tasks:
    - name: Set CPU frequency governor to {governor} (running system)
      ansible.builtin.command:
        cmd: cpupower frequency-set -g {governor}
      register: cpupower_result
      changed_when: true

    # cpupower does not survive a reboot; without this unit the host silently
    # drifts back to the distribution default governor on the next restart.
    - name: Persist the CPU governor across reboots
      ansible.builtin.copy:
        dest: /etc/systemd/system/cpu-governor-{governor}.service
        mode: "0644"
        content: |
          [Unit]
          Description=Pin the CPU frequency governor to {governor}
          After=multi-user.target

          [Service]
          Type=oneshot
          RemainAfterExit=yes
          ExecStart=/usr/bin/cpupower frequency-set -g {governor}

          [Install]
          WantedBy=multi-user.target

    - name: Enable the CPU governor unit
      ansible.builtin.systemd:
        name: cpu-governor-{governor}
        state: started
        enabled: true
        daemon_reload: true

    # Kernel arguments are APPENDED. Replacing GRUB_CMDLINE_LINUX_DEFAULT
    # wholesale drops console=, root= and crashkernel= and can leave a headless
    # co-located host unreachable.
    - name: Stage low-latency kernel arguments (RHEL family, BLS entries)
      ansible.builtin.command:
        cmd: grubby --update-kernel=ALL --args="{{{{ trading_kernel_args }}}}"
      when: ansible_facts['os_family'] == 'RedHat'
      changed_when: true

    - name: Stage low-latency kernel arguments (Debian family)
      ansible.builtin.lineinfile:
        path: /etc/default/grub
        backup: true
        backrefs: true
        regexp: '^GRUB_CMDLINE_LINUX_DEFAULT="(?!.*isolcpus=)(.*)"$'
        line: 'GRUB_CMDLINE_LINUX_DEFAULT="\\1 {{{{ trading_kernel_args }}}}"'
      when: ansible_facts['os_family'] == 'Debian'
      register: grub_defaults

    # Editing /etc/default/grub changes nothing until grub.cfg is regenerated.
    - name: Regenerate the bootloader configuration (Debian family)
      ansible.builtin.command:
        cmd: update-grub
      when: ansible_facts['os_family'] == 'Debian' and grub_defaults is changed
      changed_when: true

    - name: Apply sysctl network buffer tuning
      ansible.posix.sysctl:
        name: "{{{{ item.key }}}}"
        value: "{{{{ item.value }}}}"
        sysctl_set: true
        reload: true
        state: present
      loop:
        {sysctl_block}{ptp_task}

    # This playbook does not reboot: the host may be holding positions. The
    # assertion below turns "staged but never applied" into a loud failure
    # instead of a host that reports tuned and runs untuned.
    - name: Read the running kernel command line
      ansible.builtin.command:
        cmd: cat /proc/cmdline
      register: live_cmdline
      changed_when: false

    - name: Assert the low-latency kernel arguments are live
      ansible.builtin.assert:
        that:
          - "'isolcpus={spec.isolated_cpu_cores}' in live_cmdline.stdout"
        fail_msg: >-
          Kernel arguments are staged but not live on {spec.host_name}.
          Reboot the host outside trading hours and re-run before routing flow.
"""
        return playbook.strip()

    # --- audit --------------------------------------------------------------

    def _platform_warnings(self, spec: TradingHostSpec) -> List[str]:
        warnings: List[str] = []
        if not spec.instance_type.endswith(".metal"):
            warnings.append(
                f"instance_type '{spec.instance_type}' is not a bare-metal instance: AWS "
                "documents OS control of C-states and P-states for all Intel/AMD bare metal "
                "instances and an enumerated list of others; Graviton exposes none. Confirm "
                "the platform can honour this spec before treating an approval as effective."
            )
        if spec.hugepages_count > 0 and spec.hugepage_size_kb == 2048:
            warnings.append(
                "hugepages are sized from the default 2 MiB pool via vm.nr_hugepages "
                f"({spec.hugepages_count} pages = "
                f"{spec.hugepages_count * spec.hugepage_size_kb // 1024} MiB). Set "
                "hugepage_size_kb=1048576 for 1 GiB pages, which are reserved at boot."
            )
        return warnings

    def audit_and_generate_iac(self, spec: TradingHostSpec) -> IacTradingHostReport:
        """Audit a proposed host spec and, if it passes every check, render IaC.

        Fails closed: any violation suppresses artifact generation, and the
        returned ``status`` is the highest-priority violation while
        ``violations`` lists all of them, so an operator fixes the spec in one
        pass rather than one rejection at a time.
        """
        structural_errors = validate_spec(spec)

        is_gov_valid = isinstance(spec.cpu_governor, str) and \
            spec.cpu_governor.strip().lower() == REQUIRED_CPU_GOVERNOR
        is_cstate_valid = bool(spec.disable_cpu_cstates)
        is_buf_valid = (
            not structural_errors
            and spec.net_rmem_max_bytes >= self.min_socket_buffer_bytes
            and spec.net_wmem_max_bytes >= self.min_socket_buffer_bytes
        )
        is_ptp_valid = bool(spec.enable_ptp_clock_sync)

        isolation_errors: List[str] = []
        if not structural_errors:
            isolation_errors = _isolation_policy_errors(spec)
        is_iso_valid = not structural_errors and not isolation_errors

        # Ordered by priority: the first entry becomes `status`. Governor,
        # C-states and buffers keep their historical precedence.
        violations: List[str] = []
        detail: List[str] = []
        if structural_errors:
            violations.append(STATUS_INVALID_SPEC)
            detail.extend(structural_errors)
        if not is_gov_valid:
            violations.append(STATUS_BAD_GOVERNOR)
            detail.append(
                f"CPU governor {spec.cpu_governor!r} must be '{REQUIRED_CPU_GOVERNOR}'"
            )
        if not is_cstate_valid:
            violations.append(STATUS_CSTATES_ENABLED)
            detail.append("CPU C-states must be disabled (disable_cpu_cstates=True)")
        if not is_buf_valid and not structural_errors:
            violations.append(STATUS_SMALL_BUFFERS)
            detail.append(
                f"rmem_max ({spec.net_rmem_max_bytes} B) and wmem_max "
                f"({spec.net_wmem_max_bytes} B) must both be >= "
                f"{self.min_socket_buffer_bytes} B"
            )
        if not is_iso_valid and not structural_errors:
            violations.append(STATUS_NO_ISOLATION)
            detail.extend(isolation_errors)
        if not is_ptp_valid:
            violations.append(STATUS_PTP_DISABLED)
            detail.append(
                "PTP clock synchronisation must be enabled (ptp4l disciplines the "
                "PHC, phc2sys disciplines CLOCK_REALTIME)"
            )

        warnings = self._platform_warnings(spec) if not structural_errors else []

        if violations:
            status = violations[0]
            notes = f"IaC SPEC REJECTED [{spec.host_name!r}]: " + "; ".join(detail)
            logger.error(notes)
            tf_hcl = ""
            ansible_pb = ""
        else:
            status = STATUS_APPROVED
            notes = (
                f"IaC SPEC APPROVED [{spec.host_name}]: governor={spec.cpu_governor}, "
                f"isolcpus={spec.isolated_cpu_cores}, c_states_disabled=True, "
                f"rmem_max={spec.net_rmem_max_bytes} B, wmem_max={spec.net_wmem_max_bytes} B, "
                f"ptp_units={list(spec.ptp_service_units)}."
            )
            logger.info(notes)
            tf_hcl = self.generate_terraform_hcl(spec)
            ansible_pb = self.generate_ansible_playbook(spec)

        for warning in warnings:
            logger.warning("IaC SPEC WARNING [%s]: %s", spec.host_name, warning)

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
            audit_notes=notes,
            violations=violations,
            warnings=warnings,
        )
