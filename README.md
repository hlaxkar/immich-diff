# Immich Media Comparator & Deduplicator (`immich-comparator`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

`immich-comparator` is a powerful Python CLI utility and Docker container designed to compare local media collections (images and videos) against a self-hosted [Immich](https://immich.app/) server.

It reports exact backup coverage, identifies unmatched local files, highlights file size discrepancies, and safely separates local duplicate files into a review folder without altering your Immich library.

---

## Key Features

* **Multi-Strategy Matching**:
  * **Filename**: Matches original filenames.
  * **SHA-1 Checksum**: Compares Base64 SHA-1 hashes (chunked 1MB buffer for fast I/O).
  * **EXIF Capture Date**: Matches image capture timestamps from EXIF metadata.
* **Dual Lookup Modes**:
  * **Direct Mode**: Fast per-file target API queries ideal for smaller folders.
  * **Bulk Mode**: In-memory O(1) indexed lookup after downloading full server metadata for large photo libraries.
  * **Auto Mode**: Automatically switches between Direct and Bulk mode based on local file count threshold.
* **Zero-Risk Read-Only Immich Operations**:
  * Queries Immich read-only metadata endpoints (`/api/search/metadata`).
  * Never deletes or modifies assets stored on your Immich server.
* **Safe Duplicate Handling**:
  * Supports `--dry-run` to preview actions before touching any files.
  * Preserves relative folder structure when moving local duplicates.
  * Isolates SHA-1 and EXIF matches into `_review` subdirectories and optionally downloads the matching Immich asset to `_matched` for side-by-side verification.
* **Detailed Backup Statistics Report**:
  * Full breakdown of backed up vs. unique local files.
  * Local vs. Immich file size diffs (local larger, local smaller, identical).
  * Total recoverable storage space calculation.

---

## Installation & Usage

### 1. Installation via `pip` (Local CLI)

Clone the repository and install in editable mode:

```bash
git clone https://github.com/your-username/immich-comparator.git
cd immich-comparator
pip install -e .
```

Verify installation:

```bash
immich-comparator --help
```

---

### 2. Basic Command Usage

Run a **dry-run** comparison scan against a local media directory:

```bash
immich-comparator -d /path/to/local/photos -u http://192.168.1.100:2283 -k YOUR_IMMICH_API_KEY --dry-run
```

Execute duplicate separation (moves duplicate files to `../duplicate` by default):

```bash
immich-comparator -d /path/to/local/photos -u http://192.168.1.100:2283 -k YOUR_IMMICH_API_KEY --move
```

Specify a custom output directory for duplicates:

```bash
immich-comparator -d /path/to/local/photos -k YOUR_API_KEY --move -o /path/to/duplicates_folder
```

---

### 3. Docker Usage

Build and run using Docker:

```bash
# Build Docker image
docker build -t immich-comparator .

# Run dry-run scan
docker run --rm \
  -v /path/to/local/media:/media:ro \
  -e IMMICH_URL="http://192.168.1.100:2283" \
  -e IMMICH_API_KEY="YOUR_API_KEY" \
  immich-comparator -d /media --dry-run
```

#### Docker Compose

```yaml
version: '3.8'

services:
  immich-comparator:
    build: .
    container_name: immich-comparator
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

When `.env` is present, you can run `immich-comparator -d /path/to/media` without explicitly passing `-u` or `-k`.

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
| `-s` | `--strategy` | `all` | Matching strategy (`all`, `filename`, `checksum`, `exif`). |
| `-fm`| `--fetch-mode` | `auto` | Lookup mode (`auto`, `direct`, `bulk`). |

---

## Sample Report Output

```text
================================================================================
                        IMMICH MEDIA COMPARISON REPORT                        
================================================================================
Scanned Directory: /volume1/Photos/2024
Immich Server:    http://192.168.1.100:2283/api
Match Strategy:   ALL
Lookup Mode:      DIRECT (Direct per-file queries)
Execution Mode:   [DRY-RUN MODE] (No files will be moved)
--------------------------------------------------------------------------------

LOCAL MEDIA SUMMARY:
  Total Local Files Scanned: 1,250 (4.85 GB)
    - Images:                1,100 (2.10 GB)
    - Videos:                150 (2.75 GB)

IMMICH BACKUP STATUS:
  ✓ Backed Up on Immich:     1,180 files (4.50 GB) [94.40%]
    - Matched by Filename:       1,100 files (4.10 GB)
    - Matched by SHA-1 Checksum: 50 files (250.00 MB)
    - Matched by EXIF Timestamp: 30 files (150.00 MB)
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
  • Would move 1,100 filename-matched duplicates to: /volume1/Photos/duplicate
  • Would move 80 checksum/EXIF matched files to: /volume1/Photos/duplicate/.../_review/

To execute actual moving of files, re-run with --move flag.
================================================================================
```

---

## License

This project is licensed under the [MIT License](LICENSE).
