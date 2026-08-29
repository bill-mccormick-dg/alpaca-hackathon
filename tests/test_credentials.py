import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import credentials


class LoadCredentialsTest(unittest.TestCase):
    def setUp(self):
        env_patch = mock.patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        tmp = Path(tmpdir.name)
        self._prod_dir = tmp / "prod"
        self._prod_dir.mkdir()
        self._dotenv_file = tmp / ".env"

        prod_patch = mock.patch.object(credentials, "PRODUCTION_CREDENTIALS_DIR", self._prod_dir)
        prod_patch.start()
        self.addCleanup(prod_patch.stop)

        dotenv_patch = mock.patch.object(credentials, "DOTENV_FILE", self._dotenv_file)
        dotenv_patch.start()
        self.addCleanup(dotenv_patch.stop)

    def _write_prod_file(self, name, **overrides):
        values = {
            "ALPACA_API_KEY": "file-key",
            "ALPACA_SECRET_KEY": "file-secret",
            "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
            "FEATHERLESS_API_KEY": "file-featherless",
        }
        values.update(overrides)
        content = "\n".join(f"{k}={v}" for k, v in values.items())
        (self._prod_dir / name).write_text(content + "\n")

    def test_rejects_malformed_account_names(self):
        for bad in ("", "Live Account", "../etc", "OFFICIAL", "x" * 40):
            with self.assertRaises(ValueError):
                credentials.load_credentials(bad)

    def test_named_account_resolves_from_its_own_deployed_file(self):
        self._write_prod_file("credentials-qwen-a.env", ALPACA_API_KEY="qwen-key")
        self._write_prod_file("credentials-test.env", ALPACA_API_KEY="test-key")
        self.assertEqual(credentials.load_credentials("qwen-a")["ALPACA_API_KEY"], "qwen-key")

    def test_named_account_prefers_dotenv_name_over_plain_dotenv(self):
        body = (
            "ALPACA_SECRET_KEY=s\nALPACA_BASE_URL=https://paper-api.alpaca.markets\nFEATHERLESS_API_KEY=f\n"
        )
        self._dotenv_file.write_text("ALPACA_API_KEY=plain\n" + body)
        self._dotenv_file.with_name(".env.qwen-a").write_text("ALPACA_API_KEY=named\n" + body)
        self.assertEqual(credentials.load_credentials("qwen-a")["ALPACA_API_KEY"], "named")
        self.assertEqual(credentials.load_credentials("other")["ALPACA_API_KEY"], "plain")

    def test_credentials_file_paths(self):
        self.assertEqual(credentials.credentials_file("official").name, "credentials.env")
        self.assertEqual(credentials.credentials_file("qwen-a").name, "credentials-qwen-a.env")

    def test_environment_variables_take_priority_over_file(self):
        os.environ.update(
            {
                "ALPACA_API_KEY": "env-key",
                "ALPACA_SECRET_KEY": "env-secret",
                "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
                "FEATHERLESS_API_KEY": "env-featherless",
            }
        )
        self._write_prod_file("credentials-test.env", ALPACA_API_KEY="file-key")

        result = credentials.load_credentials("test")

        self.assertEqual(result["ALPACA_API_KEY"], "env-key")

    def test_falls_back_to_matching_production_file(self):
        self._write_prod_file("credentials-test.env", ALPACA_API_KEY="test-key")
        self._write_prod_file("credentials.env", ALPACA_API_KEY="official-key")

        test_result = credentials.load_credentials("test")
        official_result = credentials.load_credentials("official")

        self.assertEqual(test_result["ALPACA_API_KEY"], "test-key")
        self.assertEqual(official_result["ALPACA_API_KEY"], "official-key")

    def test_falls_back_to_local_dotenv_for_test_account_only(self):
        self._dotenv_file.write_text(
            "ALPACA_API_KEY=dev-key\n"
            "ALPACA_SECRET_KEY=dev-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=dev-featherless\n"
        )

        result = credentials.load_credentials("test")

        self.assertEqual(result["ALPACA_API_KEY"], "dev-key")

    def test_official_account_never_falls_back_to_dotenv(self):
        self._dotenv_file.write_text(
            "ALPACA_API_KEY=dev-key\n"
            "ALPACA_SECRET_KEY=dev-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=dev-featherless\n"
        )

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("official")

        self.assertIn("ALPACA_API_KEY", str(ctx.exception))

    def test_partial_env_falls_through_to_file_for_missing_keys(self):
        os.environ["ALPACA_API_KEY"] = "env-key"
        self._write_prod_file("credentials-test.env")

        result = credentials.load_credentials("test")

        self.assertEqual(result["ALPACA_API_KEY"], "env-key")
        self.assertEqual(result["ALPACA_SECRET_KEY"], "file-secret")

    def test_raises_with_missing_key_names_and_account_when_nothing_resolves(self):
        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        message = str(ctx.exception)
        self.assertIn("ALPACA_API_KEY", message)
        self.assertIn("FEATHERLESS_API_KEY", message)
        self.assertIn("'test'", message)


if __name__ == "__main__":
    unittest.main()
