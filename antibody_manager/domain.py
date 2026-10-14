"""Pure planning logic. All volumes are in microlitres."""
import math


def tokens(value):
    return {part.strip().casefold() for part in value.split(',') if part.strip()}


def compatible(primary, secondary):
    return secondary['kind'] == 'Secondary' and primary['host'].strip().casefold() in tokens(secondary['reactivity'])


def recipe(samples, volume, dilution, excess=0):
    values = (samples, volume, dilution, excess)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('All quantities must be finite.')
    if samples < 1 or int(samples) != samples or volume <= 0 or dilution < 1 or excess < 0:
        raise ValueError('Use positive samples and volume, dilution >= 1, and non-negative excess.')
    total = samples * volume * (1 + excess / 100)
    stock = total / dilution
    return {'total_ul': total, 'stock_ul': stock, 'diluent_ul': total - stock}
