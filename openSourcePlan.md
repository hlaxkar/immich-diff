# Open Source Launch Plan: `immich-diff`

Transform the single-file script into a production-ready, open-source Python CLI package and Docker container (`immich-diff`) designed for community adoption.

## User Review Required

> [!IMPORTANT]
> **Repository Name & Structure**
> We propose creating a clean project directory `immich-diff` inside the current workspace. This keeps the new open-source structure isolated while preparing it for `git push` to a new GitHub repository.

> [!NOTE]
> **License Choice**
> We recommend using the **MIT License** for maximum open-source adoption and ease of use, matching most python CLI utilities. Alternatively, AGPLv3 can be used if you prefer aligning with Immich's license model.

## Open Questions

> [!IMPORTANT]
> 1. **Initial Scope**: Should we include local file hash caching (SQLite/JSON cache to skip re-hashing large libraries on subsequent runs) in `v1.0.0` or defer to `v1.1.0`?
> 2. **Package Publishing**: Do you plan to publish this package to PyPI (`pip install immich-diff`) and GitHub Container Registry (`ghcr.io`) upon release?

---

## Proposed Changes

### Project Packaging & Config

#### [NEW] [`pyproject.toml`](file:///home/harshitlaxkar/testubg/compare/pyproject.toml)
- Standard modern build system (`setuptools` / `hatchling`).
- Defines metadata (author, description, tags, license, dependencies like `tqdm`, `Pillow`).
- Registers executable script CLI entrypoint: `immich-diff = immich_diff.cli:main`.

#### [NEW] [`.gitignore`](file:///home/harshitlaxkar/testubg/compare/.gitignore)
- Standard Python & Docker ignore rules (`.env`, `__pycache__`, `*.pyc`, `dist/`, `build/`, `.venv/`, `duplicate/`).

#### [NEW] [`LICENSE`](file:///home/harshitlaxkar/testubg/compare/LICENSE)
- Open-source MIT License file with copyright attribution.

#### [NEW] [`Dockerfile`](file:///home/harshitlaxkar/testubg/compare/Dockerfile) & [`docker-compose.yml`](file:///home/harshitlaxkar/testubg/compare/docker-compose.yml)
- Lightweight Python 3.11-slim container.
- Entrypoint set to `immich-diff` for running in NAS environments (Unraid, Synology, Docker Compose).

#### [NEW] [`.env.example`](file:///home/harshitlaxkar/testubg/compare/.env.example)
- Example environment file showing default parameters (`IMMICH_URL`, `IMMICH_API_KEY`).

---

### Code Modularization (`immich_diff` package)

Convert the single script into structured, clean modules:

#### [NEW] [`immich_diff/__init__.py`](file:///home/harshitlaxkar/testubg/compare/immich_diff/__init__.py)
- Package version declaration (`__version__ = "1.0.0"`).

#### [NEW] [`immich_diff/utils.py`](file:///home/harshitlaxkar/testubg/compare/immich_diff/utils.py)
- Helper functions: `format_size()`, `normalize_api_url()`, `calculate_sha1_b64()`, `extract_exif_datetime()`, `load_dotenv()`.
- Resilient fallbacks for optional `tqdm` and `Pillow` dependencies.

#### [NEW] [`immich_diff/client.py`](file:///home/harshitlaxkar/testubg/compare/immich_diff/client.py)
- `ImmichClient` class handling API pagination (`/api/search/metadata`, `/api/asset`), asset downloading, and single metadata queries.

#### [NEW] [`immich_diff/comparator.py`](file:///home/harshitlaxkar/testubg/compare/immich_diff/comparator.py)
- Core logic: `scan_local_media()`, `build_immich_index()`, `compare_local_files()` (threaded bulk/direct matching), `safe_move_file()`, and `process_duplicate_moves()`.

#### [NEW] [`immich_diff/cli.py`](file:///home/harshitlaxkar/testubg/compare/immich_diff/cli.py)
- CLI argument parsing (`argparse`), terminal status output formatting, summary report generation, and process execution.

---

### Documentation

#### [NEW] [`README.md`](file:///home/harshitlaxkar/testubg/compare/README.md)
- Professional open-source documentation:
  - Feature highlights (Direct vs Bulk lookup, Filename/Checksum/EXIF matching, zero-risk read-only Immich queries).
  - Quick Start guides for `pip`, `docker`, and standalone execution.
  - Full CLI flag options breakdown table.
  - Sample report text box example.

---

## Verification Plan

### Automated Tests
- Syntax check & import verification:
  ```bash
  python3 -m py_compile immich_diff/*.py
  ```
- Package installation in editable mode:
  ```bash
  pip install -e /home/harshitlaxkar/testubg/compare
  immich-diff --help
  ```

### Manual Verification
- Execute a `--dry-run` test scan using `immich-diff` against a test local directory and an active Immich instance.
- Verify report formatting, matching accuracy, and progress bar updates.
- Test Docker image build:
  ```bash
  docker build -t immich-diff /home/harshitlaxkar/testubg/compare
  ```
