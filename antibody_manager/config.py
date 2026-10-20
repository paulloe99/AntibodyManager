"""Read Neon environment files without exposing or mutating process credentials."""
import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def database_urls(root=None):
    root = Path(root) if root is not None else ROOT
    pooled = direct = override = None
    for values in (dotenv_values(root / '.env'), dotenv_values(root / '.env.local'), os.environ):
        override = values.get('ANTIBODY_DATABASE_URL') or override
        if values.get('DATABASE_URL'):
            new_pool = values['DATABASE_URL']
            # Never pair a newly supplied branch URL with another branch's direct URL.
            direct = values.get('DATABASE_URL_UNPOOLED') or (direct if new_pool == pooled else new_pool)
            pooled = new_pool
        elif values.get('DATABASE_URL_UNPOOLED'):
            direct = values['DATABASE_URL_UNPOOLED']
    return (override, override) if override else (pooled, direct or pooled)
