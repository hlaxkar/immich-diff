"""
Perceptual Hashing (pHash) implementation using pure Pillow and 2D DCT.
Zero heavy machine learning dependencies (no PyTorch, OpenCV, or SciPy required).
"""

import io
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, List, Optional, Tuple, Union

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from .exceptions import ImmichDiffError
from .utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, to_extended_path

logger = logging.getLogger(__name__)

# Constants for 32x32 DCT -> 8x8 low frequency pHash
HASH_SIZE = 8
HIGHFREQ_FACTOR = 4
IMG_SIZE = HASH_SIZE * HIGHFREQ_FACTOR  # 32

# Precomputed 1D DCT-II matrix for K=8, N=32
# M[k][n] = 2.0 * cos(pi * k * (2n + 1) / (2 * N))
DCT_MATRIX: List[List[float]] = [
    [2.0 * math.cos(math.pi * k * (2 * n + 1) / (2 * IMG_SIZE)) for n in range(IMG_SIZE)]
    for k in range(HASH_SIZE)
]


def extract_video_frame(video_path: Union[Path, str]) -> Optional[Any]:
    """
    Extract a representative frame from a local video file using ffmpeg.
    Returns a PIL Image object or None if ffmpeg is unavailable or fails.
    """
    if not HAS_PIL:
        return None

    if not shutil.which("ffmpeg"):
        logger.debug("ffmpeg not found in PATH; cannot extract frame from video: %s", video_path)
        return None

    ext_path = to_extended_path(Path(video_path))
    cmd = [
        "ffmpeg",
        "-ss", "00:00:01",
        "-i", str(ext_path),
        "-vframes", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1"
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False
        )
        if proc.returncode == 0 and proc.stdout:
            img = Image.open(io.BytesIO(proc.stdout))
            img.load()
            return img
    except Exception as e:
        logger.debug("ffmpeg failed to extract frame from %s: %s", video_path, e)

    return None


def compute_phash_from_image(image: Any) -> Optional[int]:
    """
    Compute 64-bit DCT perceptual hash from a PIL Image.
    1. Transpose orientation based on EXIF tags.
    2. Convert to grayscale and downsample to 32x32.
    3. Perform 2D DCT to extract top 8x8 low-frequency coefficients.
    4. Compute median of AC coefficients.
    5. Construct 64-bit unsigned integer based on comparison to median.
    """
    if not HAS_PIL:
        raise ImmichDiffError("Pillow is required for visual matching. Run: pip install Pillow")

    try:
        # Transpose image according to EXIF orientation tag if present
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass

        # Convert to grayscale ('L') and resize to 32x32 using high-quality filter
        resample = getattr(Image, 'Resampling', Image).LANCZOS
        small = image.convert('L').resize((IMG_SIZE, IMG_SIZE), resample)

        # Extract 1024 raw pixel values (0-255)
        raw_bytes = small.tobytes()
        rows = [list(raw_bytes[i * IMG_SIZE:(i + 1) * IMG_SIZE]) for i in range(IMG_SIZE)]

        # Step 1: 1D DCT on each of the 32 rows -> 32 rows of length 8
        row_transformed = [
            [sum(DCT_MATRIX[k][n] * row[n] for n in range(IMG_SIZE)) for k in range(HASH_SIZE)]
            for row in rows
        ]

        # Step 2: 1D DCT on columns of row_transformed -> 8 rows of length 8 (8x8)
        cols = list(zip(*row_transformed))
        d_matrix = [
            [sum(DCT_MATRIX[k][n] * cols[l][n] for n in range(IMG_SIZE)) for l in range(HASH_SIZE)]
            for k in range(HASH_SIZE)
        ]

        # Flatten into 64 values
        flat = [d_matrix[k][l] for k in range(HASH_SIZE) for l in range(HASH_SIZE)]

        # Compute median excluding the DC component at index 0
        ac_values = flat[1:]
        sorted_ac = sorted(ac_values)
        med = sorted_ac[len(sorted_ac) // 2]

        # Build 64-bit integer
        h = 0
        for i, val in enumerate(flat):
            if val > med:
                h |= (1 << (63 - i))
        return h

    except Exception as e:
        logger.debug("Failed computing pHash from image: %s", e)
        return None


def compute_phash(image_or_path: Union[Path, str, bytes, Any]) -> Optional[int]:
    """
    Compute 64-bit perceptual hash for a file path, raw bytes, or PIL Image.
    Returns None on failure or corrupted media.
    """
    if not HAS_PIL:
        raise ImmichDiffError("Pillow is required for visual matching. Run: pip install Pillow")

    if isinstance(image_or_path, (bytes, bytearray)):
        try:
            with Image.open(io.BytesIO(image_or_path)) as img:
                return compute_phash_from_image(img)
        except Exception as e:
            logger.debug("Failed reading image bytes for pHash: %s", e)
            return None

    if hasattr(image_or_path, 'convert') and hasattr(image_or_path, 'resize'):
        # Already a PIL Image
        return compute_phash_from_image(image_or_path)

    # Assume path
    path = Path(image_or_path)
    ext = path.suffix.lower()

    if ext in VIDEO_EXTENSIONS:
        frame_img = extract_video_frame(path)
        if frame_img is not None:
            return compute_phash_from_image(frame_img)
        return None

    if ext in IMAGE_EXTENSIONS:
        ext_path = to_extended_path(path)
        try:
            with Image.open(ext_path) as img:
                return compute_phash_from_image(img)
        except Exception as e:
            logger.debug("Could not open image file %s for pHash: %s", path, e)
            return None

    return None


def hamming_distance(h1: int, h2: int) -> int:
    """Calculate the Hamming distance between two 64-bit integer hashes."""
    xor_val = h1 ^ h2
    if hasattr(xor_val, 'bit_count'):
        return xor_val.bit_count()
    return bin(xor_val).count('1')


def similarity_percentage(distance: int) -> float:
    """Convert Hamming distance (0-64) into a visual similarity percentage (0-100%)."""
    return max(0.0, min(100.0, (64 - distance) / 64.0 * 100.0))
