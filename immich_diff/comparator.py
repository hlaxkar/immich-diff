"""
Core media scanner, indexing engine, comparison logic, and duplicate handling.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import logging
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
    parse_iso_datetime,
    to_extended_path,
    tqdm,
)

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = min(8, os.cpu_count() or 4)


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
                    logger.warning("Could not access file %s: %s", full_path, e)
    return local_files


def build_immich_index(assets: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """
    Build in-memory lookup indexes for Immich assets.
    Handles multiple assets sharing identical filenames or EXIF timestamps.
    Returns (by_filename, by_checksum, by_exif_timestamp).
    """
    by_filename: Dict[str, List[Dict[str, Any]]] = {}
    by_checksum: Dict[str, Dict[str, Any]] = {}
    by_exif: Dict[str, List[Dict[str, Any]]] = {}

    for asset in assets:
        # Index by original filename (list of assets)
        fname = asset.get('originalFileName') or asset.get('filename')
        if fname:
            key = fname.lower()
            if key not in by_filename:
                by_filename[key] = []
            by_filename[key].append(asset)

        # Index by SHA-1 checksum (unique)
        checksum = asset.get('checksum')
        if checksum and checksum not in by_checksum:
            by_checksum[checksum] = asset

        # Index by EXIF file datetime / dateTimeOriginal
        exif = asset.get('exifInfo') or {}
        dt_str = exif.get('dateTimeOriginal') or asset.get('fileCreatedAt')
        if dt_str and isinstance(dt_str, str):
            clean_dt = dt_str[:19].replace(' ', 'T')
            if clean_dt not in by_exif:
                by_exif[clean_dt] = []
            by_exif[clean_dt].append(asset)

    return by_filename, by_checksum, by_exif


def compare_local_files(
    client: ImmichClient,
    local_files: List[Dict[str, Any]],
    strategy: str = 'all',
    fetch_mode: str = 'auto',
    max_workers: int = DEFAULT_WORKERS,
    batch_size: int = 100,
    cache: Optional[Any] = None,
    max_distance: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
    force_refresh: bool = False
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    Compare local files against Immich assets with deterministic safety checks.
    Order of operations when strategy='all':
      1. Checksum (exact cryptographic match)
      2. EXIF Timestamp (+ Size verification)
      3. Filename (+ Size verification)
    When strategy='visual':
      Performs perceptual hashing (pHash) and Hamming distance comparison against Immich thumbnails.
    Returns (backed_up_items, not_backed_up_files, actual_fetch_mode).
    """
    backed_up = []
    not_backed_up = []

    # Visual perceptual hash matching strategy (opt-in)
    if strategy == 'visual':
        from datetime import datetime, timezone
        from .cache import HashCache
        from .visual import compute_phash, hamming_distance, similarity_percentage

        if cache is None:
            cache = HashCache(enabled=True)

        if max_distance is not None:
            effective_max_dist = max_distance
        elif similarity_threshold is not None:
            effective_max_dist = int((100.0 - similarity_threshold) / 100.0 * 64)
        else:
            effective_max_dist = 5  # default ~92.2% similarity (Hamming distance <= 5)

        actual_mode = 'bulk'
        immich_assets = client.fetch_all_assets(cache=cache, force_refresh=force_refresh)
        if not immich_assets:
            return [], list(local_files), actual_mode

        # 1. Retrieve cached Immich visual hashes
        asset_ids = [a.get('id') or a.get('assetId') for a in immich_assets if (a.get('id') or a.get('assetId'))]
        cached_immich = cache.get_immich_hashes(asset_ids) if cache.enabled else {}

        missing_assets = [a for a in immich_assets if (a.get('id') or a.get('assetId')) not in cached_immich]

        # 2. Fetch and index any missing Immich thumbnails
        if missing_assets:
            def fetch_and_hash_immich(asset: Dict[str, Any]) -> Tuple[str, Optional[int]]:
                aid = asset.get('id') or asset.get('assetId')
                thumb = client.fetch_asset_thumbnail(aid)
                if thumb:
                    h = compute_phash(thumb)
                    return aid, h
                return aid, None

            new_entries = []
            now_iso = datetime.now(timezone.utc).isoformat()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(fetch_and_hash_immich, a): a for a in missing_assets}
                for f in tqdm(as_completed(futures), total=len(futures), desc="Indexing Immich visual hashes", file=sys.stdout):
                    aid, h = f.result()
                    if h is not None:
                        cached_immich[aid] = h
                        new_entries.append((aid, h, now_iso))

            if new_entries and cache.enabled:
                cache.set_immich_hashes_batch(new_entries)

        # Build list of valid (asset, phash) pairs
        immich_hash_pairs = []
        for asset in immich_assets:
            aid = asset.get('id') or asset.get('assetId')
            h = cached_immich.get(aid)
            if h is not None:
                immich_hash_pairs.append((asset, h))

        # 3. Hash local files and find visual duplicates
        def evaluate_local_visual(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[int]]:
            fpath = lf['full_path']
            try:
                st = fpath.stat()
                mtime, size = st.st_mtime, st.st_size
            except OSError:
                return lf, None, None

            local_h = cache.get_local_hash(str(fpath), mtime, size) if cache.enabled else None
            if local_h is None:
                local_h = compute_phash(fpath)
                if local_h is not None and cache.enabled:
                    cache.set_local_hash(str(fpath), mtime, size, local_h)

            if local_h is None:
                return lf, None, None

            best_asset = None
            min_dist = 65

            for asset, s_hash in immich_hash_pairs:
                d = hamming_distance(local_h, s_hash)
                if d < min_dist:
                    min_dist = d
                    best_asset = asset
                    if d == 0:
                        break

            if best_asset and min_dist <= effective_max_dist:
                return lf, best_asset, min_dist

            return lf, None, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(evaluate_local_visual, lf) for lf in local_files]
            for f in tqdm(as_completed(futures), total=len(futures), desc="Comparing local files visually with Immich", file=sys.stdout):
                lf, matched_asset, dist = f.result()
                if matched_asset is not None and dist is not None:
                    immich_size = parse_immich_file_size(matched_asset)
                    backed_up.append({
                        'local': lf,
                        'immich': matched_asset,
                        'immich_size': immich_size,
                        'diff': lf['size'] - immich_size,
                        'match_reason': 'VISUAL_SIMILARITY',
                        'hamming_distance': dist,
                        'similarity': similarity_percentage(dist)
                    })
                else:
                    not_backed_up.append(lf)

        return backed_up, not_backed_up, actual_mode

    use_fn = strategy in ('all', 'filename')
    use_cs = strategy in ('all', 'checksum')
    use_ex = strategy in ('all', 'exif')

    # Determine fetch mode: if auto, use direct mode for <= 3,000 files, bulk mode for > 3,000 files
    if fetch_mode == 'auto':
        actual_mode = 'direct' if len(local_files) <= 3000 else 'bulk'
    else:
        actual_mode = fetch_mode

    if actual_mode == 'bulk':
        immich_assets = client.fetch_all_assets(cache=cache, force_refresh=force_refresh)
        by_filename, by_checksum, by_exif = build_immich_index(immich_assets) if immich_assets else ({}, {}, {})

        def evaluate_file(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]:
            filepath = lf['full_path']
            fname = lf['filename']
            local_size = lf['size']

            # 1. SHA-1 Checksum match (highest confidence)
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

            # 2. EXIF Timestamp match
            if use_ex:
                dt = extract_exif_datetime(filepath)
                if dt:
                    iso_dt = dt.strftime('%Y-%m-%dT%H:%M:%S')
                    candidates = by_exif.get(iso_dt, [])
                    # Match candidate that matches size or first candidate
                    for candidate in candidates:
                        cand_size = parse_immich_file_size(candidate)
                        if cand_size == local_size:
                            return (lf, candidate, 'EXIF_TIMESTAMP')
                    if candidates:
                        return (lf, candidates[0], 'EXIF_TIMESTAMP')
                    elif not immich_assets:
                        t_after = (dt - timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        t_before = (dt + timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        matched = client.query_single_metadata({'takenAfter': t_after, 'takenBefore': t_before})
                        if matched:
                            return (lf, matched, 'EXIF_TIMESTAMP')

            # 3. Filename match (with size verification)
            if use_fn:
                candidates = by_filename.get(fname.lower(), [])
                # Prioritize same-size match among candidates
                for candidate in candidates:
                    cand_size = parse_immich_file_size(candidate)
                    if cand_size == local_size:
                        return (lf, candidate, 'FILENAME')
                if candidates:
                    return (lf, candidates[0], 'FILENAME')
                elif not immich_assets:
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
        # 1. Compute SHA-1 checksums for local files in parallel if checksum matching is enabled
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
                        for res in results:
                            file_id = res.get('id')
                            action = res.get('action')
                            reason = res.get('reason')
                            server_asset_id = res.get('assetId')

                            if (action == 'reject' or reason == 'duplicate') and file_id in path_to_lf:
                                lf = path_to_lf[file_id]
                                matched_asset = {
                                    'id': server_asset_id,
                                    'checksum': local_checksum_map.get(file_id)
                                }
                                backed_up.append({
                                    'local': lf,
                                    'immich': matched_asset,
                                    'immich_size': 0,  # bulk-upload-check returns match status without full metadata
                                    'diff': lf['size'],
                                    'match_reason': 'CHECKSUM'
                                })
                                matched_in_chunk.add(file_id)

                # Step B: Fallback queries for remaining unmatched items in chunk (EXIF or Filename)
                unmatched_in_chunk = [lf for lf in chunk if str(lf['full_path']) not in matched_in_chunk]

                if unmatched_in_chunk and (use_ex or use_fn):
                    def evaluate_file_direct(lf: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]:
                        filepath = lf['full_path']
                        fname = lf['filename']

                        if use_ex:
                            dt = extract_exif_datetime(filepath)
                            if dt:
                                t_after = (dt - timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                                t_before = (dt + timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                                matched = client.query_single_metadata({'takenAfter': t_after, 'takenBefore': t_before})
                                if matched:
                                    return (lf, matched, 'EXIF_TIMESTAMP')

                        if use_fn:
                            matched = client.query_single_metadata({'originalFileName': fname})
                            if matched:
                                return (lf, matched, 'FILENAME')

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

    # Safeguard: ensure each local file path is present at most once in backed_up
    seen_paths = set()
    deduped_backed_up = []
    for item in backed_up:
        path_key = str(item['local']['full_path'])
        if path_key not in seen_paths:
            seen_paths.add(path_key)
            deduped_backed_up.append(item)

    return deduped_backed_up, not_backed_up, actual_mode


def safe_move_file(src_path: Path, dest_path: Path) -> Path:
    """Move file to dest_path safely without overwriting existing files or self-moving."""
    src_ext = to_extended_path(src_path)
    dest_ext = to_extended_path(dest_path)

    if os.path.normcase(os.path.normpath(src_ext)) == os.path.normcase(os.path.normpath(dest_ext)):
        return Path(src_path)

    dest_dir_ext = to_extended_path(dest_path.parent)
    os.makedirs(dest_dir_ext, exist_ok=True)

    target_ext = dest_ext
    target_path = dest_path
    counter = 1

    while os.path.exists(target_ext) and os.path.normcase(os.path.normpath(target_ext)) != os.path.normcase(os.path.normpath(src_ext)):
        target_path = dest_path.parent / f"{dest_path.stem}_{counter}{dest_path.suffix}"
        target_ext = to_extended_path(target_path)
        counter += 1

    shutil.move(src_ext, target_ext)
    return target_path


def process_duplicate_moves(
    client: ImmichClient,
    items: List[Dict[str, Any]],
    base_dest_dir: Path,
    subfolder: Optional[str],
    desc: str,
    download_matched: bool = False
) -> Tuple[int, int]:
    """Helper function to move duplicate files into strategy subfolder and optionally download matching Immich assets."""
    moved_count = 0
    error_count = 0

    for item in tqdm(items, desc=desc, file=sys.stdout):
        lf = item['local']
        src_path = lf['full_path']
        rel_parent = lf['rel_path'].parent

        if subfolder:
            dest_dir = base_dest_dir / subfolder / rel_parent
        else:
            dest_dir = base_dest_dir / rel_parent
        dest_file = dest_dir / lf['filename']

        try:
            moved_dest = safe_move_file(src_path, dest_file)
            moved_count += 1

            if download_matched and item.get('immich'):
                matched_dir = moved_dest.parent / "_matched"
                orig_name = item['immich'].get('originalFileName') or item['immich'].get('filename') or moved_dest.name
                matched_file_path = matched_dir / f"{moved_dest.stem}_immich_{orig_name}"
                if not os.path.exists(to_extended_path(matched_file_path)):
                    client.download_asset(item['immich'], matched_file_path)
        except Exception as e:
            logger.error("Error moving %s -> %s: %s", src_path, dest_file, e)
            error_count += 1

    return moved_count, error_count
