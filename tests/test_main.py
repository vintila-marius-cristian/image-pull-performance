import unittest
import threading
from io import BytesIO
from unittest.mock import MagicMock
from main import HealthHandler, _probe_threads


class FakeRequest:
    """Minimal fake request for BaseHTTPRequestHandler."""
    def __init__(self, path):
        self.path = path
        self._data = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()

    def makefile(self, mode, buffering=-1):
        return BytesIO(self._data)


class TestHealthHandler(unittest.TestCase):
    def _make_handler(self, path):
        """Create a HealthHandler and simulate a GET request."""
        handler = HealthHandler.__new__(HealthHandler)
        handler.path = path
        handler.headers = {}
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"

        # Capture response
        handler.wfile = BytesIO()
        handler._headers_buffer = []
        handler.responses = HealthHandler.responses

        # Track response code
        handler._response_code = None
        original_send_response = HealthHandler.send_response

        def capture_send_response(self, code, message=None):
            self._response_code = code

        handler.send_response = lambda code, message=None: capture_send_response(handler, code)
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None

        handler.do_GET()
        return handler

    def test_healthz_returns_200(self):
        handler = self._make_handler('/healthz')
        self.assertEqual(handler._response_code, 200)
        self.assertEqual(handler.wfile.getvalue(), b"OK")

    def test_unknown_path_returns_404(self):
        handler = self._make_handler('/unknown')
        self.assertEqual(handler._response_code, 404)

    def test_readyz_returns_503_when_no_threads(self):
        _probe_threads.clear()
        handler = self._make_handler('/readyz')
        self.assertEqual(handler._response_code, 503)
        self.assertIn(b"No probe threads alive", handler.wfile.getvalue())

    def test_readyz_returns_200_when_threads_alive(self):
        _probe_threads.clear()
        t = threading.Thread(target=lambda: threading.Event().wait(timeout=5), daemon=True)
        t.start()
        _probe_threads.append(t)
        try:
            handler = self._make_handler('/readyz')
            self.assertEqual(handler._response_code, 200)
            self.assertIn(b"1/1 probe threads alive", handler.wfile.getvalue())
        finally:
            _probe_threads.clear()

    def test_readyz_returns_503_when_all_threads_dead(self):
        _probe_threads.clear()
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        t.join()  # Wait for thread to finish (die)
        _probe_threads.append(t)
        try:
            handler = self._make_handler('/readyz')
            self.assertEqual(handler._response_code, 503)
        finally:
            _probe_threads.clear()


if __name__ == '__main__':
    unittest.main()
