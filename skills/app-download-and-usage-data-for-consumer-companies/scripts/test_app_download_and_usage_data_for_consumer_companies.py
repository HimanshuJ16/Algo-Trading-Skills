import unittest
from app_download_and_usage_data_for_consumer_companies import AppDownloadAndUsageDataForConsumerCompanies, AppDownloadAndUsageDataForConsumerCompaniesConfig

class TestAppDownloadAndUsageDataForConsumerCompanies(unittest.TestCase):
    def test_init(self):
        config = AppDownloadAndUsageDataForConsumerCompaniesConfig()
        obj = AppDownloadAndUsageDataForConsumerCompanies(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = AppDownloadAndUsageDataForConsumerCompaniesConfig()
        obj = AppDownloadAndUsageDataForConsumerCompanies(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
