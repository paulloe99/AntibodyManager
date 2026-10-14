from pathlib import Path
from streamlit.testing.v1 import AppTest
from antibody_manager.auth import hash_password

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
    values = {'Antibody name *': 'Target C', 'Host species *': 'Rabbit',
              'Reactivity species (comma-separated) *': 'Human',
              'Storage location (freezer / box / position) *': 'Freezer A / Box 1'}
    for widget in app.text_input:
        if widget.label in values:
            widget.set_value(values[widget.label])
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
