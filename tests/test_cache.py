"""
Unit tests for HashCache module.
"""

from pathlib import Path
import tempfile
import unittest

from immich_diff.cache import HashCache


class TestCache(unittest.TestCase):

    def test_cache_disabled(self):
        cache = HashCache(enabled=False)
        self.assertFalse(cache.enabled)
        self.assertIsNone(cache.get_immich_hash("asset1"))
        self.assertEqual(cache.get_immich_hashes(["asset1"]), {})
        self.assertIsNone(cache.get_local_hash("/tmp/foo.jpg", 123.45, 1000))
        # Writing should be a no-op
        cache.set_immich_hashes_batch([("asset1", 0x1234, "2024-01-01")])
        cache.set_local_hash("/tmp/foo.jpg", 123.45, 1000, 0x1234)
        cache.close()

    def test_immich_hashes_get_and_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = HashCache(cache_dir=Path(temp_dir), enabled=True)
            self.assertTrue(cache.enabled)

            # Initially empty
            self.assertIsNone(cache.get_immich_hash("asset-1"))
            self.assertEqual(cache.get_immich_hashes(["asset-1", "asset-2"]), {})

            # Batch insert
            items = [
                ("asset-1", 0x1111222233334444, "2024-01-01T10:00:00Z"),
                ("asset-2", 0x5555666677778888, "2024-01-01T10:00:00Z"),
            ]
            cache.set_immich_hashes_batch(items)

            # Fetch single
            self.assertEqual(cache.get_immich_hash("asset-1"), 0x1111222233334444)
            self.assertEqual(cache.get_immich_hash("asset-2"), 0x5555666677778888)

            # Fetch batch
            fetched = cache.get_immich_hashes(["asset-1", "asset-2", "asset-nonexistent"])
            self.assertEqual(len(fetched), 2)
            self.assertEqual(fetched["asset-1"], 0x1111222233334444)
            self.assertEqual(fetched["asset-2"], 0x5555666677778888)

            cache.close()

    def test_local_file_hashes_and_mtime_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = HashCache(cache_dir=Path(temp_dir), enabled=True)

            path = "/media/photos/sample.jpg"
            mtime = 1700000000.0
            size = 2048
            phash = 0xABCDEF0123456789

            # Save local hash
            cache.set_local_hash(path, mtime, size, phash)

            # Valid query
            self.assertEqual(cache.get_local_hash(path, mtime, size), phash)

            # Invalidated when mtime changes
            self.assertIsNone(cache.get_local_hash(path, mtime + 5.0, size))

            # Invalidated when size changes
            self.assertIsNone(cache.get_local_hash(path, mtime, size + 10))

            # Unknown path
            self.assertIsNone(cache.get_local_hash("/other/path.jpg", mtime, size))

            cache.close()

    def test_immich_asset_metadata_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = HashCache(cache_dir=Path(temp_dir), enabled=True)
            self.assertEqual(cache.get_immich_asset_count(), 0)
            self.assertIsNone(cache.get_last_asset_sync_time())

            assets = [
                {
                    'id': 'asset-1',
                    'originalFileName': 'IMG_0001.JPG',
                    'checksum': 'hash1',
                    'exifInfo': {'fileSizeInByte': 1024, 'dateTimeOriginal': '2024-01-01T12:00:00'},
                    'fileCreatedAt': '2024-01-01T12:00:00Z',
                    'updatedAt': '2024-01-01T12:30:00.000Z'
                },
                {
                    'id': 'asset-2',
                    'originalFileName': 'video.mp4',
                    'checksum': 'hash2',
                    'size': 2048,
                    'fileCreatedAt': '2024-01-02T10:00:00Z',
                    'updatedAt': '2024-01-02T10:30:00.000Z'
                }
            ]

            cache.upsert_immich_assets(assets)
            self.assertEqual(cache.get_immich_asset_count(), 2)
            self.assertEqual(cache.get_last_asset_sync_time(), '2024-01-02T10:30:00.000Z')

            loaded = cache.get_all_immich_assets()
            self.assertEqual(len(loaded), 2)
            by_id = {a['id']: a for a in loaded}
            self.assertEqual(by_id['asset-1']['originalFileName'], 'IMG_0001.JPG')
            self.assertEqual(by_id['asset-1']['exifInfo']['fileSizeInByte'], 1024)
            self.assertEqual(by_id['asset-2']['originalFileName'], 'video.mp4')
            self.assertEqual(by_id['asset-2']['fileSizeInByte'], 2048)

            # Test clear
            cache.clear_immich_assets()
            self.assertEqual(cache.get_immich_asset_count(), 0)
            self.assertIsNone(cache.get_last_asset_sync_time())
            cache.close()


if __name__ == '__main__':
    unittest.main()
