"""
Unit tests for utils module.
"""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from immich_diff.utils import (
    calculate_sha1_b64,
    format_size,
    load_dotenv,
    normalize_api_url,
    parse_immich_file_size,
    parse_iso_datetime,
    to_extended_path,
)


class TestUtils(unittest.TestCase):

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(500), "500 B")
        self.assertEqual(format_size(1024), "1.00 KB")
        self.assertEqual(format_size(1048576), "1.00 MB")
        self.assertEqual(format_size(1073741824), "1.00 GB")
        self.assertEqual(format_size(-1048576), "-1.00 MB")

    def test_normalize_api_url(self):
        self.assertEqual(normalize_api_url("http://localhost:2283"), "http://localhost:2283/api")
        self.assertEqual(normalize_api_url("http://localhost:2283/"), "http://localhost:2283/api")
        self.assertEqual(normalize_api_url("http://localhost:2283/api"), "http://localhost:2283/api")
        self.assertEqual(normalize_api_url("http://localhost:2283/api/"), "http://localhost:2283/api")

    def test_parse_iso_datetime(self):
        dt = parse_iso_datetime("2024-05-15T14:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 5)
        self.assertEqual(dt.day, 15)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)

        dt2 = parse_iso_datetime("2024-05-15 14:30:00")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2.hour, 14)

        self.assertIsNone(parse_iso_datetime(None))
        self.assertIsNone(parse_iso_datetime(""))

    def test_calculate_sha1_b64(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"Hello Immich Diff Test Data")
            temp_path = Path(f.name)

        try:
            import base64
            expected = base64.b64encode(hashlib.sha1(b"Hello Immich Diff Test Data").digest()).decode('utf-8')
            computed = calculate_sha1_b64(temp_path)
            self.assertEqual(computed, expected)
        finally:
            temp_path.unlink()

    def test_parse_immich_file_size(self):
        asset1 = {'exifInfo': {'fileSizeInByte': 12345}}
        self.assertEqual(parse_immich_file_size(asset1), 12345)

        asset2 = {'fileSizeInByte': 67890}
        self.assertEqual(parse_immich_file_size(asset2), 67890)

        asset3 = {'size': 99999}
        self.assertEqual(parse_immich_file_size(asset3), 99999)

        asset4 = {'originalFileSize': 11111}
        self.assertEqual(parse_immich_file_size(asset4), 11111)

        asset5 = {}
        self.assertEqual(parse_immich_file_size(asset5), 0)

    def test_load_dotenv(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("TEST_IMMICH_VAR=HelloWorld123\n# A comment\nANOTHER_VAR='quoted'\n")
            temp_path = Path(f.name)

        try:
            load_dotenv(temp_path)
            self.assertEqual(os.environ.get('TEST_IMMICH_VAR'), 'HelloWorld123')
            self.assertEqual(os.environ.get('ANOTHER_VAR'), 'quoted')
        finally:
            temp_path.unlink()
            os.environ.pop('TEST_IMMICH_VAR', None)
            os.environ.pop('ANOTHER_VAR', None)

    def test_to_extended_path(self):
        self.assertEqual(to_extended_path(None), "")
        self.assertEqual(to_extended_path(""), "")
        if os.name == 'nt':
            self.assertEqual(to_extended_path(r"C:\test\folder \file.jpg"), r"\\?\C:\test\folder \file.jpg")
            self.assertEqual(to_extended_path(r"C:\test\folder \..\other \file.jpg"), r"\\?\C:\test\other \file.jpg")
            self.assertEqual(to_extended_path(r"\\server\share\test"), r"\\?\UNC\server\share\test")
            self.assertEqual(to_extended_path(r"\\server\share\test\..\other"), r"\\?\UNC\server\share\other")
            self.assertEqual(to_extended_path(r"\\?\C:\already\prefixed"), r"\\?\C:\already\prefixed")
            self.assertEqual(to_extended_path(r"\\?\C:\already\prefixed\..\folder "), r"\\?\C:\already\folder ")
            self.assertEqual(to_extended_path(r"\\?\UNC\server\share\..\other"), r"\\?\UNC\server\other")
        else:
            self.assertEqual(to_extended_path("/tmp/test"), "/tmp/test")


if __name__ == '__main__':
    unittest.main()
