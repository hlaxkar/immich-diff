"""
Unit tests for visual comparison and deduplication in comparator module.
"""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from immich_diff.cache import HashCache
from immich_diff.client import ImmichClient
from immich_diff.comparator import compare_local_files, process_duplicate_moves, scan_local_media


class TestComparatorVisual(unittest.TestCase):

    def setUp(self):
        # Create image 1 (geometric pattern)
        self.img1 = Image.new('RGB', (600, 400), color=(50, 100, 150))
        draw1 = ImageDraw.Draw(self.img1)
        draw1.rectangle([50, 50, 250, 250], fill=(220, 50, 50))
        draw1.ellipse([200, 150, 450, 350], fill=(50, 220, 50))

        # Create image 2 (distinct image)
        self.img2 = Image.new('RGB', (600, 400), color=(240, 240, 240))
        draw2 = ImageDraw.Draw(self.img2)
        draw2.polygon([(50, 350), (300, 50), (550, 350)], fill=(20, 20, 20))

    def test_compare_local_files_visual_strategy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            local_dir = temp_path / "local"
            local_dir.mkdir()

            # Save local image 1 as compressed JPEG with a completely different filename
            local_file1 = local_dir / "whatsapp_image_123.jpg"
            self.img1.save(local_file1, format="JPEG", quality=55)

            # Save local image 2 as PNG
            local_file2 = local_dir / "unbacked_unique.png"
            self.img2.save(local_file2, format="PNG")

            local_files = scan_local_media(local_dir)
            self.assertEqual(len(local_files), 2)

            # Generate thumbnail bytes for Immich server asset (representing original img1)
            thumb_buf = io.BytesIO()
            self.img1.resize((200, 133)).save(thumb_buf, format="WEBP", quality=60)
            thumb_bytes = thumb_buf.getvalue()

            mock_immich_assets = [
                {
                    'id': 'asset-original-heic-id',
                    'originalFileName': 'IMG_9999.HEIC',
                    'checksum': 'completely_different_checksum_abc123',
                    'exifInfo': {'fileSizeInByte': 2500000}
                }
            ]

            mock_client = MagicMock(spec=ImmichClient)
            mock_client.fetch_all_assets.return_value = mock_immich_assets
            mock_client.fetch_asset_thumbnail.return_value = thumb_bytes

            cache = HashCache(cache_dir=temp_path / "cache", enabled=True)

            backed_up, not_backed_up, mode = compare_local_files(
                client=mock_client,
                local_files=local_files,
                strategy='visual',
                fetch_mode='auto',
                max_workers=1,
                cache=cache,
                max_distance=5
            )

            # 1 should be matched visually, 1 should be unmatched
            self.assertEqual(len(backed_up), 1)
            self.assertEqual(len(not_backed_up), 1)

            matched_item = backed_up[0]
            self.assertEqual(matched_item['local']['filename'], "whatsapp_image_123.jpg")
            self.assertEqual(matched_item['immich']['id'], "asset-original-heic-id")
            self.assertEqual(matched_item['match_reason'], "VISUAL_SIMILARITY")
            self.assertIn('hamming_distance', matched_item)
            self.assertLessEqual(matched_item['hamming_distance'], 5)
            self.assertGreaterEqual(matched_item['similarity'], 90.0)

            unmatched_item = not_backed_up[0]
            self.assertEqual(unmatched_item['filename'], "unbacked_unique.png")

            cache.close()

    def test_process_duplicate_moves_visual_subfolder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            local_file = temp_path / "compressed.jpg"
            local_file.write_bytes(b"image bytes")

            item = {
                'local': {
                    'full_path': local_file,
                    'rel_path': Path("compressed.jpg"),
                    'filename': "compressed.jpg",
                    'size': len(b"image bytes")
                },
                'immich': {'id': 'immich-visual-1', 'originalFileName': 'IMG_0001.HEIC'},
                'match_reason': 'VISUAL_SIMILARITY',
                'hamming_distance': 1,
                'similarity': 98.4
            }

            dest_dir = temp_path / "duplicate"
            mock_client = MagicMock(spec=ImmichClient)

            moved_count, err_count = process_duplicate_moves(
                client=mock_client,
                items=[item],
                base_dest_dir=dest_dir,
                subfolder="visual",
                desc="Moving visual matches",
                download_matched=False
            )

            self.assertEqual(moved_count, 1)
            self.assertEqual(err_count, 0)
            self.assertTrue((dest_dir / "visual" / "compressed.jpg").exists())
            self.assertFalse(local_file.exists())


if __name__ == '__main__':
    unittest.main()
