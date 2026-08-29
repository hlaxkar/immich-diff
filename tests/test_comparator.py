"""
Unit tests for comparator module.
"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from immich_diff.client import ImmichClient
from immich_diff.comparator import (
    build_immich_index,
    compare_local_files,
    process_duplicate_moves,
    safe_move_file,
    scan_local_media,
)
from immich_diff.utils import to_extended_path


class TestComparator(unittest.TestCase):

    def test_build_immich_index_multiple_identical_filenames(self):
        assets = [
            {'id': '1', 'originalFileName': 'IMG_0001.JPG', 'checksum': 'hash1', 'exifInfo': {'fileSizeInByte': 100}},
            {'id': '2', 'originalFileName': 'IMG_0001.JPG', 'checksum': 'hash2', 'exifInfo': {'fileSizeInByte': 200}},
            {'id': '3', 'originalFileName': 'photo.png', 'checksum': 'hash3', 'exifInfo': {'fileSizeInByte': 300}},
        ]
        by_fn, by_cs, by_ex = build_immich_index(assets)

        self.assertIn('img_0001.jpg', by_fn)
        self.assertEqual(len(by_fn['img_0001.jpg']), 2)
        self.assertEqual(by_fn['img_0001.jpg'][0]['id'], '1')
        self.assertEqual(by_fn['img_0001.jpg'][1]['id'], '2')
        self.assertEqual(len(by_cs), 3)

    def test_scan_local_media(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "sub").mkdir()
            (temp_path / "photo1.jpg").write_bytes(b"image data")
            (temp_path / "sub" / "video1.mp4").write_bytes(b"video data")
            (temp_path / "ignore.txt").write_bytes(b"text data")

            scanned = scan_local_media(temp_path)
            self.assertEqual(len(scanned), 2)
            filenames = {f['filename'] for f in scanned}
            self.assertEqual(filenames, {"photo1.jpg", "video1.mp4"})

    def test_safe_move_file_collision_handling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            src_file = temp_path / "source.jpg"
            src_file.write_text("source content")

            dest_dir = temp_path / "dest"
            dest_file = dest_dir / "target.jpg"
            dest_dir.mkdir()
            dest_file.write_text("existing content")

            # First collision moves to target_1.jpg
            moved1 = safe_move_file(src_file, dest_file)
            self.assertEqual(moved1.name, "target_1.jpg")
            self.assertTrue(moved1.exists())
            self.assertEqual(moved1.read_text(), "source content")

            # Subsequent move creates target_2.jpg
            src_file2 = temp_path / "source2.jpg"
            src_file2.write_text("source2 content")
            moved2 = safe_move_file(src_file2, dest_file)
            self.assertEqual(moved2.name, "target_2.jpg")

    def test_safe_move_file_same_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            src_file = temp_path / "source.jpg"
            src_file.write_text("content")

            moved = safe_move_file(src_file, src_file)
            self.assertEqual(moved.resolve(), src_file.resolve())
            self.assertTrue(src_file.exists())

    def test_safe_move_file_trailing_space_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            src_dir = temp_path / "folder "
            os.makedirs(to_extended_path(src_dir), exist_ok=True)
            src_file = src_dir / "image.jpg"
            with open(to_extended_path(src_file), "w") as f:
                f.write("content")

            dest_dir = temp_path / "dest_folder "
            dest_file = dest_dir / "image.jpg"

            moved = safe_move_file(src_file, dest_file)
            self.assertTrue(os.path.exists(to_extended_path(moved)))
            self.assertFalse(os.path.exists(to_extended_path(src_file)))

            # Clean up trailing space folders so TemporaryDirectory can clean up on Windows
            if os.path.exists(to_extended_path(moved)):
                os.remove(to_extended_path(moved))
            if os.path.exists(to_extended_path(dest_dir)):
                os.rmdir(to_extended_path(dest_dir))
            if os.path.exists(to_extended_path(src_dir)):
                os.rmdir(to_extended_path(src_dir))

    def test_compare_local_files_priority_checksum_over_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            test_file = temp_path / "IMG_0001.JPG"
            test_file.write_bytes(b"unique content 12345")

            local_files = scan_local_media(temp_path)
            self.assertEqual(len(local_files), 1)

            # Immich has an asset with SAME filename IMG_0001.JPG but DIFFERENT checksum,
            # and another asset with DIFFERENT filename but EXACT SAME checksum!
            import base64
            import hashlib
            exact_hash = base64.b64encode(hashlib.sha1(b"unique content 12345").digest()).decode('utf-8')

            mock_immich_assets = [
                {'id': 'server-wrong-img', 'originalFileName': 'IMG_0001.JPG', 'checksum': 'different_hash', 'exifInfo': {'fileSizeInByte': 999}},
                {'id': 'server-exact-match', 'originalFileName': 'DSC_9999.JPG', 'checksum': exact_hash, 'exifInfo': {'fileSizeInByte': len(b"unique content 12345")}}
            ]

            mock_client = MagicMock(spec=ImmichClient)
            mock_client.fetch_all_assets.return_value = mock_immich_assets

            backed_up, not_backed_up, mode = compare_local_files(
                client=mock_client,
                local_files=local_files,
                strategy='all',
                fetch_mode='bulk',
                max_workers=1
            )

            self.assertEqual(len(backed_up), 1)
            self.assertEqual(backed_up[0]['match_reason'], 'CHECKSUM')
            self.assertEqual(backed_up[0]['immich']['id'], 'server-exact-match')

    def test_process_duplicate_moves_no_download_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            local_file = temp_path / "photo.jpg"
            local_file.write_bytes(b"test")

            mock_client = MagicMock(spec=ImmichClient)
            item = {
                'local': {
                    'full_path': local_file,
                    'rel_path': Path("photo.jpg"),
                    'filename': "photo.jpg",
                    'size': 4
                },
                'immich': {'id': 'immich-1', 'originalFileName': 'photo.jpg'},
                'match_reason': 'CHECKSUM'
            }

            dest_dir = temp_path / "duplicate"
            moved_count, err_count = process_duplicate_moves(
                client=mock_client,
                items=[item],
                base_dest_dir=dest_dir,
                subfolder="checksum",
                desc="Testing move",
                download_matched=False
            )

            self.assertEqual(moved_count, 1)
            self.assertEqual(err_count, 0)
            self.assertTrue((dest_dir / "checksum" / "photo.jpg").exists())
            mock_client.download_asset.assert_not_called()

            # Now test with download_matched=True
            local_file2 = temp_path / "photo2.jpg"
            local_file2.write_bytes(b"test2")
            item2 = {
                'local': {
                    'full_path': local_file2,
                    'rel_path': Path("photo2.jpg"),
                    'filename': "photo2.jpg",
                    'size': 5
                },
                'immich': {'id': 'immich-2', 'originalFileName': 'photo2.jpg'},
                'match_reason': 'CHECKSUM'
            }
            process_duplicate_moves(
                client=mock_client,
                items=[item2],
                base_dest_dir=dest_dir,
                subfolder="checksum",
                desc="Testing move with download",
                download_matched=True
            )
            self.assertTrue((dest_dir / "checksum" / "photo2.jpg").exists())
            mock_client.download_asset.assert_called_once()

    def test_compare_local_files_direct_mode_no_duplicate_match(self):
        """Test that in direct mode, files matched by checksum are NOT also matched by filename."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            file1 = temp_path / "img1.jpg"
            file2 = temp_path / "img2.jpg"
            file1.write_bytes(b"data1")
            file2.write_bytes(b"data2")

            local_files = scan_local_media(temp_path)
            self.assertEqual(len(local_files), 2)

            mock_client = MagicMock(spec=ImmichClient)
            # Both files matched via bulk_upload_check
            mock_client.bulk_upload_check.return_value = [
                {'id': str(file1), 'action': 'reject', 'reason': 'duplicate', 'assetId': 'srv-1'},
                {'id': str(file2), 'action': 'reject', 'reason': 'duplicate', 'assetId': 'srv-2'}
            ]
            # If query_single_metadata were called, it would return a filename match
            mock_client.query_single_metadata.return_value = {'id': 'srv-filename-match', 'originalFileName': 'img1.jpg'}

            backed_up, not_backed_up, mode = compare_local_files(
                client=mock_client,
                local_files=local_files,
                strategy='all',
                fetch_mode='direct',
                max_workers=1,
                batch_size=10
            )

            # Each file should only appear once in backed_up!
            self.assertEqual(len(backed_up), 2)
            self.assertEqual(len(not_backed_up), 0)
            reasons = [item['match_reason'] for item in backed_up]
            self.assertEqual(reasons, ['CHECKSUM', 'CHECKSUM'])
            # query_single_metadata should NOT have been called because all were matched by checksum
            mock_client.query_single_metadata.assert_not_called()


if __name__ == '__main__':
    unittest.main()
