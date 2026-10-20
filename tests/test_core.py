import io
import pytest
from PIL import Image
from antibody_manager.domain import compatible, recipe
from antibody_manager.store import Store

def test_recipe():
    assert recipe(10, 200, 500, 10) == pytest.approx({'total_ul': 2200, 'stock_ul': 4.4, 'diluent_ul': 2195.6})

@pytest.mark.parametrize('args', [(0, 200, 500), (1, 0, 500), (1, 200, 0), (1, float('nan'), 500), (1.5, 200, 500)])
def test_invalid_recipe(args):
    with pytest.raises(ValueError):
        recipe(*args)

def test_matching():
    assert compatible({'host': ' Rabbit '}, {'kind': 'Secondary', 'reactivity': 'Mouse, rabbit'})
    assert not compatible({'host': 'Rabbit'}, {'kind': 'Secondary', 'reactivity': 'Rabbitfish'})

def test_persistence_and_upload(tmp_path):
    path = tmp_path / 'inventory.db'
    store = Store(path)
    store.seed_demo()
    a = store.antibodies()[0]
    image = io.BytesIO()
    Image.new('RGB', (10, 10)).save(image, format='PNG')
    store.add_review(a['id'], 'Laura', 'IF', 5, 'Good signal', [('result.png', image.getvalue())])
    store = Store(path)
    assert next(row for row in store.antibodies() if row['id'] == a['id'])['rating'] == 5
    assert store.reviews(a['id'])[0]['images'][0]['data'] == image.getvalue()
    a['location'] = 'Freezer B'
    store.save(a, a['id'])
    store.archive(a['id'])
    assert a['id'] not in [row['id'] for row in store.antibodies()]
    assert store.reviews(a['id'])[0]['comment'] == 'Good signal'
    store.archive(a['id'], False)
    assert next(row for row in store.antibodies() if row['id'] == a['id'])['location'] == 'Freezer B'
    with pytest.raises(ValueError):
        store.add_review(a['id'], 'Laura', 'IF', 4, '', [('fake.png', b'not an image')])
    assert len(store.reviews(a['id'])) == 1


def test_smart_planning():
    from antibody_manager.domain import selection_conflict
    primary = dict(id=1, kind='Primary', host='Rabbit')
    assert selection_conflict(dict(primary, id=2), [primary])
    assert not selection_conflict(dict(primary, id=2, host='Mouse'), [primary])
    secondary = dict(id=3, kind='Secondary', color='Cy5', excitation='649', emission='666')
    assert selection_conflict(dict(secondary, id=4, color='Dylight 650', excitation='650', emission='670'), [secondary])
    assert not selection_conflict(dict(secondary, id=4, color='488', excitation='495', emission='519'), [secondary])
    assert not selection_conflict(dict(secondary, id=4, host='Goat', color='488', excitation='495', emission='519'), [dict(secondary, host='Goat')])
    assert compatible(primary, dict(kind='Secondary', reactivity='All species'))


def test_secondary_reactivity_conflicts():
    from antibody_manager.domain import selection_conflict
    selected = dict(id=1, kind='Secondary', host='Goat', reactivity='Rabbit', color='Cy5')
    candidate = dict(id=2, kind='Secondary', host='Goat', color='488')
    for reactivity in ('Rabbit', ' rabbit ', 'Mouse, Rabbit', 'All species'):
        assert 'reactivity' in selection_conflict(dict(candidate, reactivity=reactivity), [selected])
    assert not selection_conflict(dict(candidate, reactivity='Mouse'), [selected])
    assert 'reactivity' in selection_conflict(dict(candidate, reactivity='Mouse'),
                                             [dict(selected, reactivity='All species')])


def test_tiff_review_update_and_field_recovery(tmp_path):
    store = Store(tmp_path / 'test.db')
    store.seed_demo()
    a = store.antibodies()[0]
    a['notes'] = 'Clone: C1\nIsotype: IgG\nExcitation: 649\nEmission: 666'
    store.save(a, a['id'])
    with store.connection() as db:
        for field in ('clone', 'isotype', 'excitation', 'emission'):
            db.execute(f'ALTER TABLE antibodies DROP COLUMN {field}')
    store = Store(store.path)
    restored = next(row for row in store.antibodies() if row['id'] == a['id'])
    assert restored['clone'] == 'C1'
    assert restored['excitation'] == '649'
    image = io.BytesIO()
    Image.new('I;16', (10, 10)).save(image, format='TIFF')
    store.add_review(a['id'], 'Laura', 'IF', 3, 'Initial', [('image.tiff', image.getvalue())])
    review = store.reviews(a['id'])[0]
    with pytest.raises(ValueError):
        store.update_review(review['id'], 'Other', 'IF', 5, 'Changed')
    store.update_review(review['id'], 'Laura', 'WB', 5, 'Updated')
    updated = store.reviews(a['id'])[0]
    assert updated['rating'] == 5 and updated['experiment'] == 'WB'
    assert updated['images'][0]['data'] == image.getvalue()
    with pytest.raises(ValueError):
        store.delete_review(review['id'], 'Other')
    assert len(store.reviews(a['id'])) == 1
    store.delete_review(review['id'], 'Laura')
    assert store.reviews(a['id']) == []
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 0
    assert next(row for row in store.antibodies() if row['id'] == a['id'])['rating'] is None
