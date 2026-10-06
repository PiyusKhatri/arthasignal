from __future__ import annotations

import pytest

from src.archive import fundamentals_causes as fc
from src.archive import fundamentals_quality as fq
from src.archive import gpu_pilot
from src.archive import statement_text as st

NFRS = """Sample Insurance Limited
Unaudited Financial Results
Statement of Financial Position  Amount in NPR
Share Capital  1,000,000,000  1,000,000,000
Total Equity and Liabilities  9,000,000,000  8,500,000,000
Total Equity  2,500,000,000  2,400,000,000
Statement of Profit or Loss  NPR in '000
Particulars  This Quarter  Upto This Quarter  This Quarter  Upto This Quarter
Profit Before Tax  60,000  150,000  50,000  120,000
Net Profit/(Loss) for the period  45,000  112,500  37,500  90,000
a) Net profit has declined by NPR 12 MM compared to the same period
"""

GROUP = """Rs in '000
Particulars  Group  Group  Bank  Bank
This Quarter  Upto This Quarter  This Quarter  Upto This Quarter
Profit for the period  300  1,300  250  1,100
"""

NRB = """(Rupees in '000')
3 Profit and Loss Account  Upto This Quarter  Upto Previous Quarter  Corresponding Prev. Year
G. Net Profit/Loss (F-3.12-3.13  60,080  17,656  2,286
4.2 Non Performing Loan (NPL) to Total Loan  3.65%  5.81%  6.51%
"""


def test_normalise_reads_ocr_dot_groups_devanagari_digits_and_slash_decimals() -> None:
    assert st.normalise("Net Profit 311.862.091") == "Net Profit 311,862,091"
    assert st.normalise("12.278,157") == "12,278,157"
    assert st.normalise("१२,३४५") == "12,345"
    assert st.normalise("रु.197/50") == "रु.197.50"
    assert st.numbers("29th Poush 2081 85,350,643") == [(2081.0, "2081"), (85350643.0, "85,350,643")]


def test_units_come_from_the_line_above_the_figure_and_nepali_unit_words() -> None:
    assert st.line_unit("रकम रु. हजारमा") == (1e3, "thousands")
    assert st.line_unit("(अन्यथा उल्लेख गरेको वाहेक अंक रु. दश लाखमा)") == (1e6, "millions")
    assert st.line_unit("Amount in NPR") == (1.0, "rupees")
    rows = st.rows(NFRS)
    assert st.unit_above(rows, 3) == (1.0, "rupees")
    assert st.unit_above(rows, 9) == (1e3, "thousands")


def test_net_profit_is_year_to_date_and_scaled_by_its_own_section_unit() -> None:
    found = st.read_field(NFRS, "net_profit")
    assert found["value"] == 112_500_000.0
    assert "Profit Before Tax" not in found["row"]


def test_net_worth_skips_total_equity_and_liabilities_and_capital_uses_rupees() -> None:
    assert st.read_field(NFRS, "net_worth")["value"] == 2_500_000_000.0
    assert st.read_field(NFRS, "paid_up_capital")["value"] == 1_000_000_000.0


def test_group_and_bank_blocks_follow_the_requested_basis() -> None:
    assert st.read_field(GROUP, "net_profit", basis="standalone")["value"] == 1_100_000.0
    assert st.read_field(GROUP, "net_profit", basis="group")["value"] == 1_300_000.0


def test_formula_references_are_not_read_as_values_and_ratios_keep_percent() -> None:
    assert st.read_field(NRB, "net_profit")["value"] == 60_080_000.0
    assert st.read_field(NRB, "npl_ratio")["value"] == 3.65


def test_implausible_scale_falls_back_to_rupees() -> None:
    assert st.plausible_scale("net_profit", 311_862_091.0, 1e3) == 1.0
    assert st.plausible_scale("net_profit", 60_080.0, 1e3) == 1e3


def _case(**kw):
    base = {"field": "net_profit", "method": "headline", "texts": {}, "label": {"unit": "thousands", "language": "english"},
            "item": {"language": "english"}, "headline": None}
    return base | kw


def test_label_without_brackets_is_a_label_error_when_the_report_prints_a_loss() -> None:
    texts = {"paddleocr_mobile": "NPR in Thousands\nNet Profit  (200,749)  (118,941)  (207,193)  (74,155)"}
    cause, evidence = fc.classify(_case(got=-118_940_000.0, truth=118_941_000.0, printed=118_941.0, texts=texts))
    assert cause == "likely_label_error" and "(118,941)" in evidence


def test_headline_inside_its_printed_step_is_rounding() -> None:
    cause, _ = fc.classify(_case(got=1_050_000.0, truth=1_059_000.0, printed=1059.0))
    assert cause == "headline_rounding"


def test_implausible_label_unit_is_a_label_error_and_plausible_one_is_unit_scale() -> None:
    cause, _ = fc.classify(_case(method="paddleocr_mobile", got=311_862_091.0, truth=311_862_091_000.0, printed=311_862_091.0))
    assert cause == "likely_label_error"
    cause, _ = fc.classify(_case(method="paddleocr_mobile", got=60_080.0, truth=60_080_000.0, printed=60_080.0))
    assert cause == "unit_scale"


def test_other_column_of_the_same_row_is_period_and_a_near_number_is_a_misread() -> None:
    texts = {"paddleocr_mobile": NFRS}
    cause, evidence = fc.classify(_case(method="paddleocr_mobile", got=45_000_000.0, truth=112_500_000.0, printed=112_500.0, texts=texts))
    assert cause == "period"
    cause, _ = fc.classify(_case(method="tesseract", got=112_600_000.0, truth=112_500_000.0, printed=112_500.0,
                                 texts={"tesseract": NFRS.replace("112,500", "112,600")}))
    assert cause == "digit_misread"


def test_headline_counts_within_its_printed_precision_only_for_that_method() -> None:
    assert fq.correct(1_050_000.0, 1_059_000.0, "net_profit", "headline_printed_precision")
    assert not fq.correct(1_050_000.0, 1_059_000.0, "net_profit", "headline")


def test_a_field_needs_checks_and_at_most_one_error() -> None:
    label = {"net_profit": 100.0, "unit": "rupees"}
    rows = [{"label": label, "methods": {"consensus_v2_checked": {"net_profit": 100.0}, "consensus_v2": {"net_profit": 100.0}}} for _ in range(150)]
    rows.append({"label": label, "methods": {"consensus_v2_checked": {"net_profit": 5.0}, "consensus_v2": {"net_profit": 5.0}}})
    results = fq.measure(rows, ("consensus_v2_checked", "consensus_v2"))
    assert results["consensus_v2_checked"]["net_profit"]["usable"] and not results["consensus_v2"]["net_profit"]["usable"]
    rows.append(rows[-1])
    assert not fq.measure(rows, ("consensus_v2_checked",))["consensus_v2_checked"]["net_profit"]["usable"]
    few = fq.measure(rows[:fq.MIN_PRODUCED - 1], ("consensus_v2_checked",))["consensus_v2_checked"]["net_profit"]
    assert few["precision"] == 1.0 and not few["usable"]


def test_vlm_output_is_parsed_with_its_unit_and_brackets() -> None:
    values = fq.read_vlm('```json\n{"unit": "thousands", "net_profit_ytd": "(1,234)", "eps": "१२.५", "npl_ratio": "3.45%"}\n```')
    assert values["net_profit"] == -1_234_000.0 and values["eps"] == 12.5 and values["npl_ratio"] == 3.45
    assert fq.read_vlm("not json")["net_profit"] is None


def test_pilot_holds_every_labelled_report_and_no_holdout_report() -> None:
    labelled = [{"sha256": f"l{i:03d}", "path": f"/x/l{i}.jpg", "published_date": "2019-01-01", "label_id": f"L{i:03d}"} for i in range(120)]
    pool = [{"sha256": f"p{i:03d}", "path": f"/x/p{i}.jpg", "published_date": f"{2014 + i % 12}-05-01"} for i in range(300)]
    pool.append({"sha256": "late", "path": "/x/late.jpg", "published_date": "2025-10-02"})
    items = gpu_pilot.build(labelled, pool)
    assert len(items) == 200 and sum(i["labelled"] for i in items) == 120
    assert "late" not in {i["sha256"] for i in items}
    assert items == gpu_pilot.build(labelled, list(reversed(pool)))
    with pytest.raises(ValueError):
        gpu_pilot.build(labelled, pool, size=100)
