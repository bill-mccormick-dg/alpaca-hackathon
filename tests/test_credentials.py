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
        self._prod_file = tmp / "credentials.env"
        self._dotenv_file = tmp / ".env"

        prod_patch = mock.patch.object(credentials, "PRODUCTION_CREDENTIALS_FILE", self._prod_file)
        prod_patch.start()
        self.addCleanup(prod_patch.stop)

        dotenv_patch = mock.patch.object(credentials, "DOTENV_FILE", self._dotenv_file)
        dotenv_patch.start()
        self.addCleanup(dotenv_patch.stop)

    def test_environment_variables_take_priority_over_file(self):
        os.environ.update(
            {
                "ALPACA_API_KEY": "env-key",
                "ALPACA_SECRET_KEY": "env-secret",
                "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
                "FEATHERLESS_API_KEY": "env-featherless",
            }
        )
        self._prod_file.write_text("ALPACA_API_KEY=file-key\n")

        result = credentials.load_credentials()

        self.assertEqual(result["ALPACA_API_KEY"], "env-key")

    def test_falls_back_to_production_credentials_file(self):
        self._prod_file.write_text(
            "# comment\n"
            "ALPACA_API_KEY=file-key\n"
            "ALPACA_SECRET_KEY=file-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=file-featherless\n"
        )

        result = credentials.load_credentials()

        self.assertEqual(result["ALPACA_API_KEY"], "file-key")
        self.assertEqual(result["FEATHERLESS_API_KEY"], "file-featherless")

    def test_falls_back_to_local_dotenv(self):
        self._dotenv_file.write_text(
            "ALPACA_API_KEY=dev-key\n"
            "ALPACA_SECRET_KEY=dev-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=dev-featherless\n"
        )

        result = credentials.load_credentials()

        self.assertEqual(result["ALPACA_API_KEY"], "dev-key")

    def test_partial_env_falls_through_to_file_for_missing_keys(self):
        os.environ["ALPACA_API_KEY"] = "env-key"
        self._prod_file.write_text(
            "ALPACA_API_KEY=file-key\n"
            "ALPACA_SECRET_KEY=file-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=file-featherless\n"
        )

        result = credentials.load_credentials()

        self.assertEqual(result["ALPACA_API_KEY"], "env-key")
        self.assertEqual(result["ALPACA_SECRET_KEY"], "file-secret")

    def test_raises_with_missing_key_names_when_nothing_resolves(self):
        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials()

        self.assertIn("ALPACA_API_KEY", str(ctx.exception))
        self.assertIn("FEATHERLESS_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
