"""
Unit tests for ImmichClient module.
"""

import json
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from immich_diff.client import ImmichClient
from immich_diff.exceptions import (
    ImmichAPIError,
    ImmichAuthError,
    ImmichNetworkError,
)


class TestImmichClient(unittest.TestCase):

    def setUp(self):
        self.client = ImmichClient(
            base_url="http://localhost:2283",
            api_key="test_api_key",
            max_retries=2,
            retry_delay=0.01,
            timeout=5
        )

    def test_auth_error_raised_on_401(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://localhost:2283/api/search/metadata",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=BytesIO(b'{"message":"Unauthorized"}')
            )
            with self.assertRaises(ImmichAuthError):
                self.client._post('search/metadata', {})

    def test_auth_error_raised_on_403(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://localhost:2283/api/search/metadata",
                code=403,
                msg="Forbidden",
                hdrs={},
                fp=BytesIO(b'{"message":"Forbidden"}')
            )
            with self.assertRaises(ImmichAuthError):
                self.client._post('search/metadata', {})

    def test_retry_on_server_500_and_succeed(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"assets": {"items": [{"id": "asset-1"}]}}).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        error_500 = urllib.error.HTTPError(
            url="http://localhost:2283/api/search/metadata",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b'{"message":"Internal Error"}')
        )

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = [error_500, mock_resp]
            result = self.client._post('search/metadata', {})
            self.assertEqual(result['assets']['items'][0]['id'], 'asset-1')
            self.assertEqual(mock_urlopen.call_count, 2)

    def test_network_error_after_max_retries(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            with self.assertRaises(ImmichNetworkError):
                self.client._post('search/metadata', {})
            self.assertEqual(mock_urlopen.call_count, 2)

    def test_bulk_upload_check(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "results": [
                {"id": "/path/to/img1.jpg", "action": "reject", "reason": "duplicate", "assetId": "immich-123"}
            ]
        }).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch('urllib.request.urlopen', return_value=mock_resp):
            results = self.client.bulk_upload_check([{"id": "/path/to/img1.jpg", "checksum": "abc=="}])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['assetId'], 'immich-123')
            self.assertEqual(results[0]['action'], 'reject')

    def test_fetch_all_assets_pagination(self):
        page1_resp = MagicMock()
        page1_resp.status = 200
        page1_resp.read.return_value = json.dumps({
            "assets": {"items": [{"id": f"asset-{i}"} for i in range(1000)]}
        }).encode('utf-8')
        page1_resp.__enter__.return_value = page1_resp
        page1_resp.__exit__.return_value = None

        page2_resp = MagicMock()
        page2_resp.status = 200
        page2_resp.read.return_value = json.dumps({
            "assets": {"items": [{"id": f"asset-{i}"} for i in range(1000, 1050)]}
        }).encode('utf-8')
        page2_resp.__enter__.return_value = page2_resp
        page2_resp.__exit__.return_value = None

        with patch('urllib.request.urlopen', side_effect=[page1_resp, page2_resp]):
            assets = self.client.fetch_all_assets(show_progress=False)
            self.assertEqual(len(assets), 1050)
            self.assertEqual(assets[0]['id'], 'asset-0')
            self.assertEqual(assets[-1]['id'], 'asset-1049')

    def test_fetch_all_assets_incremental_cache(self):
        mock_cache = MagicMock()
        mock_cache.enabled = True
        mock_cache.get_immich_asset_count.return_value = 100
        mock_cache.get_last_asset_sync_time.return_value = '2024-01-01T10:00:00.000Z'
        mock_cache.get_all_immich_assets.return_value = [{'id': f'asset-{i}'} for i in range(101)]

        # Immich returns 1 new delta asset for updatedAfter query
        delta_resp = MagicMock()
        delta_resp.status = 200
        delta_resp.read.return_value = json.dumps({
            "assets": {"items": [{"id": "asset-100", "updatedAt": "2024-01-02T10:00:00.000Z"}]}
        }).encode('utf-8')
        delta_resp.__enter__.return_value = delta_resp
        delta_resp.__exit__.return_value = None

        with patch('urllib.request.urlopen', return_value=delta_resp):
            assets = self.client.fetch_all_assets(show_progress=False, cache=mock_cache)
            self.assertEqual(len(assets), 101)
            mock_cache.upsert_immich_assets.assert_called_once()
            mock_cache.get_all_immich_assets.assert_called_once()


if __name__ == '__main__':
    unittest.main()
