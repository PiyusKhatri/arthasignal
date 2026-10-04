from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.collectors import classify_spec, news, social, store
from src.collectors.mentions import build_book, language, normalize_name

BOOK = build_book([("NABIL", "Nabil Bank Limited"), ("API", "Api Power Company Ltd."), ("CZBIL", "Citizens Bank International Limited"),
                   ("NRN", "NRN Infrastructure and Development Limited"), ("SHINE", "Shine Resunga Development Bank Ltd.")])

SHARESANSAR_LIST = """
<div class="featured-news-list margin-bottom-15">
  <div class="col-md-10"><a href="https://www.sharesansar.com/newsdetail/nabil-q1-2026-10-04" title="Nabil Bank Posts Q1 Profit">
  <h4 class="featured-news-title">Nabil Bank Posts Q1 Profit</h4></a><p><span class="text-org">Sunday, October 4, 2026</span></p></div>
</div>"""
SHARESANSAR_DETAIL = """
<h5><i class="fa fa-calendar" aria-hidden="true"></i> Sun, Oct 4, 2026 10:14 AM</i> on <a href="#">Latest</a></h5>
<div id="newsdetail-content"><p>Nabil Bank Limited (NABIL) reported growth.</p></div>"""
MEROLAGANI_LIST = """
<div class="media-news media-news-md clearfix"><div class="media-body"><h4 class="media-title">
<a href="/NewsDetail.aspx?newsID=131487">दुई कम्पनीको शेयरमूल्य समायोजन</a></h4>
<span class="media-label"> Oct 04, 2026 08:50 AM </span></div></div>"""
MEROLAGANI_DETAIL = """
<span id="ctl00_ContentPlaceHolder1_newsDate" class="media-label">Oct 04, 2026 08:50 AM</span>
<span id="ctl00_ContentPlaceHolder1_newsSource">Merolagani</span>
<div id="ctl00_ContentPlaceHolder1_newsOverview"><p>सिटिजन्स बैंक ( CZBIL )</p></div>
<div id="ctl00_ContentPlaceHolder1_newsDetail"><p>मूल्य समायोजन</p><div class='news-inner-ads'>advert</div></div>"""
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>शेयर बजार</title><link>https://bizmandu.com/content/1.html</link>
<dc:creator><![CDATA[Reporter]]></dc:creator><pubDate>Sun, 04 Oct 2026 04:17:43 +0000</pubDate><category>शेयर</category>
<guid isPermaLink="false">https://bizmandu.com/?p=1</guid><content:encoded><![CDATA[<p>नबिल (NABIL) को नाफा</p>]]></content:encoded></item>
<item><title>Nepse slips</title><link>https://kathmandupost.com/money/2026/10/03/nepse-slips</link><description>Selling</description></item>
<item><title>Politics</title><link>https://kathmandupost.com/politics/2026/10/03/x</link><description>x</description></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def get(self, url: str):
        return SimpleNamespace(text=self.pages[url])


def test_symbol_finder_uses_tags_names_and_skips_ambiguous_words() -> None:
    assert BOOK.find("Nabil Bank Limited and ( CZBIL ) rose; API rose too") == ["CZBIL", "NABIL"]
    assert BOOK.find("NRN investors want to SHINE") == []
    assert BOOK.find("Buy (API) and $SHINE now") == ["API", "SHINE"]
    assert normalize_name("Api Power Company Ltd.") == "api power"
    assert language("नेपाली बजार") == "ne" and language("market") == "en"


def test_sharesansar_collector_reads_minute_timestamps_and_body() -> None:
    url = "https://www.sharesansar.com/newsdetail/nabil-q1-2026-10-04"
    http = FakeHttp({news.SHARESANSAR_LIST: SHARESANSAR_LIST, url: SHARESANSAR_DETAIL})
    items, detail = news.SharesansarCollector(http, BOOK, set()).collect()
    assert detail == {"listed": 1, "new": 1, "details_fetched": 1}
    item = items[0]
    assert item.published_at.isoformat() == "2026-10-04T10:14:00+05:45" and item.published_precision == "minute"
    assert item.symbols == ["NABIL"] and "reported growth" in item.body
    items, detail = news.SharesansarCollector(http, BOOK, {"nabil-q1-2026-10-04"}).collect()
    assert items == [] and detail["new"] == 0


def test_merolagani_collector_strips_ads_and_keeps_minute_time() -> None:
    http = FakeHttp({news.MEROLAGANI_LIST: MEROLAGANI_LIST, news.MEROLAGANI_DETAIL.format(id="131487"): MEROLAGANI_DETAIL})
    items, _ = news.MerolaganiCollector(http, BOOK, set()).collect()
    item = items[0]
    assert item.source_item_id == "131487" and item.symbols == ["CZBIL"]
    assert "advert" not in item.body and item.language == "ne"
    assert item.published_at.isoformat() == "2026-10-04T08:50:00+05:45"


def test_feed_collectors_parse_rss_and_filter_kathmandupost_sections() -> None:
    http = FakeHttp({news.BIZMANDU_FEED: RSS, news.KATHMANDUPOST_FEED: RSS})
    items, _ = news.BizmanduCollector(http, BOOK, set()).collect()
    assert items[0].published_precision == "second" and items[0].symbols == ["NABIL"]
    assert items[0].published_at == datetime(2026, 10, 4, 4, 17, 43, tzinfo=timezone.utc)
    kp, _ = news.KathmanduPostCollector(http, BOOK, set()).collect()
    assert [i.title for i in kp] == ["Nepse slips"]
    assert kp[0].published_precision == "day" and kp[0].published_at.date().isoformat() == "2026-10-03"


def test_social_items_hash_authors_and_mark_youtube_and_reddit_for_30_day_retention() -> None:
    message = SimpleNamespace(id=42, message="Buy $NABIL before circuit!", media=None, date=datetime(2026, 10, 4, tzinfo=timezone.utc),
                              post_author=None, sender_id=999, views=10, forwards=1, replies=None, edit_date=None, fwd_from=None)
    item = social.telegram_item("somechannel", 123, message, BOOK)
    assert item.source_item_id == "123:42" and item.symbols == ["NABIL"] and item.retention == store.PERMANENT
    thread = {"snippet": {"totalReplyCount": 2, "topLevelComment": {"id": "c1", "snippet": {
        "textOriginal": "NABIL target 1000", "publishedAt": "2026-10-04T05:00:00Z", "authorChannelId": {"value": "UCx"}, "likeCount": 3}}}}
    comment = social.youtube_comment_item("vid", thread, BOOK)
    assert comment.retention == store.THIRTY_DAYS and comment.symbols == ["NABIL"]
    post = social.reddit_item("post", {"name": "t3_a", "title": "NABIL?", "selftext": "", "created_utc": 1790000000, "subreddit": "x",
                                       "permalink": "/r/x/a", "author_fullname": "t2_u"}, BOOK)
    assert post.retention == store.THIRTY_DAYS and post.body is None
    assert store.author_hash("reddit_post", "t2_u") != "t2_u"


def test_social_collectors_refuse_without_keys_or_terms_acknowledgement(monkeypatch) -> None:
    for name in ("ARTHASIGNAL_TELEGRAM_TERMS_ACK", "TELEGRAM_API_ID", "YOUTUBE_API_KEY", "REDDIT_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(social.NotConfigured, match="TERMS_ACK"):
        social.collect_telegram(BOOK, {}, {"telegram_channels": ["x"]})
    with pytest.raises(social.NotConfigured, match="YOUTUBE_API_KEY"):
        social.collect_youtube(BOOK, {"youtube_channel_ids": ["x"]})
    with pytest.raises(social.NotConfigured, match="REDDIT_CLIENT_ID"):
        social.collect_reddit(BOOK, {"reddit_subreddits": ["x"]})


def test_classification_spec_validates_labels_and_blocks_telegram() -> None:
    good = {"market_relevant": True, "symbols": [{"symbol": "NABIL", "role": "subject", "confidence": 0.9}], "event_type": "earnings",
            "sentiment": {"direction": 1, "confidence": 0.7}, "promotion": {"is_promotion": False, "signals": [], "confidence": 0.9},
            "novelty": "new_information", "evidence": "profit rose", "language": "en"}
    assert classify_spec.validate(good, {"NABIL"}) == []
    bad = dict(good, symbols=[{"symbol": "FAKE", "role": "subject", "confidence": 0.9}], event_type="moon")
    assert set(classify_spec.validate(bad, {"NABIL"})) == {"unknown symbol FAKE", "bad event_type"}
    with pytest.raises(PermissionError):
        classify_spec.build_prompt({"source": "telegram", "body": "x"}, {"NABIL": "Nabil Bank"})
    prompt = classify_spec.build_prompt({"source": "bizmandu", "title": "t", "body": "b", "symbols": ["NABIL"]}, {"NABIL": "Nabil Bank"})
    assert "NABIL: Nabil Bank" in prompt["user"] and len(classify_spec.prompt_sha256()) == 64


@pytest.fixture
def temp_schema():
    from src.database.connection import engine

    name = f"collect_test_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {name}")
    except Exception:
        pytest.skip("database not reachable")
    store.apply_schema(engine, name)
    yield engine, name
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP SCHEMA {name} CASCADE")


def test_store_is_append_only_dedupes_and_purges_only_ephemeral_text(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    news_item = store.Item("bizmandu", "p1", "t", "b", now, "second", symbols=["NABIL"])
    yt = store.Item("youtube_comment", "c1", None, "NABIL target", now, "second", symbols=["NABIL"], retention=store.THIRTY_DAYS)
    assert store.insert_items(engine, [news_item, yt], "test", schema, now) == 2
    assert store.insert_items(engine, [news_item, yt], "test", schema, now) == 0
    edited = store.Item("bizmandu", "p1", "t", "b edited", now, "second")
    assert store.insert_items(engine, [edited], "test", schema, now) == 1
    with engine.connect() as connection:
        body = connection.execute(text(f"SELECT body FROM {schema}.text_items WHERE source = 'youtube_comment'")).scalar_one()
    assert body is None
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE {schema}.text_items SET body = 'x'"))
    assert store.purge_expired(engine, schema, now + timedelta(days=29)) == 0
    assert store.purge_expired(engine, schema, now + timedelta(days=31)) == 1
    with engine.connect() as connection:
        assert connection.execute(text(f"SELECT count(*) FROM {schema}.text_items")).scalar_one() == 3
