from pathlib import Path
from streamlit.testing.v1 import AppTest
from antibody_manager.auth import hash_password
from antibody_manager.store import Store

def logged_in_app():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.secrets['accounts'] = {'tester': hash_password('test-only-password')}
    app.run()
    app.text_input(key='login_username').set_value('tester')
    app.text_input(key='login_password').set_value('test-only-password')
    app.button[0].click().run()
    app.run()
    assert not app.exception
    return app

def test_workflows(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'test.db'))
    app = logged_in_app()
    assert not app.exception
    next(b for b in app.sidebar.button if b.label == 'Load fictional demo inventory').click().run()
    assert not app.exception
    app.multiselect[4].select(1).run()
    assert not app.exception
    assert any('Calculate solutions' in header.value for header in app.subheader)
    for page in ['Storage overview', 'AB Rating']:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception

def test_create_inventory_and_review(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'forms.db'))
    app = logged_in_app()
    app.sidebar.radio[0].set_value('Storage overview').run()
    values = {'Antibody name *': 'Target C', 'Box / location *': 'Box 1'}
    for widget in app.text_input:
        if widget.label in values:
            widget.set_value(values[widget.label])
    next(w for w in app.selectbox if w.label == 'Host species *').set_value('Rabbit')
    next(w for w in app.multiselect if w.label == 'Reactivity species *').set_value(['Human'])
    app.text_area[0].set_value('IF=250')
    next(button for button in app.button if button.label == 'Save antibody').click().run()
    assert not app.exception
    app.sidebar.radio[0].set_value('AB Rating').run()
    next(widget for widget in app.text_input if widget.label == 'Experiment / application *').set_value('IF')
    app.select_slider[0].set_value(5)
    next(button for button in app.button if button.label == 'Publish review').click().run()
    app.run()
    assert not app.exception
    assert app.metric[0].value == '5.00 / 5', ([e.value for e in app.error], [(w.label, w.value) for w in app.text_input])
    next(button for button in app.button if button.label == 'Delete review').click().run()
    assert not app.exception
    assert app.metric[0].value == 'No ratings'


def test_all_species_filter_and_smart_host_selection(tmp_path, monkeypatch):
    path = tmp_path / 'smart.db'
    monkeypatch.setenv('ANTIBODY_DB', str(path))
    store = Store(path)
    for name, reactivity in [('Universal', 'All species'), ('Mouse target', 'Mouse'), ('Human target', 'Human')]:
        store.save(dict(name=name, kind='Primary', host='Rabbit', reactivity=reactivity,
                        location='Box 1', dilutions={'IF': 500}))
    app = logged_in_app()
    app.multiselect(key='primary_reactivity').set_value(['Mouse']).run()
    choices = app.multiselect(key='primary_selected')
    assert len(choices.options) == 2
    choices.set_value([2, 1]).run()
    assert len(app.multiselect(key='primary_selected').value) == 1
    assert app.warning
    assert not app.exception


def test_selections_survive_search_changes(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'search.db'))
    store = Store()
    store.seed_demo()
    app = logged_in_app()
    app.text_input(key='primary_search').set_value('Target A').run()
    app.multiselect(key='primary_selected').set_value([1]).run()
    app.text_input(key='primary_search').set_value('Target B').run()
    assert app.multiselect(key='primary_selected').value == [1]
    app.multiselect(key='primary_selected').set_value([1, 2]).run()
    app.text_input(key='primary_search').set_value('nothing matches').run()
    assert app.multiselect(key='primary_selected').value == [1, 2]
    app.text_input(key='primary_search').set_value('').run()
    assert app.multiselect(key='primary_selected').value == [1, 2]
    app.text_input(key='secondary_search').set_value('Anti-Rabbit').run()
    app.multiselect(key='secondary_selected').set_value([3]).run()
    app.text_input(key='secondary_search').set_value('Anti-Mouse').run()
    assert app.multiselect(key='secondary_selected').value == [3]
    app.multiselect(key='secondary_selected').set_value([3, 4]).run()
    app.text_input(key='secondary_search').set_value('').run()
    assert app.multiselect(key='secondary_selected').value == [3, 4]
    assert not app.exception


def test_ratings_primary_and_secondary_tabs(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'rating_tabs.db'))
    store = Store()
    store.seed_demo()
    secondary = next(a for a in store.antibodies() if a['kind'] == 'Secondary')
    store.save(dict(secondary, catalog='CAT-123'), secondary['id'])
    app = logged_in_app()
    app.sidebar.radio[0].set_value('AB Rating').run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ['Primary antibodies', 'Secondary antibodies']
    assert not app.get('file_uploader')
    primary_choice = app.selectbox(key='ratings_Primary_antibody')
    secondary_choice = app.selectbox(key='ratings_Secondary_antibody')
    assert primary_choice.value['kind'] == 'Primary'
    assert secondary_choice.value['kind'] == 'Secondary'
    assert any(c.value == 'Catalog number: CAT-123' for c in app.caption)
    secondary_form = app.tabs[1].get('form')[0]
    secondary_form.text_input[0].set_value('IF')
    secondary_form.select_slider[0].set_value(4)
    secondary_form.button[0].click().run()
    assert not app.exception
    assert len(store.reviews(secondary['id'])) == 1
    assert store.reviews(secondary['id'])[0]['images'] == []
    assert app.tabs[1].metric[0].value == '4.00 / 5'
    app.text_input(key='ratings_Primary_search').set_value('No match').run()
    assert not app.exception
    assert app.selectbox(key='ratings_Secondary_antibody').value['id'] == secondary['id']


def test_onedrive_picture_outage_keeps_review_visible(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from antibody_manager import ui
    from tests.test_onedrive import FakePictures
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'external_images.db'))
    files = FakePictures()
    monkeypatch.setattr(ui, 'onedrive_from_config', lambda _: files)
    store = Store(image_storage=files)
    store.seed_demo()
    antibody_id = store.antibodies()[0]['id']
    picture = io.BytesIO()
    Image.new('I;16', (8, 8)).save(picture, format='TIFF')
    store.add_review(antibody_id, 'tester', 'IF', 5, 'Written review remains visible', [('result.tif', picture.getvalue())])
    app = logged_in_app()
    app.sidebar.radio[0].set_value('AB Rating').run()
    assert not app.exception
    assert not any('Picture unavailable' in warning.value for warning in app.warning)
    files.files.clear()
    app.run()
    assert not app.exception
    assert any('Picture unavailable: result.tif' in warning.value for warning in app.warning)
    assert any('Written review remains visible' in markdown.value for markdown in app.markdown)
    assert any(button.label == 'Update review' for button in app.button)


def test_cloud_deployment_requires_database_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTIBODY_DB', str(tmp_path / 'local.db'))
    app = logged_in_app()
    app.secrets['deployment'] = {'require_cloud_database': True}
    app.run()
    assert not app.exception
    assert any('Configure DATABASE_URL and DATABASE_URL_UNPOOLED' in error.value for error in app.error)
    assert not app.sidebar.radio
