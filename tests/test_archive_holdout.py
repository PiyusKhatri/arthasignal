from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from src.archive import schema
from src.database import holdout_guard as hg

OLD = date(2020, 3, 1)
NEW = date(2025, 10, 1)

INSERTS = {
    "company_event_records": "INSERT INTO company_event_records (source, symbol, record_type, record_key, details, reference_date) "
                             "VALUES ('test', 'TST', 'agm', :k, '{}'::jsonb, :d)",
    "corporate_announcements": "INSERT INTO corporate_announcements (source, source_id, title, category, published_date) VALUES ('test', :k, 't', 'other', :d)",
    "archive_documents": "INSERT INTO archive_documents (source, announcement_source_id, url, sha256, bytes, path, published_date) "
                         "VALUES ('test', :k, :k, repeat('0', 64), 1, 'p', :d)",
    "news_articles": "INSERT INTO news_articles (source, source_id, url, title, published_at, published_precision) VALUES ('test', :k, :k, 't', :d, 'day')",
    "report_field_values": "INSERT INTO report_field_values (document_sha256, field, method, accepted, published_date) VALUES (repeat('0', 64), :k, 'test', true, :d)",
    "policy_events": "INSERT INTO policy_events (authority, event_type, measure, value_text, announced_date, source_url, source_title, evidence) "
                     "VALUES ('test', 'test', :k, 'v', :d, 'u', 't', 'e')",
    "sentiment_observations": "INSERT INTO sentiment_observations (series, period_end, value, unit, published_date, date_basis, source_url) "
                              "VALUES (:k, :d, 1, 'u', :d, 'test', 'u')",
}


@pytest.fixture(scope="module")
def engine():
    from src.database.connection import engine as app_engine

    schema.apply(app_engine)
    return app_engine


def test_every_archive_table_is_protected(engine):
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, p.qual FROM pg_class c "
            "JOIN pg_policies p ON p.tablename = c.relname AND p.policyname = 'holdout_guard' WHERE c.relname = ANY(:t)"),
            {"t": list(schema.DATE_COLUMNS)}).fetchall()
    found = {r[0]: r for r in rows}
    assert set(found) == set(schema.DATE_COLUMNS)
    for table, column in schema.DATE_COLUMNS.items():
        assert hg.PROTECTED_COLUMNS[table] == column
        _, enabled, forced, qual = found[table]
        assert enabled and forced
        assert column in qual and "2025-09-30" in qual


@pytest.mark.parametrize("table", sorted(INSERTS))
def test_holdout_rows_are_hidden_when_the_guard_is_on(engine, table):
    column = schema.DATE_COLUMNS[table]
    key_column = {"company_event_records": "record_key", "corporate_announcements": "source_id", "archive_documents": "url", "news_articles": "source_id",
                  "report_field_values": "field", "policy_events": "measure", "sentiment_observations": "series"}[table]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            for name, day in (("pit-test-old", OLD), ("pit-test-new", NEW)):
                connection.execute(text(INSERTS[table]), {"k": name, "d": day})
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE {key_column} LIKE 'pit-test-%'")).scalar_one() == 2
            connection.execute(text(f"SET LOCAL ROLE {hg.RESEARCH_ROLE}"))
            connection.execute(text(f"SET LOCAL {hg.GUC} = 'on'"))
            seen = connection.execute(text(f"SELECT {key_column}, {column} FROM {table} WHERE {key_column} LIKE 'pit-test-%'")).fetchall()
            assert [r[0] for r in seen] == ["pit-test-old"]
        finally:
            transaction.rollback()


def test_mentions_follow_their_article_date(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            ids = {}
            for name, day in (("pit-test-old", OLD), ("pit-test-new", NEW)):
                ids[name] = connection.execute(text(
                    "INSERT INTO news_articles (source, source_id, url, title, published_at, published_precision) "
                    "VALUES ('test', :k, :k, 't', :d, 'day') RETURNING id"), {"k": name, "d": day}).scalar_one()
                connection.execute(text("INSERT INTO news_symbol_mentions (article_id, symbol, method, published_at) VALUES (:a, 'TST', 'test', :d)"),
                                   {"a": ids[name], "d": day})
            connection.execute(text(f"SET LOCAL ROLE {hg.RESEARCH_ROLE}"))
            connection.execute(text(f"SET LOCAL {hg.GUC} = 'on'"))
            seen = connection.execute(text("SELECT article_id FROM news_symbol_mentions WHERE symbol = 'TST' AND method = 'test'")).fetchall()
            assert [r[0] for r in seen] == [ids["pit-test-old"]]
        finally:
            transaction.rollback()


def test_archive_tables_are_append_only(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(INSERTS["policy_events"]), {"k": "pit-test-old", "d": OLD})
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text("UPDATE policy_events SET value_text = 'x' WHERE measure = 'pit-test-old'"))
        finally:
            transaction.rollback()


def test_raw_store_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "RAW_ROOT", tmp_path)
    digest, path = schema.store_raw("t", b"abc", ".html")
    stamp = path.stat().st_mtime_ns
    again, same = schema.store_raw("t", b"abc", ".html")
    assert (again, same) == (digest, path) and same.stat().st_mtime_ns == stamp


def test_announcement_events_follow_their_announcement_date(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            ids = {}
            for name, day in (("pit-test-old", OLD), ("pit-test-new", NEW)):
                ids[name] = connection.execute(text(
                    "INSERT INTO corporate_announcements (source, source_id, title, category, published_date) VALUES ('test', :k, 't', 'other', :d) RETURNING id"),
                    {"k": name, "d": day}).scalar_one()
                connection.execute(text("INSERT INTO announcement_events (announcement_id, version, event_type, published_date) VALUES (:a, 'test', 'other', :d)"),
                                   {"a": ids[name], "d": day})
            connection.execute(text(f"SET LOCAL ROLE {hg.RESEARCH_ROLE}"))
            connection.execute(text(f"SET LOCAL {hg.GUC} = 'on'"))
            seen = connection.execute(text("SELECT announcement_id FROM announcement_events WHERE version = 'test'")).fetchall()
            assert [r[0] for r in seen] == [ids["pit-test-old"]]
        finally:
            transaction.rollback()


def test_dividend_proposal_view_applies_base_table_policies(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            options = connection.execute(text("SELECT reloptions FROM pg_class WHERE relname = 'dividend_proposals_pit'")).scalar_one()
            assert "security_invoker=true" in options
            for name, day in (("pit-test-old", OLD), ("pit-test-new", NEW)):
                connection.execute(text(
                    "INSERT INTO company_event_records (source, symbol, record_type, record_key, event_date, bookclose_date, cash_pct, details, reference_date) "
                    "VALUES ('test', 'TSTX', 'agm', :k, :d, :d, 5, '{}'::jsonb, :d)"), {"k": name, "d": day})
            connection.execute(text(f"SET LOCAL ROLE {hg.RESEARCH_ROLE}"))
            connection.execute(text(f"SET LOCAL {hg.GUC} = 'on'"))
            seen = connection.execute(text("SELECT knowledge_date FROM dividend_proposals_pit WHERE symbol = 'TSTX'")).fetchall()
            assert [r[0] for r in seen] == [OLD]
        finally:
            transaction.rollback()
