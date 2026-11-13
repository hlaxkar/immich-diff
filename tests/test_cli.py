"""
Unit tests for cli module.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from immich_diff.cli import main


class TestCLI(unittest.TestCase):

    def test_cli_missing_api_key_exits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_args = ["immich-diff", "-d", temp_dir, "-u", "http://localhost:2283", "-k", ""]
            with patch('sys.argv', test_args):
                with patch('os.environ.get', return_value=''):
                    with self.assertRaises(SystemExit) as cm:
                        main()
                    self.assertIn("API key is required", str(cm.exception))

    def test_cli_nonexistent_dir_exits(self):
        test_args = ["immich-diff", "-d", "/nonexistent/directory/path/12345", "-k", "some_key"]
        with patch('sys.argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertIn("does not exist", str(cm.exception))

    def test_cli_empty_dir_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_args = ["immich-diff", "-d", temp_dir, "-k", "test_key", "--dry-run"]
            with patch('sys.argv', test_args):
                # Should cleanly exit when no files found
                main()

    def test_cli_visual_strategy_flags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_args = [
                "immich-diff",
                "-d", temp_dir,
                "-k", "test_key",
                "-s", "visual",
                "--similarity-threshold", "95.0",
                "--max-distance", "3",
                "--no-cache",
                "--dry-run"
            ]
            with patch('sys.argv', test_args):
                main()


if __name__ == '__main__':
    unittest.main()
