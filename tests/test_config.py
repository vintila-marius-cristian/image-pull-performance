import os
import unittest
from src.config import JobConfig, load_config


class TestSecretResolution(unittest.TestCase):
    def _make_job(self, **kwargs):
        base = {
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://origin",
            "artifacts": ["a.bin"],
        }
        base.update(kwargs)
        return JobConfig(base)

    def test_env_prefix_resolves_env_var(self):
        os.environ["TEST_SECRET"] = "mytoken"
        job = self._make_job(password="env:TEST_SECRET")
        self.assertEqual(job.password, "mytoken")
        del os.environ["TEST_SECRET"]

    def test_env_prefix_with_colon_in_var_name_does_not_break(self):
        os.environ["MY_TOKEN"] = "secret"
        job = self._make_job(password="env:MY_TOKEN")
        self.assertEqual(job.password, "secret")
        del os.environ["MY_TOKEN"]

    def test_file_prefix_resolves_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("  filetoken  \n")
            path = f.name
        job = self._make_job(password=f"file:{path}")
        self.assertEqual(job.password, "filetoken")
        os.unlink(path)

    def test_literal_value_returned_as_is(self):
        job = self._make_job(password="plaintext")
        self.assertEqual(job.password, "plaintext")

    def test_none_value_returns_none(self):
        job = self._make_job()
        self.assertIsNone(job.password)


class TestJobConfigValidation(unittest.TestCase):
    def _make_job(self, **kwargs):
        base = {
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://origin",
            "artifacts": ["a.bin"],
        }
        base.update(kwargs)
        return JobConfig(base)

    def test_missing_edge_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(edge_url_base=None)
        self.assertIn("edge_url_base", str(ctx.exception))

    def test_missing_origin_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(origin_url_base=None)
        self.assertIn("origin_url_base", str(ctx.exception))

    def test_empty_artifacts_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(artifacts=[])
        self.assertIn("artifacts", str(ctx.exception))

    def test_repeat_count_zero_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(repeat_count=0)
        self.assertIn("repeat_count", str(ctx.exception))

    def test_repeat_count_negative_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(repeat_count=-1)
        self.assertIn("repeat_count", str(ctx.exception))

    def test_valid_config_does_not_raise(self):
        job = self._make_job()  # should not raise
        self.assertEqual(job.name, "test")


if __name__ == "__main__":
    unittest.main()
