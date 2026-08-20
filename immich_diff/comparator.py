"""
Core media scanner, indexing engine, comparison logic, and duplicate handling.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

from .client import ImmichClient
from .utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    calculate_sha1_b64,
    extract_exif_datetime,
    parse_immich_file_size,
    tqdm,
)


def scan_local_media(directory_path: Path) -> List[Dict[str, Any]]:
    """Recursively scan directory for images and videos."""
    local_files = []
    for root, _, files in os.walk(directory_path):
        for file in files:
            ext = Path(file).suffix.lower()
            media_type = 'IMAGE' if ext in IMAGE_EXTENSIONS else ('VIDEO' if ext in VIDEO_EXTENSIONS else None)
            if media_type:
                full_path = Path(root) / file
                try:
                    size = full_path.stat().st_size
                    rel_path = full_path.relative_to(directory_path)
                    local_files.append({
                        'full_path': full_path,
                        'rel_path': rel_path,
                        'filename': file,
                        'stem': Path(file).stem,
                        'ext': ext,
                        'size': size,
                        'type': media_type
                    })
                except OSError as e:
                    print(f"Warning: Could not access file {full_path}: {e}")
    return local_files


def build_immich_index(assets: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Build fast O(1) in-memory lookup indexes for Immich assets.
    Returns (by_filename, by_checksum, by_exif_timestamp).
    """
    by_filename: Dict[str, Dict[str, Any]] = {}
    by_checksum: Dict[str, Dict[str, Any]] = {}
    by_exif: Dict[str, Dict[str, Any]] = {}

    for asset in assets:
        # Index by original filename
        fname = asset.get('originalFileName') or asset.get('filename')
        if fname and fname.lower() not in by_filename:
            by_filename[fname.lower()] = asset

        # Index by SHA-1 checksum
        checksum = asset.get('checksum')
        if checksum and checksum not in by_checksum:
            by_checksum[checksum] = asset

        # Index by EXIF file datetime / dateTimeOriginal
        exif = asset.get('exifInfo') or {}
        dt_str = exif.get('dateTimeOriginal') or asset.get('fileCreatedAt')
        if dt_str and isinstance(dt_str, str):
            # Truncate to standard ISO format string 'YYYY-MM-DDTHH:MM:SS'
            clean_dt = dt_str[:19].replace(' ', 'T')
            if clean_dt not in by_exif:
                by_exif[clean_dt] = asset

    return by_filename, by_checksum, by_exif


def compare_local_files(
    client: ImmichClient,
    local_files: List[Dict[str, Any]],
    strategy: str = 'all',
    fetch_mode: str = 'auto',
    max_workers: int = 32,
    batch_size: int = 100
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    Compare local files against Immich assets using targeted API queries / batched checksum checks (direct) or full server indexing (bulk).
    Returns (backed_up_items, not_backed_up_files, actual_fetch_mode).
    """
    use_fn = strategy in ('all', 'filename')
    use_cs = strategy in ('all', 'checksum')
    use_ex = strategy in ('all', 'exif')

    # Determine fetch mode: if auto, use direct mode for <= 3,000 files, bulk mode for > 3,000 files
    if fetch_mode == 'auto':
        actual_mode = 'direct' if len(local_files) <= 3000 else 'bulk'
    else:
        actual_mode = fetch_mode

    backed_up = []
    not_backed_up = []

    if actual_mode == 'bulk':
        print("--> Using BULK mode: Fetching full server asset index from Immich...", flush=True)
        immich_assets = client.fetch_all_assets()
        if immich_assets:
            print(f"--> Indexing {len(immich_assets):,} Immich assets for fast lookup...", flush=True)
        by_filename, by_checksum, by_exif = build_immich_index(immich_assets) if immich_assets else ({}, {}, {})

        def evaluate_file(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]:
            filepath = lf['full_path']
            fname = lf['filename']

            # 1. Filename match
            if use_fn:
                matched = by_filename.get(fname.lower())
                if matched:
                    return (lf, matched, 'FILENAME')

            # 2. Checksum match
            if use_cs:
                checksum = calculate_sha1_b64(filepath)
                if checksum:
                    matched = by_checksum.get(checksum)
                    if matched:
                        return (lf, matched, 'CHECKSUM')
                    elif not immich_assets:
                        matched = client.query_single_metadata({'checksum': checksum})
                        if matched:
                            return (lf, matched, 'CHECKSUM')

            # 3. EXIF timestamp match
            if use_ex:
                dt = extract_exif_datetime(filepath)
                if dt:
                    iso_dt = dt.strftime('%Y-%m-%dT%H:%M:%S')
                    matched = by_exif.get(iso_dt)
                    if matched:
                        return (lf, matched, 'EXIF_TIMESTAMP')
                    elif not immich_assets:
                        t_after = (dt - timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        t_before = (dt + timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        matched = client.query_single_metadata({'takenAfter': t_after, 'takenBefore': t_before})
                        if matched:
                            return (lf, matched, 'EXIF_TIMESTAMP')

            if use_fn and not immich_assets:
                matched = client.query_single_metadata({'originalFileName': fname})
                if matched:
                    return (lf, matched, 'FILENAME')

            return (lf, None, 'NONE')

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(evaluate_file, lf): lf for lf in local_files}
            for future in tqdm(as_completed(futures), total=len(futures), desc="Comparing local files with Immich", file=sys.stdout):
                lf, matched_asset, reason = future.result()
                if matched_asset:
                    immich_size = parse_immich_file_size(matched_asset)
                    backed_up.append({
                        'local': lf,
                        'immich': matched_asset,
                        'immich_size': immich_size,
                        'diff': lf['size'] - immich_size,
                        'match_reason': reason
                    })
                else:
                    not_backed_up.append(lf)

    else:  # 'direct' mode with batched checksum checking
        print(f"--> Using DIRECT mode (Batched API calls): Processing {len(local_files):,} local files in chunks of {batch_size}...", flush=True)

        # 1. Compute SHA-1 checksums for local files in parallel
        local_checksum_map = {}
        if use_cs:
            def hash_worker(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
                return lf, calculate_sha1_b64(lf['full_path'])

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                hash_futures = [executor.submit(hash_worker, lf) for lf in local_files]
                for future in tqdm(as_completed(hash_futures), total=len(hash_futures), desc="Calculating local SHA-1 hashes", file=sys.stdout):
                    lf, h_val = future.result()
                    if h_val:
                        local_checksum_map[str(lf['full_path'])] = h_val

        # 2. Chunk local files into batches of batch_size
        file_chunks = [local_files[i:i + batch_size] for i in range(0, len(local_files), batch_size)]

        with tqdm(total=len(local_files), desc="Comparing local files with Immich", file=sys.stdout) as pbar:
            for chunk in file_chunks:
                matched_in_chunk = set()

                # Step A: Perform batched bulk-upload-check for SHA-1 checksums
                if use_cs:
                    check_items = []
                    path_to_lf = {}
                    for lf in chunk:
                        path_str = str(lf['full_path'])
                        checksum = local_checksum_map.get(path_str)
                        if checksum:
                            check_items.append({'id': path_str, 'checksum': checksum})
                            path_to_lf[path_str] = lf

                    if check_items:
                        results = client.bulk_upload_check(check_items)
                        if results:
                            for res in results:
                                file_id = res.get('id')
                                action = res.get('action')
                                reason = res.get('reason')
                                asset_id = res.get('assetId') or res.get('id')

                                if (action == 'reject' or reason == 'duplicate') and file_id in path_to_lf:
                                    lf = path_to_lf[file_id]
                                    matched_asset = {'id': asset_id, 'checksum': local_checksum_map.get(file_id)}
                                    backed_up.append({
                                        'local': lf,
                                        'immich': matched_asset,
                                        'immich_size': 0,  # bulk-upload-check returns match status
                                        'diff': lf['size'],
                                        'match_reason': 'CHECKSUM'
                                    })
                                    matched_in_chunk.add(path_str)

                # Step B: Fallback queries for remaining unmatched items in chunk (Filename or EXIF)
                unmatched_in_chunk = [lf for lf in chunk if str(lf['full_path']) not in matched_in_chunk]

                if unmatched_in_chunk and (use_fn or use_ex):
                    def evaluate_file_direct(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]:
                        filepath = lf['full_path']
                        fname = lf['filename']

                        if use_fn:
                            matched = client.query_single_metadata({'originalFileName': fname})
                            if matched:
                                return (lf, matched, 'FILENAME')

                        if use_ex:
                            dt = extract_exif_datetime(filepath)
                            if dt:
                                t_after = (dt - timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                                t_before = (dt + timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                                matched = client.query_single_metadata({'takenAfter': t_after, 'takenBefore': t_before})
                                if matched:
                                    return (lf, matched, 'EXIF_TIMESTAMP')

                        return (lf, None, 'NONE')

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [executor.submit(evaluate_file_direct, lf) for lf in unmatched_in_chunk]
                        for future in as_completed(futures):
                            lf, matched_asset, reason = future.result()
                            if matched_asset:
                                immich_size = parse_immich_file_size(matched_asset)
                                backed_up.append({
                                    'local': lf,
                                    'immich': matched_asset,
                                    'immich_size': immich_size,
                                    'diff': lf['size'] - immich_size,
                                    'match_reason': reason
                                })
                            else:
                                not_backed_up.append(lf)
                else:
                    for lf in unmatched_in_chunk:
                        not_backed_up.append(lf)

                pbar.update(len(chunk))

    return backed_up, not_backed_up, actual_mode


def safe_move_file(src_path: Path, dest_path: Path) -> Path:
    """Move file to dest_path safely without overwriting existing files."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    target = dest_path
    counter = 1
    while target.exists():
        target = dest_path.parent / f"{dest_path.stem}_{counter}{dest_path.suffix}"
        counter += 1
    shutil.move(src_path, target)
    return target


def process_duplicate_moves(
    client: ImmichClient,
    items: List[Dict[str, Any]],
    base_dest_dir: Path,
    is_review: bool,
    desc: str
) -> Tuple[int, int]:
    """Helper function to move duplicate files and download matching Immich assets."""
    moved_count = 0
    error_count = 0

    for item in tqdm(items, desc=desc, file=sys.stdout):
        lf = item['local']
        src_path = lf['full_path']
        rel_parent = lf['rel_path'].parent

        if is_review:
            dest_dir = base_dest_dir / rel_parent / "_review"
            dest_file = dest_dir / lf['filename']
        else:
            dest_dir = base_dest_dir / rel_parent
            dest_file = dest_dir / lf['filename']

        try:
            moved_dest = safe_move_file(src_path, dest_file)
            moved_count += 1

            # Download Immich matched asset to _matched directory
            matched_dir = moved_dest.parent / "_matched"
            orig_name = item['immich'].get('originalFileName') or item['immich'].get('filename') or moved_dest.name
            matched_file_path = matched_dir / f"{moved_dest.stem}_immich_{orig_name}"
            if not matched_file_path.exists():
                client.download_asset(item['immich'], matched_file_path)
        except Exception as e:
            print(f"Error moving {src_path} -> {dest_file}: {e}")
            error_count += 1

    return moved_count, error_count
