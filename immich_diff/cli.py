"""
CLI entry point and terminal report renderer for Immich Diff.
"""

import argparse
import os
from pathlib import Path
import sys

from .client import ImmichClient
from .comparator import (
    compare_local_files,
    process_duplicate_moves,
    scan_local_media,
)
from .utils import format_size, load_dotenv


def main():
    """Main CLI entrypoint for immich-diff."""
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="immich-diff",
        description="Compare local media files against an Immich server and handle duplicates.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-d', '--dir', required=True, type=str, help="Path to local media directory")
    parser.add_argument('-u', '--url', default=os.getenv('IMMICH_URL', 'http://localhost:2283'), type=str, help="Immich server base URL")
    parser.add_argument('-k', '--api-key', default=os.getenv('IMMICH_API_KEY', ''), type=str, help="Immich API Key")
    parser.add_argument('-m', '--move', action='store_true', help="Move duplicate files into duplicates directory")
    parser.add_argument('-n', '--dry-run', action='store_true', help="Perform dry-run without moving files")
    parser.add_argument('-o', '--output-dir', default=None, type=str, help="Directory for moved duplicates")
    parser.add_argument('-s', '--strategy', choices=['all', 'filename', 'checksum', 'exif'], default='all', help="Matching strategy")
    parser.add_argument('-fm', '--fetch-mode', choices=['auto', 'direct', 'bulk'], default='auto', help="Lookup mode: direct (targeted per-file query), bulk (download full server index), auto (smart threshold)")

    args = parser.parse_args()

    scanned_dir = Path(args.dir).resolve()
    if not scanned_dir.is_dir():
        sys.exit(f"Error: Directory '{scanned_dir}' does not exist.")

    if not args.api_key:
        sys.exit("Error: Immich API key is required. Pass --api-key or set IMMICH_API_KEY env variable.")

    is_dry_run = True if args.dry_run or not args.move else False
    client = ImmichClient(args.url, args.api_key)

    # 1. Scan local files
    print(f"--> Scanning directory: {scanned_dir} ...", flush=True)
    local_files = scan_local_media(scanned_dir)
    print(f"--> Found {len(local_files):,} local media files.", flush=True)

    if not local_files:
        print("No media files found to compare. Exiting.", flush=True)
        return

    # 2. Compare local files with Immich
    print(f"--> Connecting to Immich server at {client.base_url} (Strategy: {args.strategy.upper()})...", flush=True)
    backed_up, not_backed_up, actual_mode = compare_local_files(client, local_files, strategy=args.strategy, fetch_mode=args.fetch_mode)

    # 3. Categorize Results
    match_reasons = {'FILENAME': 0, 'CHECKSUM': 0, 'EXIF_TIMESTAMP': 0}
    match_reason_sizes = {'FILENAME': 0, 'CHECKSUM': 0, 'EXIF_TIMESTAMP': 0}
    larger_local, smaller_local, equal_size, unknown_size = [], [], [], []

    total_local_size = sum(f['size'] for f in local_files)
    total_image_count = sum(1 for f in local_files if f['type'] == 'IMAGE')
    total_image_size = sum(f['size'] for f in local_files if f['type'] == 'IMAGE')
    total_video_count = sum(1 for f in local_files if f['type'] == 'VIDEO')
    total_video_size = sum(f['size'] for f in local_files if f['type'] == 'VIDEO')

    for item_info in backed_up:
        reason = item_info['match_reason']
        match_reasons[reason] = match_reasons.get(reason, 0) + 1
        match_reason_sizes[reason] = match_reason_sizes.get(reason, 0) + item_info['local']['size']

        immich_size = item_info['immich_size']
        lf_size = item_info['local']['size']

        if immich_size > 0:
            if lf_size > immich_size:
                larger_local.append(item_info)
            elif lf_size < immich_size:
                smaller_local.append(item_info)
            else:
                equal_size.append(item_info)
        else:
            unknown_size.append(item_info)

    backed_up_size = sum(item['local']['size'] for item in backed_up)
    not_backed_up_size = sum(item['size'] for item in not_backed_up)
    backed_up_pct = (len(backed_up) / len(local_files) * 100) if local_files else 0.0
    not_backed_up_pct = (len(not_backed_up) / len(local_files) * 100) if local_files else 0.0

    mode_str = "[DRY-RUN MODE] (No files will be moved)" if is_dry_run else "[MOVE MODE] (Moving duplicates)"

    # 4. Print Summary Report
    print("\n" + "=" * 80)
    print("                        IMMICH MEDIA COMPARISON REPORT                        ")
    print("=" * 80)
    print(f"Scanned Directory: {scanned_dir}")
    print(f"Immich Server:    {client.base_url}")
    print(f"Match Strategy:   {args.strategy.upper()}")
    print(f"Lookup Mode:      {actual_mode.upper()} ({'Direct per-file queries' if actual_mode == 'direct' else 'Full server asset index'})")
    print(f"Execution Mode:   {mode_str}")
    print("-" * 80)

    print("\nLOCAL MEDIA SUMMARY:")
    print(f"  Total Local Files Scanned: {len(local_files):,} ({format_size(total_local_size)})")
    print(f"    - Images:                {total_image_count:,} ({format_size(total_image_size)})")
    print(f"    - Videos:                {total_video_count:,} ({format_size(total_video_size)})")

    print("\nIMMICH BACKUP STATUS:")
    print(f"  ✓ Backed Up on Immich:     {len(backed_up):,} files ({format_size(backed_up_size)}) [{backed_up_pct:.2f}%]")
    print(f"    - Matched by Filename:       {match_reasons.get('FILENAME', 0):,} files ({format_size(match_reason_sizes.get('FILENAME', 0))})")
    print(f"    - Matched by SHA-1 Checksum: {match_reasons.get('CHECKSUM', 0):,} files ({format_size(match_reason_sizes.get('CHECKSUM', 0))})")
    print(f"    - Matched by EXIF Timestamp: {match_reasons.get('EXIF_TIMESTAMP', 0):,} files ({format_size(match_reason_sizes.get('EXIF_TIMESTAMP', 0))})")
    print(f"  ✗ Not Backed Up (Unique):  {len(not_backed_up):,} files ({format_size(not_backed_up_size)}) [{not_backed_up_pct:.2f}%]")

    print("\nFILE SIZE COMPARISON (Matched Assets):")
    larger_diff = sum(item['diff'] for item in larger_local)
    smaller_diff = sum(abs(item['diff']) for item in smaller_local)

    print(f"  • Identical File Size:       {len(equal_size):,} files")
    print(f"  • Local Larger than Immich:  {len(larger_local):,} files (Difference: +{format_size(larger_diff)})")
    print(f"  • Local Smaller than Immich: {len(smaller_local):,} files (Difference: -{format_size(smaller_diff)})")
    if unknown_size:
        print(f"  • Unknown Immich File Size:  {len(unknown_size):,} files")

    print("\nSTORAGE IMPACT:")
    print(f"  Total Size of Local Duplicates: {format_size(backed_up_size)}")
    print(f"  Recoverable Storage space:     {format_size(backed_up_size)} (if duplicates are removed)")

    # 5. Move Duplicates or Report Actions
    duplicates_base_dir = Path(args.output_dir).resolve() if args.output_dir else scanned_dir.parent / "duplicate"
    filename_duplicates = [item for item in backed_up if item['match_reason'] == 'FILENAME']
    review_duplicates = [item for item in backed_up if item['match_reason'] in ('CHECKSUM', 'EXIF_TIMESTAMP')]

    print("-" * 80)
    if backed_up:
        if is_dry_run:
            print("\n[DRY-RUN] Action Summary:")
            if filename_duplicates:
                print(f"  • Would move {len(filename_duplicates):,} filename-matched duplicates to: {duplicates_base_dir}")
            if review_duplicates:
                print(f"  • Would move {len(review_duplicates):,} checksum/EXIF matched files to: {duplicates_base_dir}/.../_review/")
            print("\nTo execute actual moving of files, re-run with --move flag.")
        else:
            if filename_duplicates:
                print(f"\n[MOVE] Moving {len(filename_duplicates):,} filename duplicates to {duplicates_base_dir} ...")
                moved, errs = process_duplicate_moves(client, filename_duplicates, duplicates_base_dir, is_review=False, desc="Moving filename duplicates")
                print(f"Successfully moved {moved:,} filename duplicates (Errors: {errs}).")

            if review_duplicates:
                print(f"\n[MOVE] Moving {len(review_duplicates):,} SHA-1 / EXIF matches for review to {duplicates_base_dir} ...")
                moved, errs = process_duplicate_moves(client, review_duplicates, duplicates_base_dir, is_review=True, desc="Moving review files")
                print(f"Successfully moved {moved:,} review files (Errors: {errs}).")
    else:
        print("\nNo duplicate files found to move.")

    print("=" * 80 + "\n")


if __name__ == '__main__':
    main()
