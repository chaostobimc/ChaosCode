import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chaoscode import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_root = Path(self.temp_dir.name) / "cfg"
        self.patch_config = patch("chaoscode.config.user_config_dir", return_value=self.config_root)
        self.patch_config.start()
        self.addCleanup(self.patch_config.stop)

    def test_token_is_saved_and_read_without_echo(self):
        config.save_auth_token("secret-session-token")
        token, source = config.get_auth_token()
        self.assertEqual(token, "secret-session-token")
        self.assertEqual(source, "Benutzerkonfiguration")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(config.config_file().stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config_root.stat().st_mode), 0o700)

    def test_environment_token_has_precedence(self):
        config.save_auth_token("saved-token")
        with patch.dict(os.environ, {"DEEPSEEK_AUTH_TOKEN": "env-token"}):
            self.assertEqual(
                config.get_auth_token(), ("env-token", "Umgebungsvariable DEEPSEEK_AUTH_TOKEN")
            )

    def test_remove_saved_token_preserves_other_config(self):
        config._write_config({"auth_token": "token", "other": "setting"})
        self.assertTrue(config.remove_saved_auth_token())
        with config.config_file().open(encoding="utf-8") as stream:
            self.assertEqual(json.load(stream), {"other": "setting"})
        self.assertFalse(config.remove_saved_auth_token())

    def test_blank_token_is_rejected(self):
        with self.assertRaises(ValueError):
            config.save_auth_token("   ")


if __name__ == "__main__":
    unittest.main()
