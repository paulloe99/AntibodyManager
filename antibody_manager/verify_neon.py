"""Live PostgreSQL + Streamlit workflow verification using temporary test records."""
import argparse
import io
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from PIL import Image
from streamlit.testing.v1 import AppTest

from antibody_manager.auth import hash_password
from antibody_manager.config import ROOT, database_urls
from antibody_manager.store import Store


def check_app(app):
    assert not app.exception, 'Streamlit reported an application exception.'
    assert not app.error, 'Streamlit reported a storage or form error.'


def verify(pooled, direct):
    if not pooled or not (urlsplit(pooled).hostname or '').endswith('.neon.tech'):
        raise ValueError('Configure a Neon DATABASE_URL before running this live check.')
    os.environ.pop('ANTIBODY_DB', None)
    os.environ.pop('ANTIBODY_DATABASE_URL', None)
    os.environ['DATABASE_URL'] = pooled
    os.environ['DATABASE_URL_UNPOOLED'] = direct or pooled
    store = Store()
    assert store.database_url == pooled, 'The check did not select Neon.'
    marker = f'SETUP CHECK {uuid4().hex}'
    author = 'neon_setup_check'
    password = secrets.token_urlsafe(24)
    antibody_id = None
    try:
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['accounts'] = {author: hash_password(password)}
        app.secrets['database'] = {'url': pooled}
        app.secrets['onedrive'] = None
        app.run()
        app.text_input(key='login_username').set_value(author)
        app.text_input(key='login_password').set_value(password)
        next(button for button in app.button if button.label == 'Log in').click().run()
        check_app(app)
        print('PASS: Streamlit sign-in and pooled Neon connection.')

        app.sidebar.radio[0].set_value('Storage overview').run()
        check_app(app)
        form = next(form for form in app.get('form') if form.key == 'edit_new')
        for widget in form.text_input:
            if widget.label == 'Antibody name *':
                widget.set_value(marker)
            elif widget.label == 'Box / location *':
                widget.set_value('Setup verification only')
            elif widget.label == 'Catalog number':
                widget.set_value(marker)
        next(w for w in form.selectbox if w.label == 'Host species *').set_value('Rabbit')
        next(w for w in form.multiselect if w.label == 'Reactivity species *').set_value(['Mouse'])
        form.text_area[0].set_value('IF=500')
        next(button for button in form.button if button.label == 'Save antibody').click().run()
        check_app(app)
        antibody_id = next(a['id'] for a in store.antibodies() if a['name'] == marker)
        assert Store().antibodies(), 'Data did not persist across database connections.'
        print('PASS: Inventory creation through the Streamlit storage form; persisted in Neon.')

        app.sidebar.radio[0].set_value('Experimental planning').run()
        app.text_input(key='primary_search').set_value(marker).run()
        app.multiselect(key='primary_selected').set_value([antibody_id]).run()
        check_app(app)
        assert any('Calculate solutions' in h.value for h in app.subheader)
        print('PASS: Planning search, selection and solution calculation using the saved inventory.')

        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'green').save(image, format='PNG')
        store.add_review(antibody_id, author, 'IF', 4, 'Setup image test', [('setup-check.png', image.getvalue())])
        review = store.reviews(antibody_id)[0]
        assert store.image_data(review['images'][0]) == image.getvalue(), 'Image bytes did not round-trip.'
        app.sidebar.radio[0].set_value('AB Rating').run()
        app.text_input(key='ratings_Primary_search').set_value(marker).run()
        check_app(app)
        assert not any('Picture unavailable' in warning.value for warning in app.warning)
        assert app.tabs[0].metric[0].value == '4.00 / 5'
        print('PASS: Review and picture stored in Neon, downloaded byte-for-byte and displayed in Streamlit.')

        form = next(form for form in app.get('form') if form.key == f"update_review_{review['id']}")
        form.select_slider[0].set_value(5)
        form.text_area[0].set_value('Setup update verified')
        next(b for b in form.button if b.label == 'Update review').click().run()
        check_app(app)
        assert store.reviews(antibody_id)[0]['rating'] == 5
        next(b for b in app.tabs[0].button if b.label == 'Delete review').click().run()
        check_app(app)
        assert store.reviews(antibody_id) == []
        print('PASS: Review update and deletion through Streamlit persisted in Neon.')
    finally:
        # Only remove the unique records this check created. No lab records touched.
        with store.connection() as db:
            found = db.execute('SELECT id FROM antibodies WHERE name=? AND catalog=?', (marker, marker)).fetchone()
            if found:
                db.execute('DELETE FROM images WHERE review_id IN (SELECT id FROM reviews WHERE antibody_id=?)', (found['id'],))
                db.execute('DELETE FROM reviews WHERE antibody_id=?', (found['id'],))
                db.execute('DELETE FROM antibodies WHERE id=?', (found['id'],))
        print('PASS: Temporary verification records cleaned up.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    try:
        if args.env_file:
            if not args.env_file.is_file():
                raise ValueError('Environment file not found.')
            values = dotenv_values(args.env_file)
            pooled, direct = values.get('DATABASE_URL'), values.get('DATABASE_URL_UNPOOLED')
        else:
            pooled, direct = database_urls()
        verify(pooled, direct)
        print('Neon end-to-end verification passed.')
    except Exception as exc:
        # Avoid dumping connection strings, browser-test credentials or SQL parameters.
        print(f'Neon verification failed: {type(exc).__name__}.')
        if isinstance(exc, AssertionError):
            print(str(exc))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
