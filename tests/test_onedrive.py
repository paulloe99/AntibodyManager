import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from antibody_manager import onedrive
from antibody_manager.store import Store


def picture():
    buffer = io.BytesIO()
    Image.new('I;16', (8, 8)).save(buffer, format='TIFF')
    return buffer.getvalue()


class FakePictures:
    def __init__(self):
        self.files = {}
        self.fail_upload = False
        self.fail_delete = False

    def upload(self, name, data):
        if self.fail_upload:
            raise onedrive.ImageStorageError('Upload failed')
        key = f'file-{len(self.files)}'
        self.files[key] = data
        return key

    def download(self, key):
        if key not in self.files:
            raise onedrive.ImageStorageError('Not available')
        return self.files[key]

    def delete(self, key):
        if self.fail_delete:
            raise onedrive.ImageStorageError('Delete failed')
        self.files.pop(key, None)


def seeded(tmp_path, files):
    store = Store(tmp_path / 'test.db', image_storage=files)
    store.seed_demo()
    return store, store.antibodies()[0]['id']


def test_external_picture_roundtrip_and_legacy_pictures(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    original = picture()
    store.add_review(antibody_id, 'Laura', 'IF', 5, 'External', [('result.tif', original)])
    image = store.reviews(antibody_id)[0]['images'][0]
    assert image['data'] == b'' and image['storage_provider'] == 'onedrive'
    assert store.image_data(image) == original
    assert image['name'] == 'result.tif'
    # A restart still reads the same remote reference, without a cached local copy.
    restarted = Store(store.path, image_storage=files)
    assert restarted.image_data(restarted.reviews(antibody_id)[0]['images'][0]) == original
    legacy = Store(store.path)
    legacy.add_review(antibody_id, 'Laura', 'WB', 4, 'Database', [('old.tif', original)])
    images = [image for review in store.reviews(antibody_id) for image in review['images']]
    assert all(store.image_data(image) == original for image in images)
    external = next(i for i in images if i['storage_provider'] == 'onedrive')
    with pytest.raises(ValueError, match='Connect OneDrive'):
        legacy.image_data(external)


def test_failed_upload_and_db_failure_remove_unpublished_files(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    data = picture()
    real_upload = files.upload
    def failing_upload(name, data):
        if name == 'bad.tif':
            raise onedrive.ImageStorageError('Upload failed')
        return real_upload(name, data)
    files.upload = failing_upload
    with pytest.raises(ValueError):
        store.add_review(antibody_id, 'Laura', 'IF', 5, '', [('good.tif', data), ('bad.tif', data)])
    assert store.reviews(antibody_id) == [] and files.files == {}
    # Upload succeeds but review insert fails its FK: uploaded file is removed.
    with pytest.raises(Exception):
        store.add_review(99999, 'Laura', 'IF', 5, '', [('good.tif', data)])
    assert files.files == {}


def test_delete_retry_queue_and_author_check(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    store.add_review(antibody_id, 'Laura', 'IF', 5, '', [('result.tif', picture())])
    review = store.reviews(antibody_id)[0]
    with pytest.raises(ValueError):
        store.delete_review(review['id'], 'Other')
    assert len(files.files) == 1
    files.fail_delete = True
    store.delete_review(review['id'], 'Laura')
    assert store.reviews(antibody_id) == [] and len(files.files) == 1
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM image_cleanup').fetchone()[0] == 1
    files.fail_delete = False
    Store(store.path, image_storage=files).cleanup_images()
    assert files.files == {}
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM image_cleanup').fetchone()[0] == 0


def test_ambiguous_upload_is_queued_when_immediate_cleanup_fails(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    def ambiguous_upload(name, data):
        files.files['ambiguous'] = data
        raise onedrive.ImageStorageError('Response lost', cleanup_reference='ambiguous')
    files.upload = ambiguous_upload
    files.fail_delete = True
    with pytest.raises(ValueError, match='Response lost'):
        store.add_review(antibody_id, 'Laura', 'IF', 5, '', [('image.tif', picture())])
    assert store.reviews(antibody_id) == []
    with store.connection() as db:
        assert db.execute('SELECT storage_key FROM image_cleanup').fetchone()[0] == 'ambiguous'
    files.fail_delete = False
    store.cleanup_images()
    assert not files.files


def test_all_pictures_are_validated_before_upload(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    with pytest.raises(ValueError, match='valid image'):
        store.add_review(antibody_id, 'Laura', 'IF', 5, '', [('good.tif', picture()), ('bad.tif', b'bad')])
    assert not files.files and not store.reviews(antibody_id)


def test_large_review_picture_is_reduced_before_onedrive_upload(tmp_path):
    files = FakePictures()
    store, antibody_id = seeded(tmp_path, files)
    buffer = io.BytesIO()
    Image.new('RGB', (3000, 1800), 'blue').save(buffer, format='PNG')
    store.add_review(antibody_id, 'Laura', 'IF', 5, '', [('large.png', buffer.getvalue())])
    image = store.reviews(antibody_id)[0]['images'][0]
    assert image['name'] == 'large_review.jpg'
    data = files.files[image['storage_key']]
    assert len(data) <= 1024 * 1024
    with Image.open(io.BytesIO(data)) as saved:
        assert max(saved.size) <= 2048


def response(status=200, payload=None, data=b'', headers=None):
    return SimpleNamespace(status_code=status, json=lambda: payload, headers=headers or {},
                           iter_content=lambda _: iter([data]), close=lambda: None)


def client():
    return onedrive.OneDriveImages('tenant', 'client', 'very-secret', 'drive', 'folder')


def test_graph_upload_unique_names_and_cached_token(monkeypatch):
    token_calls, graph_calls = [], []
    monkeypatch.setattr(onedrive.requests, 'post', lambda *a, **kw:
        token_calls.append(kw) or response(payload={'access_token': 'token', 'expires_in': 3600}))
    monkeypatch.setattr(onedrive.requests, 'request', lambda *a, **kw:
        graph_calls.append((a, kw)) or response(201, {'id': 'item'}))
    storage = client()
    ref = storage.upload('sample.tiff', b'original bytes')
    storage.upload('sample.tiff', b'other bytes')
    assert json.loads(ref) == {'drive_id': 'drive', 'item_id': 'item'}
    assert len(token_calls) == 1
    assert graph_calls[0][0][1] != graph_calls[1][0][1]
    assert graph_calls[0][1]['data'] == b'original bytes'
    assert graph_calls[0][1]['headers']['Authorization'] == 'Bearer token'


def test_download_redirect_does_not_forward_token(monkeypatch):
    monkeypatch.setattr(onedrive.OneDriveImages, '_access_token', lambda _: 'secret-token')
    monkeypatch.setattr(onedrive.requests, 'request', lambda *a, **kw:
        response(302, headers={'Location': 'https://storage.example/download'}))
    calls = []
    monkeypatch.setattr(onedrive.requests, 'get', lambda *a, **kw:
        calls.append((a, kw)) or response(data=b'original bytes'))
    ref = json.dumps({'drive_id': 'drive', 'item_id': 'item'})
    assert client().download(ref) == b'original bytes'
    assert 'headers' not in calls[0][1]


def test_api_errors_are_safe_and_failed_upload_has_cleanup_reference(monkeypatch):
    monkeypatch.setattr(onedrive.OneDriveImages, '_access_token', lambda _: 'secret-token')
    def failure(*a, **kw):
        raise requests.ConnectionError('details containing secret-token and very-secret')
    monkeypatch.setattr(onedrive.requests, 'request', failure)
    with pytest.raises(onedrive.ImageStorageError) as error:
        client().upload('sample.tif', b'data')
    assert 'secret' not in str(error.value)
    assert json.loads(error.value.cleanup_reference)['filename'].endswith('.tif')


def test_authentication_failure_is_safe(monkeypatch):
    monkeypatch.setattr(onedrive.requests, 'post', lambda *a, **kw: response(403))
    with pytest.raises(onedrive.ImageStorageError, match='authentication failed') as error:
        client()._access_token()
    assert 'very-secret' not in str(error.value)


def test_missing_or_incomplete_settings():
    assert onedrive.from_config(None) is None
    with pytest.raises(ValueError, match='Complete'):
        onedrive.from_config({})


def test_ambiguous_upload_cleanup_uses_original_folder_path(monkeypatch):
    calls = []
    storage = client()
    monkeypatch.setattr(storage, '_graph', lambda method, path: calls.append((method, path)) or response(404))
    storage.delete(json.dumps({'drive_id': 'old-drive', 'folder_id': 'old-folder', 'filename': 'unique.tif'}))
    assert calls == [('DELETE', '/drives/old-drive/items/old-folder:/unique.tif')]
