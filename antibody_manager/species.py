"""Canonical species names for inventory entry and workbook imports."""
import re


ALIASES = {
    'Human': ('H', 'Hu', 'Humna'), 'Mouse': ('M',), 'Rat': ('R', 'Ratte'),
    'Hamster': ('Hm',), 'Monkey': ('Mk',), 'Chicken': ('C',),
    'D. melanogaster': ('Dm',), 'Bovine': ('B', 'Cow'), 'Dog': ('Dg',),
    'Pig': ('Pg', 'Porcine'), 'Guinea Pig': ('GP',), 'Mink': ('Mi',),
    'Zebrafish': ('Z', 'Zebrafisch'), 'S. cerevisiae': ('Sc', 'S.cerevisiae'),
    'Rabbit': ('Rb', 'Rabitt'), 'Goat': (), 'Donkey': (), 'Horse': (),
    'Cat': (), 'Sheep': (), 'Armenian hamster': (), 'Non-human primate': (),
    'Rhesus monkey': (), 'Mustelid': (), 'Bacteria': (), 'Insect': (),
    'Yeast': (), 'Mammals': (), 'All species': (),
}
SPECIES = {alias.casefold(): name for name, aliases in ALIASES.items() for alias in (name, *aliases)}
_NAMES = re.compile('|'.join(re.escape(name) for name in sorted(SPECIES, key=len, reverse=True)), re.I)


def species(value):
    """Expand known names/lists while preserving uncertain biological annotations."""
    result = []
    for part in re.split(r'[,;]+', value.strip()):
        part = ' '.join(part.split())
        if not part:
            continue
        if part.casefold() in SPECIES:
            normalized = [SPECIES[part.casefold()]]
        else:
            # Accept whitespace-separated lists only when every word is known.
            matches = list(_NAMES.finditer(part))
            remainder = _NAMES.sub('', part)
            contiguous = all(not part[a.end():b.start()].strip() and a.end() < b.start()
                             for a, b in zip(matches, matches[1:]))
            normalized = ([SPECIES[m.group().casefold()] for m in matches]
                          if matches and not remainder.strip() and contiguous else [part])
        for name in normalized:
            if name not in result:
                result.append(name)
    return ', '.join(result)
