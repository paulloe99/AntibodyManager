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
