import unittest
from unittest.mock import patch, MagicMock
import requests
from src.config import JobConfig
from src.client import download_artifact

class TestClient(unittest.TestCase):
    def setUp(self):
        self.job = JobConfig({
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://orig",
            "artifacts": ["test.bin"],
            "timeout": 5,
            "cache_busting": True
        })

    @patch('src.client.requests.get')
    def test_download_artifact_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_get.return_value.__enter__.return_value = mock_resp

        res = download_artifact("http://edge", "test.bin", self.job, "edge")
        
        self.assertTrue(res['success'])
        self.assertEqual(res['status_code'], 200)
        self.assertEqual(res['bytes_downloaded'], 12)
        self.assertTrue(res['ttfb'] > 0)
        self.assertTrue(res['duration'] >= res['ttfb'])
        
        # Verify cache busting triggered
        called_url = mock_get.call_args[0][0]
        self.assertIn('nocache=', called_url)
        
        headers = mock_get.call_args[1]['headers']
        self.assertIn('Cache-Control', headers)
        self.assertEqual(headers['Cache-Control'], 'no-cache, no-store, must-revalidate')
        
    @patch('src.client.requests.get')
    def test_download_artifact_no_cache_busting(self, mock_get):
        self.job.cache_busting = False
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"chunk1"]
        mock_get.return_value.__enter__.return_value = mock_resp

        res = download_artifact("http://edge", "test.bin", self.job, "edge")
        
        self.assertTrue(res['success'])
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, "http://edge/test.bin")
        self.assertNotIn('Cache-Control', mock_get.call_args[1]['headers'])

    @patch('src.client.requests.get')
    def test_download_artifact_timeout(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")

        res = download_artifact("http://edge", "test.bin", self.job, "edge")

        self.assertFalse(res['success'])
        self.assertEqual(res['error_class'], 'timeout')
        self.assertEqual(res['bytes_downloaded'], 0)
        self.assertGreater(res['duration'], 0)
        self.assertEqual(res['ttfb'], res['duration'])

    @patch('src.client.requests.get')
    def test_download_artifact_connection_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        res = download_artifact("http://edge", "test.bin", self.job, "edge")

        self.assertFalse(res['success'])
        self.assertEqual(res['error_class'], 'connection_error')

    @patch('src.client.requests.get')
    def test_download_artifact_http_error_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_resp
        )
        mock_get.return_value.__enter__.return_value = mock_resp

        res = download_artifact("http://edge", "test.bin", self.job, "edge")

        self.assertFalse(res['success'])
        self.assertEqual(res['error_class'], 'http_error')
        self.assertEqual(res['status_code'], 404)

    @patch('src.client.requests.get')
    def test_bearer_auth_header_set(self, mock_get):
        self.job.auth_method = 'bearer'
        self.job.password = 'mytoken'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"data"]
        mock_get.return_value.__enter__.return_value = mock_resp

        download_artifact("http://edge", "test.bin", self.job, "edge")

        headers = mock_get.call_args[1]['headers']
        self.assertEqual(headers['Authorization'], 'Bearer mytoken')

    @patch('src.client.requests.get')
    def test_basic_auth_tuple_set(self, mock_get):
        self.job.auth_method = 'basic'
        self.job.username = 'user'
        self.job.password = 'pass'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"data"]
        mock_get.return_value.__enter__.return_value = mock_resp

        download_artifact("http://edge", "test.bin", self.job, "edge")

        auth = mock_get.call_args[1]['auth']
        self.assertEqual(auth, ('user', 'pass'))

    @patch('src.client.requests.get')
    def test_max_bytes_sets_range_header(self, mock_get):
        self.job.max_bytes = 1024
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"x" * 100]
        mock_get.return_value.__enter__.return_value = mock_resp

        download_artifact("http://edge", "test.bin", self.job, "edge")

        headers = mock_get.call_args[1]['headers']
        self.assertEqual(headers['Range'], 'bytes=0-1023')

if __name__ == '__main__':
    unittest.main()
