import hashlib
from pathlib import Path

from streamlit.testing.v1 import AppTest

from antibody_manager.auth import authenticate, hash_password, session_valid, verify_password
from tests.test_app import logged_in_app


def test_password_hash_and_throttle():
    encoded = hash_password('a-test-password-only')
    assert encoded != hash_password('a-test-password-only')
    assert verify_password('a-test-password-only', encoded)
    assert not verify_password('incorrect-password', encoded)
    assert not verify_password('anything', 'invalid')
    users = {'throttle-test': encoded}
    for _ in range(5):
        assert not authenticate('throttle-test', 'wrong', users)
    assert not authenticate('throttle-test', 'a-test-password-only', users)


def test_expiry_and_revocation():
    users = {'tester': hash_password('test-only-password')}
    session = {'username': 'tester', 'started': 100, 'credential': hashlib.sha256(users['tester'].encode()).hexdigest()}
    assert session_valid(session, users, now=101)
    assert not session_valid(session, users, now=100 + 8 * 3600)
    assert not session_valid(session, {}, now=101)
    assert not session_valid(session, {'tester': hash_password('changed-test-password')}, now=101)


def test_login_gate_and_logout(tmp_path, monkeypatch):
    path = tmp_path / 'protected.db'
    monkeypatch.setenv('ANTIBODY_DB', str(path))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.secrets['accounts'] = {'gate-test': hash_password('test-only-password')}
    app.run()
    assert not app.exception
    assert not app.sidebar.radio
    assert not path.exists()
    app.text_input(key='login_username').set_value('gate-test')
    app.text_input(key='login_password').set_value('wrong')
    app.button[0].click().run()
    assert app.error
    assert not path.exists()
    app = logged_in_app()
    assert app.sidebar.radio
    next(b for b in app.sidebar.button if b.label == 'Log out').click().run()
    app.run()
    assert not app.exception
    assert not app.sidebar.radio
    assert '_auth' not in app.session_state


def test_missing_configuration_blocks_access(tmp_path, monkeypatch):
    path = tmp_path / 'protected.db'
    monkeypatch.setenv('ANTIBODY_DB', str(path))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.secrets['accounts'] = {}
    app.run()
    assert not app.exception
    assert app.error
    assert not path.exists()
