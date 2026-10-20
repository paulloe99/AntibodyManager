from antibody_manager.config import database_urls
from antibody_manager.migrate_cloud import configured_url


def test_neon_env_priority_and_separate_migration_url(tmp_path, monkeypatch):
    for key in ('ANTIBODY_DATABASE_URL', 'DATABASE_URL', 'DATABASE_URL_UNPOOLED'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('DATABASE_URL=postgresql://older/db\n', encoding='utf-8')
    (tmp_path / '.env.local').write_text('DATABASE_URL=postgresql://pooled/db\nDATABASE_URL_UNPOOLED=postgresql://direct/db\n', encoding='utf-8')
    assert database_urls(tmp_path) == ('postgresql://pooled/db', 'postgresql://direct/db')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://runtime/db')
    assert database_urls(tmp_path) == ('postgresql://runtime/db', 'postgresql://runtime/db')
    monkeypatch.setenv('ANTIBODY_DATABASE_URL', 'postgresql://override/db')
    assert database_urls(tmp_path) == ('postgresql://override/db', 'postgresql://override/db')


def test_migration_uses_unpooled_neon_url(tmp_path, monkeypatch):
    monkeypatch.delenv('ANTIBODY_DATABASE_URL', raising=False)
    monkeypatch.setattr('antibody_manager.migrate_cloud.database_urls',
                        lambda: ('postgresql://pooled/db', 'postgresql://direct/db'))
    assert configured_url(tmp_path / 'missing.toml') == 'postgresql://direct/db'
