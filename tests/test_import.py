from antibody_manager.domain import compatible
from antibody_manager.import_histology import dilutions, import_records, species
from antibody_manager.store import Store


def test_conservative_dilutions():
    assert dilutions('WB 1:1000, IHC 1:200') == {'WB': 1000, 'IHC': 200}
    assert dilutions('1:200 IHC') == {'IHC': 200}
    assert dilutions('WB 1:5,000') == {'WB': 5000}
    for value in ['1:200', 'WB 1:100-1:500', 'IHC 0.5 µg/ml', '1:20 ?', '0.18055555555555558']:
        assert dilutions(value) == {}


def test_species_and_unknown_host():
    assert species('rabbit ') == 'Rabbit'
    assert species('H,M,R') == 'Human, Mouse, Rat'
    assert species('Mouse (published), Human') == 'Mouse (published), Human'
    assert not compatible({'host': 'Unknown (not recorded)'}, {'kind': 'Secondary', 'reactivity': 'Unknown (not recorded)'})


def test_import_is_additive_and_repeatable(tmp_path):
    store = Store(tmp_path / 'test.db')
    store.seed_demo()
    record = dict(name='Test antibody', kind='Primary', host='Rabbit', reactivity='Human',
                  location='Box 1', notes='Source row', dilutions={'IF': 200}, archived=0)
    records = [('Primary', 4, record), ('Primary', 5, dict(record, location='Box 2'))]
    assert import_records(store, records) == (2, 0)
    assert import_records(store, records) == (0, 2)
    assert len(store.antibodies()) == 6
