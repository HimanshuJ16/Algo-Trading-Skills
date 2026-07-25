import unittest
from arrival_price_benchmark_execution_algo import (
    ArrivalPriceTrajectoryGenerator,
    UrgencyLevel
)

class TestArrivalPriceTrajectoryGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = ArrivalPriceTrajectoryGenerator()
        self.total_size = 10000
        self.num_bins = 10

    def test_low_urgency_trajectory(self):
        # Low urgency should approximate TWAP (uniform distribution)
        trajectory = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.LOW)
        
        self.assertEqual(sum(trajectory.child_order_sizes), self.total_size)
        self.assertEqual(len(trajectory.child_order_sizes), self.num_bins)
        
        # In a uniform distribution of 10000 over 10 bins, each bin should be exactly 1000
        for size in trajectory.child_order_sizes:
            self.assertEqual(size, 1000)

    def test_high_urgency_front_loading(self):
        # High urgency should heavily front-load the execution
        trajectory = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.HIGH)
        
        self.assertEqual(sum(trajectory.child_order_sizes), self.total_size)
        
        # The first half of the bins should contain significantly more shares than the second half
        first_half_sum = sum(trajectory.child_order_sizes[:5])
        second_half_sum = sum(trajectory.child_order_sizes[5:])
        
        self.assertGreater(first_half_sum, second_half_sum * 2) # Heavily front-loaded
        
        # Specifically, the first bin should be the largest
        self.assertEqual(trajectory.child_order_sizes[0], max(trajectory.child_order_sizes))

    def test_medium_urgency_curve(self):
        trajectory = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.MEDIUM)
        self.assertEqual(sum(trajectory.child_order_sizes), self.total_size)
        
        # Medium should be front-loaded, but less extreme than High
        high_traj = self.generator.generate_schedule(self.total_size, self.num_bins, UrgencyLevel.HIGH)
        
        self.assertLess(trajectory.child_order_sizes[0], high_traj.child_order_sizes[0])

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(-500, 10, UrgencyLevel.MEDIUM)
            
        with self.assertRaises(ValueError):
            self.generator.generate_schedule(1000, 0, UrgencyLevel.MEDIUM)

if __name__ == '__main__':
    unittest.main()
