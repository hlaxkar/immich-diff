# Immich Media Diff & Deduplicator (`immich-diff`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

`immich-diff` is a powerful Python CLI utility and Docker container designed to compare local media collections (images and videos) against a self-hosted [Immich](https://immich.app/) server.

It reports exact backup coverage, identifies unmatched local files, highlights file size discrepancies, and safely separates local duplicate files into a review folder without altering your Immich library.

---

## Key Features

* **Multi-Strategy Prioritized Matching**:
  * **SHA-1 Checksum [Exact]**: Deterministic Base64 SHA-1 hashes (chunked 1MB buffer for fast I/O).
  * **EXIF Capture Date [Review]**: Matches image capture timestamps from EXIF metadata (`DateTimeOriginal`).
  * **Filename [Review]**: Matches original filenames (useful for finding compressed / lower-resolution copies on Immich).
  * **Visual pHash [Opt-in with `-s visual`]**: 64-bit Discrete Cosine Transform (DCT) perceptual hashing. Matches visually identical photos across compression (WhatsApp, Telegram), format conversions (`.HEIC` vs `.JPG` vs `.WebP`), and stripped EXIF data with zero external ML dependencies.
* **Optimized Dual Lookup Modes**:
  * **Direct Mode (Batched API Calls)**: Uses Immich's `POST /api/assets/bulk-upload-check` endpoint to verify 100+ local file checksums in **a single HTTP request** (reducing network calls by 99%).
  * **Bulk Mode**: In-memory O(1) indexed lookup after downloading full server metadata for large photo libraries.
  * **Auto Mode**: Automatically selects the optimal mode based on file count.
* **Zero-Risk Read-Only Immich Operations**:
  * Queries Immich read-only metadata and verification endpoints.
  * Never deletes or modifies assets stored on your Immich server.
* **Safe Duplicate Handling**:
  * Supports `--dry-run` to preview actions before touching any files.
  * Preserves relative folder structure when moving local duplicates.
  * Separates duplicates into isolated strategy folders (`checksum/`, `exif/`, `filename/`, and `visual/`) for easy inspection.
* **Detailed Backup Statistics Report**:
  * Full breakdown of backed up vs. unique local files.
  * Local vs. Immich file size diffs (local larger, local smaller, identical).
  * Total recoverable storage space calculation.

---

## Installation & Usage

### 1. Installation via `pip` (Local CLI)

Clone the repository and install in editable mode:

```bash
git clone https://github.com/your-username/immich-diff.git
cd immich-diff
pip install -e .
```

Verify installation:

```bash
immich-diff --help
```

---

### 2. Basic Command Usage

Run a **dry-run** comparison scan against a local media directory:

```bash
immich-diff -d /path/to/local/photos -u http://192.168.1.100:2283 -k YOUR_IMMICH_API_KEY --dry-run
```

Run a **visual similarity duplicate scan** (catches compressed, resized, and converted photos):

```bash
immich-diff -d /path/to/local/photos -k YOUR_API_KEY -s visual --similarity-threshold 92 --dry-run
```

Execute duplicate separation (moves duplicate files to `../duplicate` by default):

```bash
immich-diff -d /path/to/local/photos -u http://192.168.1.100:2283 -k YOUR_IMMICH_API_KEY --move
```

Specify a custom output directory for duplicates:

```bash
immich-diff -d /path/to/local/photos -k YOUR_API_KEY --move -o /path/to/duplicates_folder
```

---

### 3. Docker Usage

Build and run using Docker:

```bash
# Build Docker image
docker build -t immich-diff .

# Run dry-run scan
docker run --rm \
  -v /path/to/local/media:/media:ro \
  -e IMMICH_URL="http://192.168.1.100:2283" \
  -e IMMICH_API_KEY="YOUR_API_KEY" \
  immich-diff -d /media --dry-run
```

#### Docker Compose

```yaml
version: '3.8'

services:
  immich-diff:
    build: .
    container_name: immich-diff
    environment:
      - IMMICH_URL=http://192.168.1.100:2283
      - IMMICH_API_KEY=YOUR_API_KEY_HERE
    volumes:
      - /path/to/local/media:/media:ro
    command: ["-d", "/media", "--dry-run"]
```

---

## Configuration (`.env`)

You can set default environment variables by creating a `.env` file in your working directory or project root:

```ini
IMMICH_URL=http://localhost:2283
IMMICH_API_KEY=your_immich_api_key_here
```

When `.env` is present, you can run `immich-diff -d /path/to/media` without explicitly passing `-u` or `-k`.

---

## CLI Argument Options

| Flag | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-d` | `--dir` | *(Required)* | Path to the local media directory to scan. |
| `-u` | `--url` | `http://localhost:2283` | Immich server base URL (or `IMMICH_URL` env). |
| `-k` | `--api-key` | `""` | Immich API Key (or `IMMICH_API_KEY` env). |
| `-m` | `--move` | `False` | Move duplicate files out of the local media directory. |
| `-n` | `--dry-run` | `False` | Perform a dry-run without moving any files. |
| `-o` | `--output-dir` | `../duplicate` | Custom destination directory for moved duplicate files. |
| `-s` | `--strategy` | `all` | Matching strategy (`all`, `checksum`, `exif`, `filename`, `visual`). |
| `-fm`| `--fetch-mode` | `auto` | Lookup mode (`auto`, `direct`, `bulk`). |
| `-w` | `--workers` | `auto` | Number of concurrent worker threads. |
|      | `--similarity-threshold` | `92.0` | Minimum visual similarity percentage for visual strategy. |
|      | `--max-distance` | `None` | Maximum Hamming distance for visual strategy (overrides similarity threshold). |
|      | `--cache-dir` | `~/.cache/immich-diff` | Directory for SQLite visual hash cache database. |
|      | `--no-cache` | `False` | Disable reading or writing to SQLite hash cache. |
|      | `--download-matched` | `False` | Download matching Immich assets to `_matched` directory when moving duplicates. |
| `-v` | `--verbose` | `False` | Enable debug logging output. |

---

## Sample Report Output

```text
================================================================================
                        IMMICH MEDIA COMPARISON REPORT                        
================================================================================
Scanned Directory: /volume1/Photos/2024
Immich Server:    http://192.168.1.100:2283/api
Match Strategy:   ALL
Lookup Mode:      DIRECT (Batched API calls)
Execution Mode:   [DRY-RUN MODE] (No files will be moved)
--------------------------------------------------------------------------------

LOCAL MEDIA SUMMARY:
  Total Local Files Scanned: 1,250 (4.85 GB)
    - Images:                1,100 (2.10 GB)
    - Videos:                150 (2.75 GB)

IMMICH BACKUP STATUS:
  ✓ Backed Up on Immich:     1,180 files (4.50 GB) [94.40%]
    - Matched by SHA-1 Hash:     1,100 files (4.10 GB) [Exact]
    - Matched by EXIF Timestamp: 50 files (250.00 MB) [Review]
    - Matched by Filename:       30 files (150.00 MB) [Review]
  ✗ Not Backed Up (Unique):  70 files (350.00 MB) [5.60%]

FILE SIZE COMPARISON (Matched Assets):
  • Identical File Size:       1,150 files
  • Local Larger than Immich:  20 files (Difference: +45.20 MB)
  • Local Smaller than Immich: 10 files (Difference: -12.40 MB)

STORAGE IMPACT:
  Total Size of Local Duplicates: 4.50 GB
  Recoverable Storage space:     4.50 GB (if duplicates are removed)
--------------------------------------------------------------------------------

[DRY-RUN] Action Summary:
  • Would move 1,100 SHA-1 checksum matches to:  /volume1/Photos/duplicate/checksum/
  • Would move 50 EXIF timestamp matches to:     /volume1/Photos/duplicate/exif/
  • Would move 30 filename matches to:           /volume1/Photos/duplicate/filename/

To execute actual moving of files, re-run with --move flag.
================================================================================
```

---

## License

This project is licensed under the [MIT License](LICENSE).
