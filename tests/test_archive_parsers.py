from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from src.archive import news_mentions as nm
from src.archive import ocr_eval
from src.archive import report_documents as rd
from src.archive import sharesansar_company as sc
from src.archive import sharesansar_news as sn
from src.archive.sentiment_series import OVERSUBSCRIBED

LIST_HTML = """
<div class="featured-news-list margin-bottom-15">
    <div class="col-md-10 col-sm-10 col-xs-12">
        <a href="https://www.sharesansar.com/newsdetail/abc-2016-05-31" title="Khanikhola &amp; Co IPO">
            <h4 class="featured-news-title">Khanikhola IPO</h4>
        </a>
        <p><span class="text-org">Tuesday, May 31, 2016</span>
        </p>
<a class="page-link" href="?cursor=eyJwdWJsaXNoZWRfZGF0ZSI6IjIwMTYtMDUtMzAgMTA6MDA6MDAiLCJfcG9pbnRzVG9OZXh0SXRlbXMiOnRydWV9" rel="next">Next</a>
"""


def test_news_list_and_cursor():
    items, nxt = sn.parse_list(LIST_HTML)
    assert items == [{"url": "https://www.sharesansar.com/newsdetail/abc-2016-05-31", "title": "Khanikhola & Co IPO", "day": date(2016, 5, 31)}]
    assert sn.cursor_time(nxt) == datetime(2016, 5, 30, 10, 0, 0)
    assert sn.cursor_time(sn.cursor_for(datetime(2014, 7, 1, 9, 5, 7))) == datetime(2014, 7, 1, 9, 5, 7)


def test_news_detail_minute_timestamp_and_body():
    html = '<i class="fa fa-clock"> Sun, Oct 4, 2026 2:08 PM</i><div id="newsdetail-content"><p>Line one.</p><p>Line two.</p></div>'
    out = sn.parse_detail(html)
    assert out["published"].isoformat() == "2026-10-04T14:08:00+05:45"
    assert out["body"] == "Line one.\nLine two."


def test_announcement_classification_and_numbers():
    assert sc.classify("Nabil Bank Limited has posted a net profit of Rs 1.12 billion and published its 1st quarter company analysis of the fiscal year 2076/77") == "quarterly_report"
    assert sc.classify("Nabil Bank Limited has proposed 10.80% cash dividend and 5% bonus shares") == "dividend_bonus"
    assert sc.classify("XYZ Limited is issuing 1:1 right shares from Magh 5") == "right_share"
    assert sc.classify("Book closure for 42nd AGM") == "agm"
    assert sc.classify("urges shareholders to collect uncollected due dividend") == "other"
    assert sc.parse_title_numbers("proposed 10.80% cash dividend and 5% bonus shares; right 1:0.5") == {"cash_pct": 10.8, "bonus_pct": 5.0, "ratio": "1:0.5"}


def test_report_attachments_and_url_repair():
    html = ('<img src="https://content.sharesansar.com/photos/shares/uploads/sharesansar_footer.png">'
            '<img src="https://content.sharesansar.com/photos/shares/announcement/1573876675-NABIL.jpg">'
            '<a href="https://content.sharesansar.com/files/report.pdf">pdf</a>')
    assert rd.attachments(html) == ["https://content.sharesansar.com/photos/shares/announcement/1573876675-NABIL.jpg",
                                    "https://content.sharesansar.com/files/report.pdf"]
    assert rd.upload_time("https://content.sharesansar.com/photos/shares/announcement/1573876675-NABIL.jpg").date() == date(2019, 11, 16)
    assert rd.repaired("https://content.sharesansar.com/photos/shares/announcement//photos/wp-content/uploads/2016/04/x.jpg") == \
        "https://content.sharesansar.com/photos/wp-content/uploads/2016/04/x.jpg"


def test_mention_aliases_skip_generic_names_and_stop_symbols():
    companies = pd.DataFrame({"symbol": ["PDB", "NABIL", "API"], "company_name": ["Development Bank Limited", "Nabil Bank Limited", "Api Power Company Ltd"]})
    names = nm.aliases(companies)
    assert "development bank" not in names and names["nabil bank"] == "NABIL"
    pattern = re.compile(r" (" + "|".join(re.escape(a) for a in names) + r") ")
    found = nm.mentions("Nabil Bank Proposes Dividend; API rises", "NABIL shares", names, {"NABIL", "API"}, pattern)
    assert found == {("NABIL", "company_name"), ("NABIL", "ticker")}


def test_oversubscription_patterns():
    assert OVERSUBSCRIBED.search("IPO Issue of Jhapa Energy Limited Closing Today; Oversubscribed 39.38 Times So Far").group(1) == "39.38"
    assert OVERSUBSCRIBED.search("Khanikhola Hydropower IPO oversubscribed by 18.12 times till second day").group(1) == "18.12"


def test_ocr_parser_handles_nepali_digits_slash_decimals_and_ytd():
    nepali = "प्रति शेयर आम्दानी रु.१०१/५० (वार्षिक)\nप्रति शेयर नेटवर्थ रु.२९४/३७"
    out = ocr_eval.extract(nepali)
    assert (out["eps"], out["book_value"]) == (101.5, 294.37)
    english = "Rs in '000\nParticulars This Quarter Up to This Quarter (YTD)\nProfit/(Loss) for the Period 155,172 436,007 114,936 285,384"
    out = ocr_eval.extract(english)
    assert out["unit"] == 1000.0 and out["net_profit"] == 436007


def test_nrb_macro_extraction_and_period():
    from src.archive import nrb_macro

    raw = ("The weighted average 91 -day Treasury bills rate increased to 5.50 percent in the eleventh month. "
           "Weighted average deposit rate and len ding rate of commercial banks stood at 6.64 percent and 12.20 percent respectively. "
           "The average base rate of commercial banks decreased to 9.48 percent. margin nature loan decreased 2.5 percent and hire")
    got = {k: v[0] for k, v in nrb_macro.extract(raw).items()}
    assert got == {"nrb_tbill_91d_rate": 5.5, "nrb_wavg_deposit_rate": 6.64, "nrb_wavg_lending_rate": 12.2,
                   "nrb_base_rate_commercial": 9.48, "nrb_margin_loan_growth": -2.5}
    assert nrb_macro.period_end("Situation - English (Based on Eleven Months Data of 2018/19)") == date(2019, 6, 15)
    assert nrb_macro.period_end("Situation (Based on Five Months Data of 2020/21)") == date(2020, 12, 15)


def test_event_classifier_v2():
    from src.archive import corporate_events as ce

    assert ce.classify("Bishal Bazar Company Limited has posted a net profit of Rs 56.24 million and published its final quarter company analysis") == "quarterly_report"
    assert ce.classify("ICFC Finance Limited has deposited 17% Bonus Share for the fiscal year 2072/73 in respective demat account.") == "dividend_distribution"
    assert ce.classify("XYZ Bank has proposed 10% bonus shares and 0.53% cash dividend") == "dividend_proposal"
    assert ce.classify("Nabil Bank Limited has published a notice regarding new interest rate") == "interest_rate_notice"
    assert ce.numbers("proposed 10% bonus shares and 0.53% cash dividend for FY 2081/82") == {"cash_pct": 0.53, "bonus_pct": 10.0, "right_ratio": None, "fiscal_year": "2081/82"}
