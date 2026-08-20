"""
Immich API client implementation.
"""

import json
import shutil
import sys
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from .utils import normalize_api_url, tqdm


class ImmichClient:
    """Encapsulates Immich API network calls, authentication, and URL handling."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = normalize_api_url(base_url)
        self.api_key = api_key
        self.headers = {
            'x-api-key': self.api_key,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'ImmichMediaComparator/1.0.0'
        }

    def _post(self, endpoint: str, payload_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = json.dumps(payload_dict).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self.headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (200, 201):
                    return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"Error: Immich API authentication failed (HTTP {e.code}). Please check your API key.")
        except Exception:
            pass
        return None

    def bulk_upload_check(self, items: List[Dict[str, str]]) -> Optional[List[Dict[str, Any]]]:
        """
        Check duplicate status for a batch of assets using POST /api/assets/bulk-upload-check.
        'items' should be a list of dicts: [{'id': 'local_file_id', 'checksum': 'b64_sha1'}, ...]
        Returns list of result objects from Immich server.
        """
        if not items:
            return []
        data = self._post('assets/bulk-upload-check', {'assets': items})
        if data and isinstance(data, dict):
            return data.get('results', [])
        elif isinstance(data, list):
            return data
        return None

    def fetch_all_assets(self) -> List[Dict[str, Any]]:
        """Bulk fetch all assets from Immich API using pagination."""
        assets = []
        page = 1
        page_size = 1000

        print("--> Fetching asset metadata from Immich server...", flush=True)

        with tqdm(desc="Fetching Immich assets", unit=" assets", file=sys.stdout) as pbar:
            # Try POST /api/search/metadata
            while True:
                data = self._post('search/metadata', {'size': page_size, 'page': page, 'withExif': True})
                if not data:
                    break
                items = data.get('assets', {}).get('items', [])
                if not items:
                    break
                assets.extend(items)
                pbar.update(len(items))
                if len(items) < page_size:
                    break
                page += 1

        if assets:
            print(f"--> Successfully loaded {len(assets):,} assets from Immich.", flush=True)
            return assets

        # Fallback to GET /api/asset or GET /api/assets
        for endpoint in ['asset', 'assets']:
            try:
                print(f"--> Trying fallback endpoint GET /api/{endpoint}...", flush=True)
                req = urllib.request.Request(f"{self.base_url}/{endpoint}", headers=self.headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        res_data = json.loads(resp.read().decode('utf-8'))
                        if isinstance(res_data, list):
                            assets = res_data
                            break
                        elif isinstance(res_data, dict):
                            items = res_data.get('assets', {}).get('items') or res_data.get('items')
                            if isinstance(items, list) and items:
                                assets = items
                                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    sys.exit(f"Error: Immich API authentication failed (HTTP {e.code}). Check your API key.")
            except Exception:
                pass

        if assets:
            print(f"--> Successfully loaded {len(assets):,} assets from Immich.", flush=True)

        return assets

    def query_single_metadata(self, payload_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform a single metadata search query on Immich."""
        data = self._post('search/metadata', {**payload_dict, 'withExif': True})
        if data:
            items = data.get('assets', {}).get('items', [])
            if items:
                return items[0]
        return None

    def download_asset(self, asset: Dict[str, Any], target_file_path: Path) -> bool:
        """Download asset original or thumbnail file from Immich."""
        asset_id = asset.get('id') or asset.get('assetId')
        if not asset_id:
            return False

        endpoints = [f"assets/{asset_id}/original", f"assets/{asset_id}/thumbnail"]
        for ep in endpoints:
            try:
                url = f"{self.base_url}/{ep}"
                req = urllib.request.Request(url, headers={'x-api-key': self.api_key, 'User-Agent': 'ImmichMediaComparator/1.0.0'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        target_file_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(target_file_path, 'wb') as f:
                            shutil.copyfileobj(resp, f)
                        return True
            except Exception:
                pass
        return False
