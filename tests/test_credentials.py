import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from bot import credentials


class CredentialsTestCase(unittest.TestCase):
    """Shared fixture: an isolated env, deployed dir, secrets.yaml and .env,
    so no test ever reads the developer's real credentials."""

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

        self._secrets_file = tmp / "secrets.yaml"
        secrets_patch = mock.patch.object(credentials, "SECRETS_FILE", self._secrets_file)
        secrets_patch.start()
        self.addCleanup(secrets_patch.stop)

    def _write_secrets(self, **doc):
        self._secrets_file.write_text(yaml.safe_dump(doc))

    def _full_secrets(self, **overrides):
        """A secrets.yaml with everything account `test` needs."""
        doc = {
            "featherless_api_key": "yaml-featherless",
            "base_url": "https://paper-api.alpaca.markets",
            "accounts": {"test": {"api_key": "yaml-key", "secret_key": "yaml-secret"}},
        }
        doc.update(overrides)
        self._write_secrets(**doc)

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


class LoadCredentialsTest(CredentialsTestCase):
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


class SecretsYamlTest(CredentialsTestCase):
    """secrets.yaml is the canonical local-dev credentials file: one file, every
    local account, each field traceable back to the vault variable it came from.
    The .env chain below it is legacy back-compat."""

    def test_named_account_resolves_from_secrets_yaml(self):
        self._full_secrets()

        result = credentials.load_credentials("test")

        self.assertEqual(result["ALPACA_API_KEY"], "yaml-key")
        self.assertEqual(result["ALPACA_SECRET_KEY"], "yaml-secret")
        self.assertEqual(result["FEATHERLESS_API_KEY"], "yaml-featherless")
        self.assertEqual(result["ALPACA_BASE_URL"], "https://paper-api.alpaca.markets")

    def test_shared_values_apply_to_every_account(self):
        self._write_secrets(
            featherless_api_key="shared-featherless",
            base_url="https://paper-api.alpaca.markets",
            accounts={"kimi26": {"api_key": "k26", "secret_key": "s26"}},
        )

        result = credentials.load_credentials("kimi26")

        self.assertEqual(result["ALPACA_API_KEY"], "k26")
        self.assertEqual(result["FEATHERLESS_API_KEY"], "shared-featherless")

    def test_account_block_overrides_shared_values(self):
        self._write_secrets(
            featherless_api_key="shared-featherless",
            base_url="https://paper-api.alpaca.markets",
            accounts={"test": {"api_key": "k", "secret_key": "s", "featherless_api_key": "mine"}},
        )

        self.assertEqual(credentials.load_credentials("test")["FEATHERLESS_API_KEY"], "mine")

    def test_secrets_yaml_wins_over_legacy_dotenv(self):
        self._full_secrets()
        self._dotenv_file.write_text(
            "ALPACA_API_KEY=stale-dotenv\n"
            "ALPACA_SECRET_KEY=stale\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=stale\n"
        )

        self.assertEqual(credentials.load_credentials("test")["ALPACA_API_KEY"], "yaml-key")

    def test_dotenv_fills_keys_missing_from_secrets_yaml(self):
        self._write_secrets(accounts={"test": {"api_key": "yaml-key"}})
        self._dotenv_file.write_text(
            "ALPACA_SECRET_KEY=dotenv-secret\n"
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets\n"
            "FEATHERLESS_API_KEY=dotenv-featherless\n"
        )

        result = credentials.load_credentials("test")

        self.assertEqual(result["ALPACA_API_KEY"], "yaml-key")
        self.assertEqual(result["ALPACA_SECRET_KEY"], "dotenv-secret")

    def test_environment_variables_win_over_secrets_yaml(self):
        self._full_secrets()
        os.environ["ALPACA_API_KEY"] = "env-key"

        self.assertEqual(credentials.load_credentials("test")["ALPACA_API_KEY"], "env-key")

    def test_deployed_file_wins_over_secrets_yaml(self):
        self._full_secrets()
        self._write_prod_file("credentials-test.env", ALPACA_API_KEY="deployed-key")

        self.assertEqual(credentials.load_credentials("test")["ALPACA_API_KEY"], "deployed-key")

    def test_unknown_account_falls_through_to_the_normal_error(self):
        self._full_secrets()

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("qwen-a")

        self.assertIn("'qwen-a'", str(ctx.exception))

    def test_error_message_names_the_secrets_file(self):
        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        self.assertIn("secrets.yaml", str(ctx.exception))

    def test_blank_values_are_treated_as_missing(self):
        self._write_secrets(
            featherless_api_key="",
            base_url="",
            accounts={"test": {"api_key": "", "secret_key": None}},
        )

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        self.assertIn("ALPACA_API_KEY", str(ctx.exception))

    def test_non_string_values_are_coerced(self):
        self._write_secrets(
            featherless_api_key=12345,
            base_url="https://paper-api.alpaca.markets",
            accounts={"test": {"api_key": "k", "secret_key": "s"}},
        )

        self.assertEqual(credentials.load_credentials("test")["FEATHERLESS_API_KEY"], "12345")

    # --- the official-account invariant ------------------------------------

    def test_official_account_never_reads_secrets_yaml(self):
        self._write_secrets(
            featherless_api_key="f",
            base_url="https://paper-api.alpaca.markets",
            accounts={"test": {"api_key": "k", "secret_key": "s"}},
        )

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("official")

        self.assertIn("ALPACA_API_KEY", str(ctx.exception))

    def test_an_official_account_block_is_rejected_for_every_account(self):
        """Pasting the judging account's keys in here must fail loudly, not
        quietly trade PA3VS39Y5LE2 under the name `test`."""
        self._write_secrets(
            featherless_api_key="f",
            base_url="https://paper-api.alpaca.markets",
            accounts={
                "official": {"api_key": "leaked", "secret_key": "leaked"},
                "test": {"api_key": "k", "secret_key": "s"},
            },
        )

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        message = str(ctx.exception)
        self.assertIn("official", message)
        self.assertIn("credentials.env", message)

    def test_top_level_official_key_is_rejected(self):
        self._write_secrets(official={"api_key": "leaked"}, accounts={"test": {"api_key": "k"}})

        with self.assertRaises(RuntimeError):
            credentials.load_credentials("test")

    def test_secrets_for_account_returns_nothing_for_official(self):
        self._write_secrets(accounts={"test": {"api_key": "k"}})

        self.assertEqual(credentials.secrets_for_account("official"), {})

    # --- malformed input ----------------------------------------------------

    def test_malformed_yaml_raises_a_clear_error(self):
        self._secrets_file.write_text("accounts:\n  test: [unclosed\n")

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        self.assertIn("secrets.yaml", str(ctx.exception))

    def test_non_mapping_document_is_rejected(self):
        self._secrets_file.write_text("- just\n- a list\n")

        with self.assertRaises(RuntimeError):
            credentials.load_credentials("test")

    def test_bad_account_name_in_secrets_yaml_is_rejected(self):
        self._write_secrets(accounts={"Live Account": {"api_key": "k"}})

        with self.assertRaises(ValueError):
            credentials.load_credentials("test")

    def test_a_secrets_yaml_directory_is_ignored(self):
        """`docker compose` creates an empty directory at a bind-mount source
        that doesn't exist yet. That must read as "no secrets file"."""
        self._secrets_file.mkdir()

        with self.assertRaises(RuntimeError) as ctx:
            credentials.load_credentials("test")

        self.assertIn("ALPACA_API_KEY", str(ctx.exception))

    def test_empty_secrets_file_is_ignored(self):
        self._secrets_file.write_text("")

        with self.assertRaises(RuntimeError):
            credentials.load_credentials("test")


class SecretsYamlMqttTest(CredentialsTestCase):
    def test_mqtt_settings_resolve_from_secrets_yaml(self):
        self._write_secrets(
            accounts={"test": {"api_key": "k"}},
            mqtt={"host": "broker.local", "port": 1883, "username": "u", "password": "p"},
        )

        found = credentials.load_mqtt_env("test")

        self.assertEqual(found["MQTT_HOST"], "broker.local")
        self.assertEqual(found["MQTT_PORT"], "1883")

    def test_account_mqtt_block_overrides_the_shared_one(self):
        self._write_secrets(
            accounts={"test": {"api_key": "k", "mqtt": {"host": "mine.local"}}},
            mqtt={"host": "shared.local", "port": 1883},
        )

        found = credentials.load_mqtt_env("test")

        self.assertEqual(found["MQTT_HOST"], "mine.local")
        self.assertEqual(found["MQTT_PORT"], "1883")

    def test_official_mqtt_never_reads_secrets_yaml(self):
        self._write_secrets(accounts={"test": {"api_key": "k"}}, mqtt={"host": "broker.local"})

        self.assertEqual(credentials.load_mqtt_env("official"), {})

    def test_never_raises_on_a_malformed_secrets_file(self):
        self._secrets_file.write_text("accounts:\n  test: [unclosed\n")

        self.assertEqual(credentials.load_mqtt_env("test"), {})

    def test_never_raises_on_an_official_block(self):
        self._write_secrets(accounts={"official": {"api_key": "leaked"}})

        self.assertEqual(credentials.load_mqtt_env("test"), {})

    def test_no_mqtt_block_is_a_silent_no_op(self):
        self._full_secrets()

        self.assertEqual(credentials.load_mqtt_env("test"), {})


class SecretsExampleFileTest(unittest.TestCase):
    """The committed template must stay parseable, credential-free, and free of
    anything that shouldn't be in a repo that goes public before Sep 4."""

    def setUp(self):
        self.path = credentials.REPO_ROOT / "secrets.example.yaml"

    def test_example_parses_and_declares_the_test_account(self):
        data = credentials.load_secrets(self.path)

        self.assertIn("test", data["accounts"])

    def test_example_contains_no_credentials(self):
        """Every secret is blank; only the public paper-API base URL is filled in."""
        resolved = credentials.secrets_for_account("test", self.path)

        self.assertNotIn("ALPACA_API_KEY", resolved)
        self.assertNotIn("ALPACA_SECRET_KEY", resolved)
        self.assertNotIn("FEATHERLESS_API_KEY", resolved)
        self.assertNotIn("MQTT_PASSWORD", resolved)
        self.assertEqual(resolved, {"ALPACA_BASE_URL": "https://paper-api.alpaca.markets"})

    def test_example_names_the_vault_variables_it_comes_from(self):
        text = self.path.read_text()

        self.assertIn("vault_alpaca_hackathon_test_api_key", text)
        self.assertIn("vault_alpaca_hackathon_featherless_api_key", text)

    def test_example_leaks_no_lan_address(self):
        text = self.path.read_text()

        self.assertNotIn("192.168.", text)
        self.assertNotIn("10.0.", text)


if __name__ == "__main__":
    unittest.main()
