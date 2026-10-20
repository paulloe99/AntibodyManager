"""Conservative, repeatable import of the lab's Histology Antibodies workbook.

Reads XLSX XML without modifying the source. Preview is the default.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import posixpath
import re
import sqlite3
import zipfile
from xml.etree import ElementTree as ET

from antibody_manager.store import FIELDS, Store

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
SOURCE = '#1 Histology Antibodies.xlsx'
from antibody_manager.species import SPECIES, species


def dilutions(value):
    """Accept only complete, exact application + ratio expressions; never choose a range endpoint."""
    output = {}
    for segment in re.split(r';|,(?!\d)', value):
        segment = segment.strip()
        app = r'(WB|IHC-P|IHC|IF|ICC|FC|FACS|IP|ELISA)'
        prefix = re.fullmatch(app + r'\s*:?\s*\(?1:\s*([\d,]+)\)?', segment, re.I)
        suffix = re.fullmatch(r'1:\s*([\d,]+)\s*' + app, segment, re.I)
        if prefix or suffix:
            application, denominator = prefix.groups() if prefix else tuple(reversed(suffix.groups()))
            denominator = int(denominator.replace(',', ''))
            if denominator >= 1:
                key = application.upper()
                if key in output and output[key] != denominator:
                    return {}
                output[key] = denominator
    return output


def workbook_rows(path):
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//m:t', NS))
                       for si in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        relationships = {r.get('Id'): r.get('Target') for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).findall('m:sheets/m:sheet', NS):
            target = relationships[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            rows = []
            for row in ET.fromstring(archive.read(target)).findall('m:sheetData/m:row', NS):
                cells = {}
                for cell in row.findall('m:c', NS):
                    column = re.sub(r'\d', '', cell.get('r'))
                    v = cell.find('m:v', NS)
                    if cell.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//m:t', NS))
                    elif v is not None:
                        value = strings[int(v.text)] if cell.get('t') == 's' else v.text
                    else:
                        value = ''
                    if value:
                        cells[column] = value.strip()
                if cells:
                    rows.append((int(row.get('r')), cells))
            yield sheet.get('name'), rows


def prepare(path):
    records, issues = [], []
    counts = Counter()
    for sheet, rows in workbook_rows(path):
        if not rows:
            continue
        header = next(((number, cells) for number, cells in rows if 'Target' in cells.values()), None)
        if not header:
            raise ValueError(f'No Target header found in {sheet}.')
        header_number, headers = header
        secondary = sheet.startswith('Secondary')
        for number, cells in rows:
            if number <= header_number:
                continue
            source = {title: cells.get(column, '') for column, title in headers.items()}
            name = source.get('Target', '')
            if not name:
                continue  # Empty positions and conjugate legend are not antibody entries.
            if name == 'Target':
                continue
            if secondary and name.casefold() == 'cell mask':
                issues.append({'sheet': sheet, 'row': number, 'name': name, 'issue': 'Excluded: cell stain, not an antibody.'})
                continue
            if secondary and source.get('Reactivity') == '1mg/ml':
                issues.append({'sheet': sheet, 'row': number, 'name': name, 'issue': 'Excluded: shifted/malformed row; possible duplicate of rat HRP ab102182.'})
                continue
            kind = 'Secondary' if secondary else 'Primary'
            # The mixed sheets contain these explicitly labelled secondary reagents.
            if name in ('Mouse IgG (Biotin) AB', 'Mouse IgG AF488', 'HRP-Anti-Rabbit', 'IgG-HRP'):
                kind = 'Secondary'
            host = species(source.get('Host spezies', ''))
            reactivity = species(source.get('Reactivity', ''))
            location = source.get('Storage', '')
            if sheet.startswith('All Antibodies'):
                location = ' / '.join(x for x in ('-20°C', location, source.get('Position', '')) if x)
            elif secondary:
                location = source.get('Comment', '') or '4°C (sheet heading; exact position not recorded)'
            warnings = []
            for field, value in [('host', host), ('reactivity', reactivity), ('location', location)]:
                if not value:
                    warnings.append(f'{field} not recorded')
            suggestions = dilutions(source.get('Dilution', ''))
            if source.get('Dilution') and not suggestions:
                warnings.append('dilution requires manual application/value confirmation')
            color = source.get('Conjugate', '')
            if kind == 'Secondary' and not color:
                if 'AF488' in name:
                    color = 'AF488'
                elif 'Biotin' in name:
                    color = 'Biotin'
                elif 'HRP' in name:
                    color = 'HRP'
            notes = []
            notes += [f'{key}: {value}' for key, value in source.items() if value]
            if warnings:
                notes.append('Import checks: ' + '; '.join(warnings))
                issues.append({'sheet': sheet, 'row': number, 'name': name, 'issue': '; '.join(warnings)})
            record = dict(name=name, kind=kind, host=host or 'Unknown (not recorded)',
                          reactivity=reactivity or 'Unknown (not recorded)', company=source.get('Company', ''),
                          catalog=source.get('Catalogue number', source.get('Catalog#', '')), lot='',
                          location=location or 'Unknown (not recorded)', color=color, notes='\n'.join(notes),
                          clone=source.get('Clone', ''), isotype=source.get('Isotype', ''),
                          excitation=next((v for k, v in source.items() if 'excitation' in k.casefold()), ''),
                          emission=next((v for k, v in source.items() if 'emission' in k.casefold()), ''),
                          dilutions=suggestions, archived=int(source.get('Isotype', '').casefold() == 'finished'))
            records.append((sheet, number, record))
            counts[sheet] += 1
    return records, issues, counts


def import_records(store, records):
    """Atomic additive import; stable source row IDs make reruns safe."""
    inserted = skipped = 0
    with store.connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS imported_rows (
            source TEXT NOT NULL, sheet TEXT NOT NULL, row_number INTEGER NOT NULL,
            antibody_id INTEGER NOT NULL REFERENCES antibodies(id),
            PRIMARY KEY(source, sheet, row_number))''')
        for sheet, number, record in records:
            if db.execute('SELECT 1 FROM imported_rows WHERE source=? AND sheet=? AND row_number=?', (SOURCE, sheet, number)).fetchone():
                skipped += 1
                continue
            values = {key: record.get(key, '') for key in FIELDS}
            values.update(dilutions=json.dumps(record['dilutions']), archived=record['archived'])
            antibody_id = db.execute(f"INSERT INTO antibodies ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values())).lastrowid
            db.execute('INSERT INTO imported_rows VALUES (?,?,?,?)', (SOURCE, sheet, number, antibody_id))
            inserted += 1
    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    parser.add_argument('--apply', action='store_true', help='Back up and import; otherwise preview only.')
    args = parser.parse_args()
    records, issues, counts = prepare(args.workbook)
    report = {'source': str(args.workbook), 'by_sheet': dict(counts), 'prepared': len(records), 'issues': issues}
    if args.apply:
        store = Store()
        backup = store.path.with_name(f'{store.path.stem}.before-import-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3')
        with store.connection() as db, sqlite3.connect(backup) as destination:
            db.backup(destination)
        inserted, skipped = import_records(store, records)
        # Demo entries remain restorable but should not mix with real inventory.
        with store.connection() as db:
            demos = db.execute("UPDATE antibodies SET archived=1 WHERE name LIKE 'DEMO:%' AND company='Fictional demo supplier' AND archived=0").rowcount
        report.update(inserted=inserted, already_imported=skipped, backup=str(backup))
        report['demo_entries_archived'] = demos
        report_path = store.path.with_name('histology_import_report.json')
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        report['report'] = str(report_path)
    print(json.dumps({key: value for key, value in report.items() if key != 'issues'}, ensure_ascii=True, indent=2))
    print(f'{len(issues)} rows have import checks or exclusions; original details are preserved in notes.')


if __name__ == '__main__':
    main()
