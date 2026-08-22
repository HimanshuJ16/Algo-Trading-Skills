import unittest
from capacity_planning_for_symbol_universe_growth import (
    UDP_IPV4_ETHERNET_OVERHEAD_BYTES,
    CapacityPlanner,
    HardwareSpec,
    UniverseSpec,
    peak_rate_per_sec_from_burst,
)


class TestCapacityPlanner(unittest.TestCase):

    def setUp(self):
        # 10 GbE NIC, JSON parsing (100k msgs/sec/core), 128GB RAM
        self.hw = HardwareSpec(
            nic_bandwidth_mbps=10000.0,
            cpu_msgs_per_sec_per_core=100_000,
            available_ram_gb=128.0
        )
        self.planner = CapacityPlanner(self.hw)

    def test_small_universe(self):
        # 100 symbols, 500 msgs/sec each (50k msgs/sec total)
        # 200 bytes per msg -> 10 MB/s -> 80 Mbps
        universe = UniverseSpec(
            num_symbols=100,
            peak_ticks_per_sec_per_symbol=500,
            bytes_per_msg=200,
            state_memory_mb_per_symbol=10.0 # 1GB total
        )
        report = self.planner.evaluate(universe)

        self.assertTrue(report.is_viable)
        self.assertEqual(report.total_peak_msgs_per_sec, 50_000)
        self.assertAlmostEqual(report.required_network_mbps, 80.0)
        self.assertEqual(report.required_cpu_cores, 1)
        # 100 x 10 MiB = 1000 MiB = 0.9765625 GiB (binary units).
        self.assertAlmostEqual(report.required_ram_gb, 0.9765625)

    def test_network_bottleneck_massive_universe(self):
        # 10,000 symbols, 1,000 msgs/sec each (10M msgs/sec total)
        # 200 bytes per msg -> 2 GB/s -> 16,000 Mbps
        # This will crush a 10GbE link
        universe = UniverseSpec(
            num_symbols=10_000,
            peak_ticks_per_sec_per_symbol=1000,
            bytes_per_msg=200,
            state_memory_mb_per_symbol=10.0
        )
        report = self.planner.evaluate(universe)

        self.assertFalse(report.is_viable)
        self.assertTrue(report.network_bottleneck)
        self.assertEqual(report.required_network_mbps, 16000.0)

        # 10M msgs / 100k per core = 100 cores -> CPU bottleneck too
        self.assertTrue(report.cpu_bottleneck)
        self.assertEqual(report.required_cpu_cores, 100)

    # --- CPU headroom is taken from HardwareSpec, not a hard-coded constant -----------

    def test_cpu_bottleneck_respects_configured_core_count(self):
        """A 16-core box must be flagged at 20 required cores, not silently passed."""
        small_box = HardwareSpec(
            nic_bandwidth_mbps=10000.0,
            cpu_msgs_per_sec_per_core=100_000,
            available_ram_gb=128.0,
            available_cpu_cores=16,
        )
        planner = CapacityPlanner(small_box)
        # 2000 symbols x 1000 msgs/sec = 2M msgs/sec -> 20 cores.
        universe = UniverseSpec(
            num_symbols=2000,
            peak_ticks_per_sec_per_symbol=1000,
            bytes_per_msg=50,
            state_memory_mb_per_symbol=1.0,
        )
        report = planner.evaluate(universe)
        self.assertEqual(report.required_cpu_cores, 20)
        self.assertTrue(report.cpu_bottleneck)
        self.assertFalse(report.is_viable)

        # The same universe on a 64-core box is within CPU budget.
        big_box = HardwareSpec(
            nic_bandwidth_mbps=10000.0,
            cpu_msgs_per_sec_per_core=100_000,
            available_ram_gb=128.0,
            available_cpu_cores=64,
        )
        self.assertFalse(CapacityPlanner(big_box).evaluate(universe).cpu_bottleneck)

    def test_core_count_boundary_is_inclusive(self):
        hw = HardwareSpec(nic_bandwidth_mbps=10000.0, cpu_msgs_per_sec_per_core=100_000,
                          available_ram_gb=128.0, available_cpu_cores=10)
        planner = CapacityPlanner(hw)
        # Exactly 1M msgs/sec = exactly 10 cores: allowed.
        exact = UniverseSpec(num_symbols=1000, peak_ticks_per_sec_per_symbol=1000,
                             bytes_per_msg=50, state_memory_mb_per_symbol=1.0)
        self.assertEqual(planner.evaluate(exact).required_cpu_cores, 10)
        self.assertFalse(planner.evaluate(exact).cpu_bottleneck)
        # One more symbol pushes it to 11 cores.
        over = UniverseSpec(num_symbols=1001, peak_ticks_per_sec_per_symbol=1000,
                            bytes_per_msg=50, state_memory_mb_per_symbol=1.0)
        self.assertEqual(planner.evaluate(over).required_cpu_cores, 11)
        self.assertTrue(planner.evaluate(over).cpu_bottleneck)

    def test_single_hot_symbol_cannot_be_scaled_by_adding_cores(self):
        """
        Under symbol partitioning one symbol's stream cannot span cores, so a symbol
        hotter than a single core is infeasible however many cores exist.
        """
        hw = HardwareSpec(nic_bandwidth_mbps=100_000.0, cpu_msgs_per_sec_per_core=100_000,
                          available_ram_gb=1024.0, available_cpu_cores=256)
        planner = CapacityPlanner(hw)
        universe = UniverseSpec(
            num_symbols=2,
            peak_ticks_per_sec_per_symbol=150_000,  # > 100k per core
            bytes_per_msg=50,
            state_memory_mb_per_symbol=1.0,
        )
        report = planner.evaluate(universe)
        self.assertTrue(report.single_symbol_exceeds_core)
        self.assertTrue(report.cpu_bottleneck)
        self.assertFalse(report.is_viable)
        # Only 3 cores are nominally required - the raw core count hides the problem.
        self.assertEqual(report.required_cpu_cores, 3)

    # --- Packet batching --------------------------------------------------------------

    def test_batching_reduces_framing_cost(self):
        """
        Charging framing per packet rather than per message is the difference between
        an accurate figure and a several-fold overestimate on a batched feed.
        """
        # Unbatched: 50-byte payload + 66 bytes framing charged to every message.
        unbatched = UniverseSpec(
            num_symbols=1000, peak_ticks_per_sec_per_symbol=1000,
            bytes_per_msg=50, state_memory_mb_per_symbol=1.0,
            packet_overhead_bytes=UDP_IPV4_ETHERNET_OVERHEAD_BYTES, msgs_per_packet=1)
        # 1M msgs/sec x (50 + 66) = 116 MB/s -> 928 Mbps.
        self.assertAlmostEqual(
            self.planner.evaluate(unbatched).required_network_mbps, 928.0)

        # Batched 20 messages per packet: 1M x 50 + 50k packets x 66
        # = 50,000,000 + 3,300,000 = 53.3 MB/s -> 426.4 Mbps.
        batched = UniverseSpec(
            num_symbols=1000, peak_ticks_per_sec_per_symbol=1000,
            bytes_per_msg=50, state_memory_mb_per_symbol=1.0,
            packet_overhead_bytes=UDP_IPV4_ETHERNET_OVERHEAD_BYTES, msgs_per_packet=20)
        self.assertAlmostEqual(
            self.planner.evaluate(batched).required_network_mbps, 426.4)

    def test_implausible_batching_factor_is_warned_about(self):
        """Over-stating batching under-states bandwidth, so it must not pass silently."""
        universe = UniverseSpec(
            num_symbols=10, peak_ticks_per_sec_per_symbol=100,
            bytes_per_msg=200, state_memory_mb_per_symbol=1.0,
            msgs_per_packet=100)  # 20,000 bytes: impossible on a 1500-byte MTU
        with self.assertLogs(
                "capacity_planning_for_symbol_universe_growth", level="WARNING") as logs:
            self.planner.evaluate(universe)
        self.assertTrue(any("MTU" in line for line in logs.output))

    def test_udp_framing_constant_is_the_documented_wire_cost(self):
        # 38 bytes L1/L2 + 20 IPv4 + 8 UDP.
        self.assertEqual(UDP_IPV4_ETHERNET_OVERHEAD_BYTES, 66)

    # --- Redundant feeds and retransmission -------------------------------------------

    def test_redundant_ab_feeds_double_bandwidth(self):
        single = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                              bytes_per_msg=200, state_memory_mb_per_symbol=1.0)
        dual = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                            bytes_per_msg=200, state_memory_mb_per_symbol=1.0,
                            redundant_feeds=2)
        self.assertAlmostEqual(self.planner.evaluate(single).required_network_mbps, 80.0)
        self.assertAlmostEqual(self.planner.evaluate(dual).required_network_mbps, 160.0)

    def test_retransmission_overhead_inflates_bandwidth(self):
        universe = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                                bytes_per_msg=200, state_memory_mb_per_symbol=1.0,
                                retransmission_overhead_fraction=0.10)
        # 80 Mbps + 10%.
        self.assertAlmostEqual(
            self.planner.evaluate(universe).required_network_mbps, 88.0)

    def test_redundancy_and_retransmission_compose(self):
        universe = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                                bytes_per_msg=200, state_memory_mb_per_symbol=1.0,
                                retransmission_overhead_fraction=0.10, redundant_feeds=2)
        # 80 x 1.10 x 2 = 176 Mbps.
        self.assertAlmostEqual(
            self.planner.evaluate(universe).required_network_mbps, 176.0)

    # --- Safety margin ----------------------------------------------------------------

    def test_safety_margin_scales_rate_but_not_ram(self):
        planner = CapacityPlanner(self.hw, safety_margin=2.0)
        universe = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                                bytes_per_msg=200, state_memory_mb_per_symbol=10.0)
        report = planner.evaluate(universe)
        self.assertEqual(report.total_peak_msgs_per_sec, 100_000)
        self.assertAlmostEqual(report.required_network_mbps, 160.0)
        # RAM is driven by symbol count, not tick rate, so it is unchanged.
        self.assertAlmostEqual(report.required_ram_gb, 0.9765625)

    def test_default_safety_margin_does_not_inflate(self):
        universe = UniverseSpec(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                                bytes_per_msg=200, state_memory_mb_per_symbol=10.0)
        self.assertEqual(self.planner.evaluate(universe).total_peak_msgs_per_sec, 50_000)

    def test_safety_margin_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            CapacityPlanner(self.hw, safety_margin=0.9)

    # --- Network utilisation cap ------------------------------------------------------

    def test_utilisation_cap_is_configurable_and_reported(self):
        planner = CapacityPlanner(self.hw, max_network_utilization=0.80)
        self.assertAlmostEqual(planner.max_safe_network_mbps, 8000.0)
        universe = UniverseSpec(num_symbols=10_000, peak_ticks_per_sec_per_symbol=875,
                                bytes_per_msg=100, state_memory_mb_per_symbol=0.1)
        # 8.75M msgs/sec x 100 bytes = 875 MB/s -> 7000 Mbps: under 80%, over 60%.
        report = planner.evaluate(universe)
        self.assertAlmostEqual(report.required_network_mbps, 7000.0)
        self.assertFalse(report.network_bottleneck)
        self.assertAlmostEqual(report.max_safe_network_mbps, 8000.0)
        self.assertTrue(CapacityPlanner(self.hw).evaluate(universe).network_bottleneck)

    def test_utilisation_above_one_is_rejected(self):
        with self.assertRaises(ValueError):
            CapacityPlanner(self.hw, max_network_utilization=1.5)

    # --- Input validation -------------------------------------------------------------

    def test_zero_core_throughput_is_rejected_not_divided_by(self):
        with self.assertRaises(ValueError):
            HardwareSpec(nic_bandwidth_mbps=10000.0, cpu_msgs_per_sec_per_core=0,
                         available_ram_gb=128.0)

    def test_invalid_hardware_values_are_rejected(self):
        bad = [
            dict(nic_bandwidth_mbps=0.0),
            dict(nic_bandwidth_mbps=-1.0),
            dict(nic_bandwidth_mbps=float("nan")),
            dict(available_ram_gb=0.0),
            dict(available_cpu_cores=0),
            dict(cpu_msgs_per_sec_per_core=-5),
        ]
        for overrides in bad:
            kwargs = dict(nic_bandwidth_mbps=10000.0, cpu_msgs_per_sec_per_core=100_000,
                          available_ram_gb=128.0)
            kwargs.update(overrides)
            with self.subTest(**overrides):
                with self.assertRaises(ValueError):
                    HardwareSpec(**kwargs)

    def test_invalid_universe_values_are_rejected(self):
        bad = [
            dict(num_symbols=0),
            dict(num_symbols=-10),
            dict(peak_ticks_per_sec_per_symbol=0),
            dict(bytes_per_msg=0),
            dict(state_memory_mb_per_symbol=float("nan")),
            dict(state_memory_mb_per_symbol=-1.0),
            dict(msgs_per_packet=0),
            dict(packet_overhead_bytes=-1),
            dict(redundant_feeds=0),
            dict(retransmission_overhead_fraction=-0.1),
        ]
        for overrides in bad:
            kwargs = dict(num_symbols=100, peak_ticks_per_sec_per_symbol=500,
                          bytes_per_msg=200, state_memory_mb_per_symbol=1.0)
            kwargs.update(overrides)
            with self.subTest(**overrides):
                with self.assertRaises(ValueError):
                    UniverseSpec(**kwargs)

    def test_bool_is_not_accepted_as_a_count(self):
        with self.assertRaises(ValueError):
            UniverseSpec(num_symbols=True, peak_ticks_per_sec_per_symbol=500,
                         bytes_per_msg=200, state_memory_mb_per_symbol=1.0)

    # --- RAM ---------------------------------------------------------------------------

    def test_ram_bottleneck_uses_binary_units(self):
        hw = HardwareSpec(nic_bandwidth_mbps=10000.0, cpu_msgs_per_sec_per_core=100_000,
                          available_ram_gb=1.0)
        planner = CapacityPlanner(hw)
        # 1024 symbols x 1 MiB = 1024 MiB = exactly 1.0 GiB: at the limit, not over it.
        at_limit = UniverseSpec(num_symbols=1024, peak_ticks_per_sec_per_symbol=1,
                                bytes_per_msg=100, state_memory_mb_per_symbol=1.0)
        report = planner.evaluate(at_limit)
        self.assertAlmostEqual(report.required_ram_gb, 1.0)
        self.assertFalse(report.ram_bottleneck)
        # One more symbol exceeds it.
        over = UniverseSpec(num_symbols=1025, peak_ticks_per_sec_per_symbol=1,
                            bytes_per_msg=100, state_memory_mb_per_symbol=1.0)
        self.assertTrue(planner.evaluate(over).ram_bottleneck)

    # --- Burst window helper ------------------------------------------------------------

    def test_peak_rate_from_burst_window(self):
        # 40,000 messages in a 10ms window is a 4M msgs/sec rate.
        self.assertAlmostEqual(peak_rate_per_sec_from_burst(40_000, 10.0), 4_000_000.0)
        # A 1-second window returns the count unchanged.
        self.assertAlmostEqual(peak_rate_per_sec_from_burst(40_000, 1000.0), 40_000.0)

    def test_peak_rate_from_burst_rejects_bad_window(self):
        with self.assertRaises(ValueError):
            peak_rate_per_sec_from_burst(100, 0.0)
        with self.assertRaises(ValueError):
            peak_rate_per_sec_from_burst(0, 10.0)

    # --- Documented worked example ------------------------------------------------------

    def test_skill_md_verification_example_is_reproducible(self):
        """The example quoted in SKILL.md must actually produce the stated figures."""
        hw = HardwareSpec(nic_bandwidth_mbps=1000.0, cpu_msgs_per_sec_per_core=100_000,
                          available_ram_gb=128.0, available_cpu_cores=64)
        planner = CapacityPlanner(hw)
        universe = UniverseSpec(num_symbols=5000, peak_ticks_per_sec_per_symbol=1000,
                                bytes_per_msg=125, state_memory_mb_per_symbol=1.0)
        report = planner.evaluate(universe)
        # 5M msgs/sec x 125 bytes x 8 = 5,000 Mbps against a 1 Gbps link.
        self.assertEqual(report.total_peak_msgs_per_sec, 5_000_000)
        self.assertAlmostEqual(report.required_network_mbps, 5000.0)
        self.assertTrue(report.network_bottleneck)
        self.assertFalse(report.is_viable)


if __name__ == '__main__':
    unittest.main()
