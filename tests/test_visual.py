"""
Unit tests for visual perceptual hashing (pHash) module.
"""

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from immich_diff.visual import (
    compute_phash,
    compute_phash_from_image,
    extract_video_frame,
    hamming_distance,
    similarity_percentage,
)


class TestVisual(unittest.TestCase):

    def setUp(self):
        # Create a sample test image with recognizable geometric shapes
        self.img1 = Image.new('RGB', (800, 600), color=(80, 120, 180))
        draw = ImageDraw.Draw(self.img1)
        draw.rectangle([100, 100, 400, 400], fill=(240, 60, 60))
        draw.ellipse([300, 200, 600, 500], fill=(60, 240, 60))

        # Create a completely different image
        self.img2 = Image.new('RGB', (800, 600), color=(250, 250, 250))
        draw2 = ImageDraw.Draw(self.img2)
        draw2.polygon([(100, 500), (400, 100), (700, 500)], fill=(10, 10, 10))

    def test_hamming_distance_and_similarity(self):
        # Same hash
        h = 0xAAAAAAAAAAAAAAAA
        self.assertEqual(hamming_distance(h, h), 0)
        self.assertEqual(similarity_percentage(0), 100.0)

        # Inverted hash (all 64 bits flipped)
        h_inv = ~h & 0xFFFFFFFFFFFFFFFF
        self.assertEqual(hamming_distance(h, h_inv), 64)
        self.assertEqual(similarity_percentage(64), 0.0)

        # 5 bits different
        h_diff5 = h ^ 0b11111
        self.assertEqual(hamming_distance(h, h_diff5), 5)
        self.assertAlmostEqual(similarity_percentage(5), (64 - 5) / 64.0 * 100.0, places=2)

    def test_phash_invariance_across_formats_and_compression(self):
        h_orig = compute_phash_from_image(self.img1)
        self.assertIsNotNone(h_orig)

        # 1. Compress to JPEG quality 50
        buf_jpg = io.BytesIO()
        self.img1.save(buf_jpg, format='JPEG', quality=50)
        buf_jpg.seek(0)
        h_jpg = compute_phash(buf_jpg.getvalue())
        dist_jpg = hamming_distance(h_orig, h_jpg)
        self.assertLessEqual(dist_jpg, 2, "JPEG compressed image should have Hamming distance <= 2")

        # 2. Convert to WebP and resize to 200x150 thumbnail
        thumb = self.img1.resize((200, 150))
        buf_webp = io.BytesIO()
        thumb.save(buf_webp, format='WEBP', quality=60)
        buf_webp.seek(0)
        h_webp = compute_phash(buf_webp.getvalue())
        dist_webp = hamming_distance(h_orig, h_webp)
        self.assertLessEqual(dist_webp, 3, "WebP thumbnail should have Hamming distance <= 3")

    def test_distinct_images_have_large_distance(self):
        h1 = compute_phash_from_image(self.img1)
        h2 = compute_phash_from_image(self.img2)
        dist = hamming_distance(h1, h2)
        self.assertGreater(dist, 10, f"Distinct images should have large Hamming distance, got {dist}")

    def test_compute_phash_from_file_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "test_img.png"
            self.img1.save(file_path, format="PNG")

            h_file = compute_phash(file_path)
            h_direct = compute_phash_from_image(self.img1)
            self.assertEqual(h_file, h_direct)

    def test_compute_phash_corrupt_data(self):
        corrupt_bytes = b"not a valid image data"
        self.assertIsNone(compute_phash(corrupt_bytes))

    @patch("shutil.which", return_value=None)
    def test_extract_video_frame_no_ffmpeg(self, mock_which):
        result = extract_video_frame(Path("/dummy/video.mp4"))
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
