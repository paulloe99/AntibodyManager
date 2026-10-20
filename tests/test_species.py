import sqlite3

from antibody_manager.species import species
from antibody_manager.normalize_inventory import normalize_inventory
from antibody_manager.store import Store


def test_species_abbreviations_and_case():
    assert species('H M R Hm Mk C Dm B Dg Pg GP') == (
        'Human, Mouse, Rat, Hamster, Monkey, Chicken, D. melanogaster, Bovine, Dog, Pig, Guinea Pig')
    assert species('H M R Hm Mk Mi Dm Z B Dg Pg Sc') == (
        'Human, Mouse, Rat, Hamster, Monkey, Mink, D. melanogaster, Zebrafish, Bovine, Dog, Pig, S. cerevisiae')
    assert species('mouse, Mouse, H; human; mouse rat') == 'Mouse, Human, Rat'
    assert species('guinea pig, porcine, cow, Zebrafisch') == 'Guinea Pig, Pig, Bovine, Zebrafish'
    assert species('Mouse (published), Human') == 'Mouse (published), Human'
    assert species('human,mouse (does not work in mouse)') == 'Human, mouse (does not work in mouse)'
    assert species('MHV-A59, GFP') == 'MHV-A59, GFP'


def test_cleanup_backup_and_future_saves(tmp_path):
    path = tmp_path / 'inventory.db'
    store = Store(path)
    antibody_id = store.save(dict(name='Test', kind='Primary', host='Rabbit',
                                  reactivity='mouse, H', location='4\u00b0C'))
    assert store.antibodies()[0]['reactivity'] == 'Mouse, Human'
    with store.connection() as db:
        db.execute('UPDATE antibodies SET reactivity=? WHERE id=?', ('H M R', antibody_id))
    result = normalize_inventory(path)
    assert result['changed'] == 1
    assert store.antibodies()[0]['reactivity'] == 'Human, Mouse, Rat'
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT reactivity FROM antibodies').fetchone()[0] == 'H M R'
    assert normalize_inventory(path)['changed'] == 0
