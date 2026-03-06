import unittest
from unittest.mock import patch, MagicMock
from src.probe import run_probe, calculate_percentile
from src.config import JobConfig
from src import metrics # Used to verify they don't break

class TestProbe(unittest.TestCase):
    def setUp(self):
        self.job = JobConfig({
            "name": "test_job",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://orig",
            "artifacts": ["test.bin"],
            "repeat_count": 3,
            "warmup_runs": 1,
            "cooldown_seconds": 0
        })

    def test_calculate_percentile(self):
        data = [5, 1, 3, 2, 4] # sorted: 1, 2, 3, 4, 5
        self.assertEqual(calculate_percentile(data, 0.5), 3)
        self.assertEqual(calculate_percentile(data, 1.0), 5)
        self.assertEqual(calculate_percentile(data, 0.0), 1)

    def test_calculate_percentile_does_not_mutate_input(self):
        data = [5, 1, 3, 2, 4]
        original = data.copy()
        calculate_percentile(data, 0.95)
        self.assertEqual(data, original, "calculate_percentile must not mutate the input list")

    @patch('src.probe.download_artifact')
    def test_run_probe_orchestration(self, mock_download):
        mock_download.return_value = {
            "success": True,
            "error_class": None,
            "status_code": 200,
            "bytes_downloaded": 100,
            "ttfb": 0.1,
            "duration": 0.5,
            "throughput": 200
        }

        # Ensure no exception happens during lifecycle
        run_probe(self.job)

        # 1 warmup + 3 repeats = 4 total calls per path
        # 2 paths (edge + origin) = 8 total calls to download_artifact
        self.assertEqual(mock_download.call_count, 8)

if __name__ == '__main__':
    unittest.main()
