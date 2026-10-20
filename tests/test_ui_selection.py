from types import SimpleNamespace

from antibody_manager import ui


def inventory():
    return [dict(id=i, name=f'AB {i}', kind='Primary', host=host, reactivity='Mouse',
                 company='', location='Box 1', dilutions={}, rating=None)
            for i, host in [(1, 'Rabbit'), (2, 'Mouse'), (3, 'Rabbit')]]


def mock_table(monkeypatch, state):
    rendered = {}
    def editor(frame, **kwargs):
        rendered.update(frame=frame, **kwargs)
    monkeypatch.setattr(ui, 'st', SimpleNamespace(session_state=state, caption=lambda *a: None,
                                                data_editor=editor, info=lambda *a: None))
    return rendered


def edit(state, rendered, changes):
    state[rendered['key']] = {'edited_rows': changes}
    rendered['on_change']()


def test_search_selections_keep_ids_and_checkbox_alignment(monkeypatch):
    state = {'chosen': []}
    rendered = mock_table(monkeypatch, state)
    rows = inventory()
    ui.table([rows[1]], 'table', 'chosen', inventory=rows)
    first_key = rendered['key']
    edit(state, rendered, {0: {'Selected': True}})
    assert state['chosen'] == [2]
    ui.table(rows, 'table', 'chosen', inventory=rows)
    assert rendered['key'] != first_key
    assert list(rendered['frame'].data['Selected']) == [False, True, False]
    ui.table([rows[0]], 'table', 'chosen', inventory=rows)
    edit(state, rendered, {0: {'Selected': True}})
    assert state['chosen'] == [2, 1]
    ui.table(rows, 'table', 'chosen', inventory=rows)
    assert list(rendered['frame'].data['Selected']) == [True, True, False]
    ui.table([rows[1]], 'table', 'chosen', inventory=rows)
    edit(state, rendered, {0: {'Selected': False}})
    assert state['chosen'] == [1]
    ui.table([], 'table', 'chosen', inventory=rows)
    assert state['chosen'] == [1]


def test_hidden_selections_still_block_conflicts(monkeypatch):
    state = {'chosen': [1]}
    rendered = mock_table(monkeypatch, state)
    rows = inventory()
    ui.table([rows[2]], 'table', 'chosen', inventory=rows, smart=True)
    frame = rendered['frame']
    assert frame.data.iloc[0]['Selection status'].startswith('Unavailable:')
    assert ('color', '#888888') in frame._compute().ctx[(0, 1)]
    edit(state, rendered, {0: {'Selected': True}})
    assert state['chosen'] == [1]
    previous_key = rendered['key']
    ui.table([rows[2]], 'table', 'chosen', inventory=rows, smart=True)
    assert rendered['key'] != previous_key
    assert not rendered['frame'].data.iloc[0]['Selected']


def test_storage_unchecking_clears_selection(monkeypatch):
    state = {'chosen': 1}
    rendered = mock_table(monkeypatch, state)
    ui.table(inventory(), 'table', 'chosen', storage_view=True, multi=False)
    edit(state, rendered, {0: {'Selected': False}})
    assert state['chosen'] is None
