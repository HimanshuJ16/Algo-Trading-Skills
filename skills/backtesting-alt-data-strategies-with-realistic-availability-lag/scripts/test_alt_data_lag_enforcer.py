import unittest
import pandas as pd
from alt_data_lag_enforcer import AltDataLagEnforcer

class TestAltDataLagEnforcer(unittest.TestCase):

    def setUp(self):
        # Mock alternative data (e.g., credit card transaction volume)
        data = {
            "event_date": ["2023-11-24", "2023-11-25", "2023-11-26"],
            "cc_volume": [1500, 1600, 1200]
        }
        self.df_no_pub = pd.DataFrame(data)
        
        data_with_pub = {
            "event_date": ["2023-11-24", "2023-11-25", "2023-11-26"],
            "pub_date": ["2023-11-26", "2023-11-28", "2023-11-27"], # Out of order publication
            "cc_volume": [1500, 1600, 1200]
        }
        self.df_pub = pd.DataFrame(data_with_pub)

    def test_default_lag_enforcement(self):
        # Default lag is 3 days
        enforcer = AltDataLagEnforcer(self.df_no_pub, default_lag_days=3)
        
        # As of Nov 26: Nov 24 + 3 days = Nov 27. Therefore NO data is available yet.
        df_pit = enforcer.get_point_in_time_data("2023-11-26")
        self.assertEqual(len(df_pit), 0)
        
        # As of Nov 27: Nov 24 data becomes available.
        df_pit = enforcer.get_point_in_time_data("2023-11-27")
        self.assertEqual(len(df_pit), 1)
        self.assertEqual(df_pit.iloc[0]["event_date"], pd.Timestamp("2023-11-24"))
        
        # As of Nov 29: All data available.
        df_pit = enforcer.get_point_in_time_data("2023-11-29")
        self.assertEqual(len(df_pit), 3)

    def test_explicit_publication_date_enforcement(self):
        enforcer = AltDataLagEnforcer(self.df_pub, publication_date_col="pub_date")
        
        # As of Nov 26: Only Nov 24 data is published.
        df_pit = enforcer.get_point_in_time_data("2023-11-26")
        self.assertEqual(len(df_pit), 1)
        self.assertEqual(df_pit.iloc[0]["event_date"], pd.Timestamp("2023-11-24"))
        
        # As of Nov 27: Nov 26 data is published early! Nov 25 is still unpublished.
        df_pit = enforcer.get_point_in_time_data("2023-11-27")
        self.assertEqual(len(df_pit), 2)
        # Check that it returns Nov 24 and Nov 26 (sorted by event_date)
        self.assertEqual(df_pit.iloc[1]["event_date"], pd.Timestamp("2023-11-26"))

if __name__ == '__main__':
    unittest.main()
