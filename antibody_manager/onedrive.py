"""Microsoft 365 OneDrive image storage through Microsoft Graph."""
from functools import lru_cache
import json
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ImageStorageError(ValueError):
    """Safe message suitable for display without exposing credentials."""
    def __init__(self, message, cleanup_reference=None):
        super().__init__(message)
        self.cleanup_reference = cleanup_reference


class OneDriveImages:
    def __init__(self, tenant_id, client_id, client_secret, drive_id, folder_id):
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.drive_id, self.folder_id = drive_id, folder_id
        if not all(isinstance(v, str) and v.strip() for v in
                   (tenant_id, client_id, client_secret, drive_id, folder_id)):
            raise ImageStorageError('Complete the OneDrive connection settings.')
        self._token = None
        self._expires = 0
        self._lock = threading.Lock()

    def _access_token(self):
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            try:
                response = requests.post(
                    f'https://login.microsoftonline.com/{quote(self.tenant_id, safe="")}/oauth2/v2.0/token',
                    data={'client_id': self.client_id, 'client_secret': self.client_secret,
                          'scope': 'https://graph.microsoft.com/.default', 'grant_type': 'client_credentials'},
                    timeout=20)
                if response.status_code != 200:
                    raise ImageStorageError('OneDrive authentication failed. Check the Microsoft app credentials and permissions.')
                payload = response.json()
                self._token = payload['access_token']
                self._expires = time.monotonic() + max(0, int(payload['expires_in']) - 60)
                return self._token
            except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ImageStorageError):
                    raise
                raise ImageStorageError('Could not authenticate with OneDrive. Please try again later.') from None

    def _graph(self, method, path, **kwargs):
        for attempt in range(2):
            try:
                response = requests.request(method, f'https://graph.microsoft.com/v1.0{path}',
                    headers={'Authorization': f'Bearer {self._access_token()}',
                             'Content-Type': 'application/octet-stream'},
                    timeout=30, allow_redirects=False, **kwargs)
            except requests.RequestException:
                raise ImageStorageError('Could not reach OneDrive. Please try again later.') from None
            if response.status_code != 401 or attempt:
                return response
            response.close()
            with self._lock:
                self._token = None

    @staticmethod
    def _item_path(reference):
        try:
            item = json.loads(reference)
            drive = quote(item['drive_id'], safe='')
            if 'item_id' in item:
                return f'/drives/{drive}/items/{quote(item["item_id"], safe="")}'
            return f'/drives/{drive}/items/{quote(item["folder_id"], safe="")}:/'+quote(item['filename'], safe='')
        except (ValueError, KeyError, TypeError):
            raise ImageStorageError('The stored OneDrive picture reference is invalid.') from None

    def upload(self, name, data):
        # Unique names prevent one review's image from replacing another's image.
        filename = uuid4().hex + Path(name).suffix.lower()
        parent = f'/drives/{quote(self.drive_id, safe="")}/items/{quote(self.folder_id, safe="")}'
        path = f'{parent}:/{quote(filename, safe="")}:'
        # Keep a stable path reference even if a successful PUT response is lost.
        reference = json.dumps({'drive_id': self.drive_id, 'folder_id': self.folder_id, 'filename': filename})
        try:
            response = self._graph('PUT', path + '/content', data=data)
            if response.status_code not in (200, 201):
                raise ImageStorageError('OneDrive could not save the picture. Check folder permissions and available storage.')
            item_id = response.json()['id']
            return json.dumps({'drive_id': self.drive_id, 'item_id': item_id})
        except (ImageStorageError, KeyError, ValueError):
            raise ImageStorageError('OneDrive could not save the picture. The review was not published; please try again.',
                                    cleanup_reference=reference) from None

    def download(self, reference):
        response = self._graph('GET', self._item_path(reference) + '/content', stream=True)
        if response.status_code == 302:
            location = response.headers.get('Location', '')
            response.close()
            if urlsplit(location).scheme != 'https':
                raise ImageStorageError('OneDrive returned an invalid picture download address.')
            try:
                # The temporary download URL needs no token. Never forward the bearer token.
                response = requests.get(location, timeout=30, stream=True)
            except requests.RequestException:
                raise ImageStorageError('Could not download this OneDrive picture.') from None
        if response.status_code != 200:
            response.close()
            raise ImageStorageError('This OneDrive picture is unavailable. It may have been moved or deleted.')
        try:
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ImageStorageError('The OneDrive picture exceeds the 10 MB display limit.')
                chunks.append(chunk)
            return b''.join(chunks)
        except requests.RequestException:
            raise ImageStorageError('Could not download this OneDrive picture.') from None
        finally:
            response.close()

    def delete(self, reference):
        response = self._graph('DELETE', self._item_path(reference))
        if response.status_code not in (204, 404):
            raise ImageStorageError('Could not remove the picture from OneDrive. Cleanup will be retried.')

    def check(self):
        path = f'/drives/{quote(self.drive_id, safe="")}/items/{quote(self.folder_id, safe="")}'
        response = self._graph('GET', path)
        if response.status_code != 200 or 'folder' not in response.json():
            raise ImageStorageError('The configured OneDrive folder is unavailable.')


@lru_cache(maxsize=8)
def _client(tenant_id, client_id, client_secret, drive_id, folder_id):
    return OneDriveImages(tenant_id, client_id, client_secret, drive_id, folder_id)


def from_config(config):
    if config is None:
        return None
    return _client(*(config.get(key, '') for key in
                     ('tenant_id', 'client_id', 'client_secret', 'drive_id', 'folder_id')))


if __name__ == '__main__':
    import tomllib
    try:
        with open('.streamlit/secrets.toml', 'rb') as stream:
            client = from_config(tomllib.load(stream).get('onedrive'))
        if client is None:
            raise ImageStorageError('Add the [onedrive] section to .streamlit/secrets.toml first.')
        client.check()
        print('OneDrive authentication and folder access verified (no files changed).')
    except (OSError, ValueError):
        raise SystemExit('OneDrive check failed. Check the configuration, app permissions and folder IDs.') from None
