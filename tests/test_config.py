"""Tests for configuration loading."""

import unittest
from pathlib import Path

from config import PROJECT_ROOT, ConfigError, load_config
from tests.helpers import temp_dir


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.env_file = Path(self.tmp.name) / ".env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        config = load_config(env_file=self.env_file, environ={})
        self.assertEqual(config.name, "NOVA")
        self.assertEqual(config.version, "0.1")
        self.assertEqual(config.brain, "ollama")
        self.assertEqual(config.ollama_host, "http://localhost:11434")
        self.assertEqual(config.ollama_model, "")
        self.assertEqual(config.data_dir, PROJECT_ROOT / "data")

    def test_env_file_is_read(self):
        self.env_file.write_text(
            "# comment\n\nNOVA_NAME=Nova Prime\nOLLAMA_MODEL=\"llama3.2\"\nexport NOVA_MAX_HISTORY=8\n"
        )
        config = load_config(env_file=self.env_file, environ={})
        self.assertEqual(config.name, "Nova Prime")
        self.assertEqual(config.ollama_model, "llama3.2")
        self.assertEqual(config.max_history, 8)

    def test_environment_overrides_env_file(self):
        self.env_file.write_text("OLLAMA_MODEL=from-file\n")
        config = load_config(env_file=self.env_file, environ={"OLLAMA_MODEL": "from-env"})
        self.assertEqual(config.ollama_model, "from-env")

    def test_relative_paths_use_project_root(self):
        config = load_config(env_file=self.env_file, environ={"NOVA_DATA_DIR": "somewhere"})
        self.assertEqual(config.data_dir, PROJECT_ROOT / "somewhere")
        self.assertEqual(config.db_path, PROJECT_ROOT / "somewhere" / "nova.db")

    def test_invalid_values_raise_config_error(self):
        bad_values = [
            {"NOVA_BRAIN": "chatgpt"},
            {"NOVA_MAX_HISTORY": "lots"},
            {"NOVA_MAX_HISTORY": "0"},
            {"NOVA_LOG_LEVEL": "LOUD"},
            {"OLLAMA_HOST": "localhost:11434"},
            {"NOVA_NAME": "  "},
        ]
        for environ in bad_values:
            with self.subTest(environ=environ):
                with self.assertRaises(ConfigError):
                    load_config(env_file=self.env_file, environ=environ)

    def test_malformed_env_file(self):
        self.env_file.write_text("THIS LINE HAS NO EQUALS SIGN\n")
        with self.assertRaises(ConfigError):
            load_config(env_file=self.env_file, environ={})


if __name__ == "__main__":
    unittest.main()
