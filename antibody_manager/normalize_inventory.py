"""Back up and normalize existing reactivity values without changing source notes."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from antibody_manager.species import species


def normalize_inventory(path):
    path = Path(path)
    changes = []
    with sqlite3.connect(path) as db:
        for antibody_id, old in db.execute('SELECT id, reactivity FROM antibodies'):
            new = species(old)
            if new != old:
                changes.append({'id': antibody_id, 'before': old, 'after': new})
        if not changes:
            return {'changed': 0, 'backup': None}
        backup = path.with_name(f'{path.stem}.before-species-cleanup-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3')
        with sqlite3.connect(backup) as destination:
            db.backup(destination)
        with db:
            db.executemany('UPDATE antibodies SET reactivity=? WHERE id=?',
                           [(change['after'], change['id']) for change in changes])
    report = path.with_name('species_cleanup_report.json')
    report.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'changed': len(changes), 'backup': str(backup), 'report': str(report)}


if __name__ == '__main__':
    from antibody_manager.store import Store
    print(json.dumps(normalize_inventory(Store().path), indent=2))
