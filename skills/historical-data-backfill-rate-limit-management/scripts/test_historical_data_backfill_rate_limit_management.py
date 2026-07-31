import unittest
from historical_data_backfill_rate_limit_management import (
    HistoricalBackfillManagerEngine, BackfillChunk, BackfillExecutionReport
)

class TestHistoricalBackfillManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalBackfillManagerEngine(
            requests_per_minute=60, max_burst_capacity=5, max_retries=3
        )
        self.chunks = [
            BackfillChunk(f"CHK_{i:02d}", f"2026-{i:02d}-01", f"2026-{i:02d}-28")
            for i in range(1, 6)
        ]

    def test_backfill_success_without_throttling(self):
        def fetch_func(chunk):
            return True, False, 1000 # Success, No 429, 1000 records

        report = self.engine.execute_backfill("AAPL", self.chunks, fetch_func)

        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertEqual(report.completed_chunks_count, 5)
        self.assertEqual(report.failed_chunks_count, 0)
        self.assertEqual(report.total_records_ingested, 5000)
        self.assertEqual(report.total_rate_limit_throttles, 0)

    def test_backfill_handles_http_429_exponential_backoff(self):
        # Inject 1 HTTP 429 error on first attempt for each chunk
        attempts = {}

        def fetch_func(chunk):
            c_id = chunk.chunk_id
            attempts[c_id] = attempts.get(c_id, 0) + 1
            if attempts[c_id] == 1:
                return False, True, 0 # Failed with HTTP 429
            return True, False, 1000 # Success on 2nd attempt

        report = self.engine.execute_backfill("AAPL", self.chunks, fetch_func)

        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertEqual(report.completed_chunks_count, 5)
        self.assertEqual(report.total_rate_limit_throttles, 5)

if __name__ == '__main__':
    unittest.main()
