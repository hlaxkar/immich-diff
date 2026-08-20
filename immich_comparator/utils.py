"""
Utility functions, constants, and dependency fallbacks for Immich Comparator.
"""

import base64
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

# Progress bar fallback
try:
    from tqdm import tqdm
except ImportError:
    class DummyTqdm:
        def __init__(self, iterable=None, desc="", total=None, unit="", file=None, **kwargs):
            self.iterable = iterable
            self.desc = desc
            self.total = total
            self.count = 0
            if desc:
                print(f"--> {desc}...", flush=True)

        def __iter__(self):
            if self.iterable:
                for item in self.iterable:
                    yield item

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def update(self, n=1):
            self.count += n

    def tqdm(iterable=None, **kwargs):
        return DummyTqdm(iterable=iterable, **kwargs)

# PIL EXIF reader fallback
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.gif',
    '.tiff', '.tif', '.bmp', '.raw', '.arw', '.cr2', '.nef',
    '.dng', '.orf', '.rw2', '.pef', '.srw'
}

VIDEO_EXTENSIONS = {
    '.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v',
    '.3gp', '.ts', '.mts', '.m2ts', '.wmv', '.vob', '.ogv'
}


def format_size(bytes_val: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB, etc.)."""
    sign = "-" if bytes_val < 0 else ""
    bytes_val = abs(bytes_val)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if bytes_val < 1024.0 or unit == 'PB':
            return f"{sign}{bytes_val:.2f} {unit}" if unit != 'B' else f"{sign}{int(bytes_val)} B"
        bytes_val /= 1024.0
    return f"{sign}{bytes_val:.2f} B"


def normalize_api_url(url: str) -> str:
    """Ensure URL ends with /api cleanly."""
    url = url.rstrip('/')
    return url if url.endswith('/api') else f"{url}/api"


def extract_exif_datetime(filepath: Path) -> Optional[datetime]:
    """Extract capture datetime from image EXIF tags if PIL is available."""
    if not HAS_PIL or filepath.suffix.lower() not in ('.jpg', '.jpeg', '.tif', '.tiff'):
        return None
    try:
        with Image.open(filepath) as img:
            exif = img.getexif()
            if exif:
                # 36867: DateTimeOriginal, 36868: DateTimeDigitized, 306: DateTime
                for tag_id in (36867, 36868, 306):
                    val = exif.get(tag_id)
                    if val and isinstance(val, str):
                        clean_val = val.strip('\x00 ').strip()
                        if len(clean_val) >= 19:
                            try:
                                return datetime.strptime(clean_val[:19], '%Y:%m:%d %H:%M:%S').replace(tzinfo=timezone.utc)
                            except ValueError:
                                pass
    except Exception:
        pass
    return None


def calculate_sha1_b64(filepath: Path) -> Optional[str]:
    """Compute Base64-encoded SHA-1 checksum of a file using 1MB chunk buffer."""
    try:
        sha1 = hashlib.sha1()
        with open(filepath, 'rb') as f:
            while chunk := f.read(1048576):  # 1MB buffer for faster disk I/O
                sha1.update(chunk)
        return base64.b64encode(sha1.digest()).decode('utf-8')
    except Exception:
        return None


def parse_immich_file_size(asset: Dict[str, Any]) -> int:
    """Extract file size in bytes from Immich asset dictionary."""
    exif = asset.get('exifInfo') or {}
    if isinstance(exif, dict) and exif.get('fileSizeInByte') is not None:
        return int(exif['fileSizeInByte'])

    for key in ('fileSizeInByte', 'size', 'originalFileSize'):
        if asset.get(key) is not None:
            return int(asset[key])

    return 0


def load_dotenv(dotenv_path: Optional[Path] = None) -> None:
    """Load environment variables from a .env file if present."""
    paths_to_check = [Path(dotenv_path)] if dotenv_path else []
    paths_to_check.extend([Path.cwd() / '.env', Path(__file__).resolve().parent.parent / '.env'])

    for path in paths_to_check:
        if path.is_file():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        key, val = line.split('=', 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key and key not in os.environ:
                            os.environ[key] = val
                break
            except Exception:
                pass
