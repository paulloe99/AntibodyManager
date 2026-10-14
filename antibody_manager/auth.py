"""Local named-account authentication; no plaintext passwords are persisted."""
import argparse
import getpass
import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60
_attempts = {}
_lock = threading.Lock()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def valid_hash(encoded):
    try:
        algorithm, rounds, salt, digest = encoded.split('$')
        return (algorithm == 'pbkdf2_sha256' and int(rounds) == ITERATIONS
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (AttributeError, TypeError, ValueError):
        return False


def verify_password(password, encoded):
    if not valid_hash(encoded):
        return False
    _, rounds, salt, digest = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(actual, bytes.fromhex(digest))


def authenticate(username, password, users):
    """Process-wide per-account throttling also applies across browser sessions."""
    now = time.monotonic()
    # Unknown users share a bucket to keep memory bounded.
    bucket = username if username in users else '__unknown__'
    with _lock:
        failures = [stamp for stamp in _attempts.get(bucket, []) if now - stamp < 60]
        _attempts[bucket] = failures
        if len(failures) >= 5:
            return False
        encoded = users.get(username)
        # Perform the same expensive operation for unknown names.
        candidate = encoded or f'pbkdf2_sha256${ITERATIONS}${"00" * 16}${"00" * 32}'
        valid = verify_password(password, candidate) and encoded is not None
        if valid:
            _attempts.pop(bucket, None)
        else:
            failures.append(now)
        return valid


def session_valid(session, users, now=None):
    if not isinstance(session, dict):
        return False
    username = session.get('username')
    encoded = users.get(username)
    return bool(encoded and session.get('credential') == hashlib.sha256(encoded.encode()).hexdigest()
                and 0 <= (now if now is not None else time.time()) - session.get('started', 0) < SESSION_SECONDS)


def require_login():
    import streamlit as st

    try:
        users = dict(st.secrets.get('accounts', {}))
    except (FileNotFoundError, ValueError):
        users = {}
    if not users or any(not isinstance(name, str) or not name.strip() or not valid_hash(value) for name, value in users.items()):
        st.title('Antibody Manager · Login setup')
        st.error('Configure valid named accounts in .streamlit/secrets.toml before using the app.')
        st.code('.\\.venv\\Scripts\\python.exe -m antibody_manager.auth --username laura', language='powershell')
        st.info('Copy the generated account line under [accounts] in the secrets file. See README.md for setup.')
        st.stop()
    session = st.session_state.get('_auth')
    if session_valid(session, users):
        st.sidebar.caption(f"Signed in as {session['username']}")
        if st.sidebar.button('Log out'):
            st.session_state.clear()
            st.rerun()
        return session['username']
    if session:
        st.session_state.clear()
    st.title('🧪 Antibody Manager')
    st.subheader('Log in')
    with st.form('login', clear_on_submit=True):
        username = st.text_input('Username', key='login_username')
        password = st.text_input('Password', type='password', key='login_password')
        submitted = st.form_submit_button('Log in')
    if submitted:
        username = username.strip()
        if authenticate(username, password, users):
            st.session_state['_auth'] = {'username': username, 'started': time.time(),
                                         'credential': hashlib.sha256(users[username].encode()).hexdigest()}
            st.session_state.pop('login_password', None)
            st.rerun()
        st.error('Login failed. Check your credentials; after repeated failures, wait one minute before retrying.')
    st.stop()


def main():
    parser = argparse.ArgumentParser(description='Generate a named account line for Streamlit secrets.')
    parser.add_argument('--username', required=True)
    args = parser.parse_args()
    if not args.username or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.username):
        parser.error('Username must contain only letters, digits, underscores or hyphens.')
    password = getpass.getpass('New password: ')
    if password != getpass.getpass('Confirm password: '):
        parser.error('Passwords do not match.')
    encoded = hash_password(password)
    print(f'"{args.username}" = "{encoded}"')


if __name__ == '__main__':
    main()
