import tempfile
import unittest
from pathlib import Path

from enterprise_ai.settings import create_env, load_settings


class SettingsTests(unittest.TestCase):
    def test_creates_distinct_secrets_and_reads_settings(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / '.env'
            create_env(path)
            settings = load_settings(path, environ={})
            secrets = [settings[k] for k in ('POSTGRES_PASSWORD', 'LAB_A_PASSWORD', 'LAB_B_PASSWORD')]
            self.assertEqual(len(set(secrets)), 3)
            self.assertTrue(all(len(secret) >= 32 for secret in secrets))
            self.assertEqual(settings['POSTGRES_PORT'], '5432')

    def test_does_not_replace_existing_credentials(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / '.env'
            path.write_text('keep-me', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                create_env(path)
            self.assertEqual(path.read_text(), 'keep-me')

    def test_rejects_missing_secret_without_echoing_value(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / '.env'
            create_env(path)
            with self.assertRaisesRegex(ValueError, 'LAB_A_PASSWORD'):
                load_settings(path, environ={'LAB_A_PASSWORD': ''})

    def test_rejects_invalid_port_and_shell_expansion(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / '.env'
            create_env(path)
            for port in ('0', '65536', 'abc'):
                with self.assertRaises(ValueError):
                    load_settings(path, environ={'POSTGRES_PORT': port})
            with path.open('a', encoding='utf-8') as handle:
                handle.write('BAD=$(whoami)\n')
            with self.assertRaises(ValueError):
                load_settings(path, environ={})
