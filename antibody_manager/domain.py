"""Pure planning logic. All volumes are in microlitres."""
import math


def tokens(value):
    return {part.strip().casefold() for part in value.split(',') if part.strip()}


def compatible(primary, secondary):
    host = primary['host'].strip().casefold()
    return (host not in ('', 'unknown', 'unknown (not recorded)')
            and secondary['kind'] == 'Secondary' and (host in tokens(secondary['reactivity']) or 'all species' in tokens(secondary['reactivity'])))


def recipe(samples, volume, dilution, excess=0):
    values = (samples, volume, dilution, excess)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('All quantities must be finite.')
    if samples < 1 or int(samples) != samples or volume <= 0 or dilution < 1 or excess < 0:
        raise ValueError('Use positive samples and volume, dilution >= 1, and non-negative excess.')
    total = samples * volume * (1 + excess / 100)
    stock = total / dilution
    return {'total_ul': total, 'stock_ul': stock, 'diluent_ul': total - stock}


def wavelength(value):
    import re
    match = re.search(r'\d+(?:\.\d+)?', str(value))
    return float(match.group()) if match else None


def selection_conflict(candidate, selected, gap=30):
    for other in selected:
        if candidate['kind'] != other['kind']:
            continue
        if candidate['id'] == other['id']:
            continue
        if candidate['kind'] == 'Primary':
            if tokens(candidate['host']) & tokens(other['host']):
                return 'Primary antibodies must have distinct hosts.'
        else:
            reactivity = tokens(candidate.get('reactivity', ''))
            other_reactivity = tokens(other.get('reactivity', ''))
            if (reactivity & other_reactivity or
                    (reactivity and other_reactivity and 'all species' in reactivity | other_reactivity)):
                return 'Select only one secondary antibody per reactivity species.'
            if candidate['color'].strip() and candidate['color'].strip().casefold() == other['color'].strip().casefold():
                return 'Secondary antibodies must have distinct colors.'
            for field in ('excitation', 'emission'):
                a, b = wavelength(candidate.get(field, '')), wavelength(other.get(field, ''))
                if a is not None and b is not None and abs(a - b) < gap:
                    return f'Secondary {field} wavelengths must be at least {gap:g} nm apart.'
    return ''
