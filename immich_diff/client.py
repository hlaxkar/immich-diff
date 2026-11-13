"""
Immich API client implementation with retry logic, custom exceptions, and pagination.
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from .exceptions import ImmichAPIError, ImmichAuthError, ImmichNetworkError
from .utils import normalize_api_url, to_extended_path, tqdm

logger = logging.getLogger(__name__)


class ImmichClient:
    """Encapsulates Immich API network calls, authentication, retry logic, and URL handling."""

    def __init__(self, base_url: str, api_key: str, max_retries: int = 3, retry_delay: float = 1.0, timeout: int = 30):
        self.base_url = normalize_api_url(base_url)
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.headers = {
            'x-api-key': self.api_key,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'ImmichMediaComparator/1.0.0'
        }

    def _request(self, endpoint: str, data: Optional[bytes] = None, method: str = 'GET') -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status in (200, 201):
                        resp_body = resp.read().decode('utf-8')
                        return json.loads(resp_body)
                    else:
                        raise ImmichAPIError(f"Unexpected HTTP status {resp.status} from {url}")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise ImmichAuthError(f"Immich API authentication failed (HTTP {e.code}). Please check your API key.") from e
                elif e.code >= 500 or e.code == 429:
                    last_error = ImmichAPIError(f"Immich server error (HTTP {e.code}): {e.reason}")
                    if attempt < self.max_retries:
                        sleep_time = self.retry_delay * (2 ** (attempt - 1))
                        logger.warning(f"HTTP {e.code} error from {url}. Retrying in {sleep_time:.1f}s (attempt {attempt}/{self.max_retries})...")
                        time.sleep(sleep_time)
                        continue
                else:
                    # Client errors (400, 404, etc.)
                    raise ImmichAPIError(f"Immich API request failed (HTTP {e.code}): {e.reason}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_error = ImmichNetworkError(f"Network error connecting to {url}: {e}")
                if attempt < self.max_retries:
                    sleep_time = self.retry_delay * (2 ** (attempt - 1))
                    logger.warning(f"Connection error connecting to {url}. Retrying in {sleep_time:.1f}s (attempt {attempt}/{self.max_retries})...")
                    time.sleep(sleep_time)
                    continue
            except json.JSONDecodeError as e:
                raise ImmichAPIError(f"Failed to decode JSON response from {url}: {e}") from e

        if last_error:
            raise last_error
        raise ImmichNetworkError(f"Failed to execute request to {url} after {self.max_retries} attempts.")

    def _post(self, endpoint: str, payload_dict: Dict[str, Any]) -> Any:
        data = json.dumps(payload_dict).encode('utf-8')
        return self._request(endpoint, data=data, method='POST')

    def _get(self, endpoint: str) -> Any:
        return self._request(endpoint, method='GET')

    def bulk_upload_check(self, items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Check duplicate status for a batch of assets using POST /api/assets/bulk-upload-check.
        'items' should be a list of dicts: [{'id': 'local_file_id', 'checksum': 'b64_sha1'}, ...]
        Returns list of result objects from Immich server.
        """
        if not items:
            return []
        try:
            data = self._post('assets/bulk-upload-check', {'assets': items})
            if data and isinstance(data, dict):
                return data.get('results', [])
            elif isinstance(data, list):
                return data
        except ImmichAPIError as e:
            logger.debug("bulk_upload_check API error: %s", e)
        return []

    def fetch_all_assets(
        self,
        show_progress: bool = True,
        cache: Optional[Any] = None,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch all assets from Immich. If a cache is provided, utilizes incremental delta sync
        (via updatedAfter filter) and loads pre-cached metadata from SQLite in milliseconds.
        """
        # Incremental Delta Sync with Cache
        if cache and getattr(cache, "enabled", False) and not force_refresh:
            cached_count = cache.get_immich_asset_count()
            last_sync = cache.get_last_asset_sync_time()

            if cached_count > 0 and last_sync:
                delta_assets = []
                page = 1
                page_size = 1000
                while True:
                    try:
                        data = self._post('search/metadata', {
                            'size': page_size,
                            'page': page,
                            'withExif': True,
                            'updatedAfter': last_sync
                        })
                    except ImmichAPIError as e:
                        logger.debug("search/metadata updatedAfter query error: %s", e)
                        break

                    if not data:
                        break
                    items = data.get('assets', {}).get('items', [])
                    if not items:
                        break
                    delta_assets.extend(items)
                    if len(items) < page_size:
                        break
                    page += 1

                if delta_assets:
                    cache.upsert_immich_assets(delta_assets)
                    if show_progress:
                        print(f"--> Incremental sync: {len(delta_assets):,} new/updated assets from Immich.", flush=True)
                else:
                    if show_progress:
                        print(f"--> Immich asset index is up-to-date ({cached_count:,} assets loaded from cache).", flush=True)

                return cache.get_all_immich_assets()

        # Full Sync from Immich Server
        assets = []
        page = 1
        page_size = 1000

        pbar = tqdm(desc="Fetching Immich assets", unit=" assets") if show_progress else None

        try:
            while True:
                try:
                    data = self._post('search/metadata', {'size': page_size, 'page': page, 'withExif': True})
                except ImmichAPIError as e:
                    # If search/metadata endpoint is not supported (e.g. 404), fall back to GET endpoints
                    if page == 1:
                        logger.debug("search/metadata not supported, attempting fallback GET: %s", e)
                        break
                    raise

                if not data:
                    break
                items = data.get('assets', {}).get('items', [])
                if not items:
                    break
                assets.extend(items)
                if pbar is not None:
                    pbar.update(len(items))
                if len(items) < page_size:
                    break
                page += 1
        finally:
            if pbar is not None:
                pbar.__exit__(None, None, None)

        if not assets:
            # Fallback to GET /api/asset or GET /api/assets for older Immich server versions
            for endpoint in ['asset', 'assets']:
                try:
                    res_data = self._get(endpoint)
                    if isinstance(res_data, list):
                        assets = res_data
                        break
                    elif isinstance(res_data, dict):
                        items = res_data.get('assets', {}).get('items') or res_data.get('items')
                        if isinstance(items, list) and items:
                            assets = items
                            break
                except ImmichDiffError as e:
                    logger.debug("Fallback endpoint %s failed: %s", endpoint, e)
                    continue

        if assets and cache and getattr(cache, "enabled", False):
            if force_refresh:
                cache.clear_immich_assets()
            cache.upsert_immich_assets(assets)

        return assets

    def query_single_metadata(self, payload_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform a single metadata search query on Immich."""
        try:
            data = self._post('search/metadata', {**payload_dict, 'withExif': True})
            if data and isinstance(data, dict):
                items = data.get('assets', {}).get('items', [])
                if items:
                    return items[0]
        except ImmichDiffError as e:
            logger.debug("query_single_metadata error: %s", e)
        return None

    def download_asset(self, asset: Dict[str, Any], target_file_path: Path) -> bool:
        """Download asset original or thumbnail file from Immich."""
        asset_id = asset.get('id') or asset.get('assetId')
        if not asset_id:
            return False

        endpoints = [f"assets/{asset_id}/original", f"assets/{asset_id}/thumbnail"]
        for ep in endpoints:
            url = f"{self.base_url}/{ep}"
            req = urllib.request.Request(url, headers={'x-api-key': self.api_key, 'User-Agent': 'ImmichMediaComparator/1.0.0'})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        target_ext = to_extended_path(target_file_path)
                        os.makedirs(os.path.dirname(target_ext), exist_ok=True)
                        with open(target_ext, 'wb') as f:
                            shutil.copyfileobj(resp, f)
                        return True
            except Exception as e:
                logger.debug("Failed downloading asset %s via %s: %s", asset_id, ep, e)
        return False

    def fetch_asset_thumbnail(self, asset_id: str) -> Optional[bytes]:
        """Fetch asset thumbnail bytes from Immich."""
        if not asset_id:
            return None

        endpoints = [f"assets/{asset_id}/thumbnail", f"asset/file/{asset_id}?isThumb=true"]
        for ep in endpoints:
            url = f"{self.base_url}/{ep.lstrip('/')}"
            req = urllib.request.Request(url, headers={'x-api-key': self.api_key, 'User-Agent': 'ImmichMediaComparator/1.0.0'})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        return resp.read()
            except Exception as e:
                logger.debug("Failed fetching thumbnail for asset %s via %s: %s", asset_id, ep, e)
        return None
