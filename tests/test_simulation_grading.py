from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from src.simulation import costs
from src.simulation.grading import (
    BLOCKED,
    BUY,
    CLOSES_BUY,
    FLOORSHEET_DERIVED,
    GRADED,
    HOLD,
    LOCKED_UPPER,
    NO_BUY,
    NO_TRADE,
    NOT_OPEN,
    OPENED,
    PENDING,
    REACHES_HOLDOUT,
    SELL,
    SELL_CALL,
    UNFILLED,
    UNGRADED,
    WAIT,
    Bar,
    Call,
    OpenCalls,
    accuracy,
    effective_bar,
    grade,
    hidden,
    max_drawdown,
    position_call,
    risk_control,
    sell_threshold,
)
from src.simulation.protocol import Protocol, load, round_score

P = load()
CALL_DAY = date(2024, 6, 2)


def path(prices, start=CALL_DAY, prev=100.0, no_trade=(), gap_days=1):
    bars = []
    day = start
    for index, price in enumerate(prices):
        day = day + timedelta(days=gap_days)
        if index in no_trade:
            bars.append(Bar(day, 0.0, 0.0, 0.0, 0.0, 0.0, prev))
            continue
        o, h, l, c = price if isinstance(price, tuple) else (price, price, price, price)
        bars.append(Bar(day, o, h, l, c, 1000.0, prev))
        prev = c
    return bars


def flat(n, value=100.0):
    return [(value, value + 0.5, value - 0.5, value)] * n


def call(kind, holding=20, target=None, stop=None, reference=100.0, horizon="short", day=CALL_DAY):
    return Call("TEST", kind, day, reference, horizon, holding, target, stop)


def test_protocol_identity_and_horizons():
    assert P.version == "sim-protocol-v1.1"
    assert len(P.sha256) == 64
    assert P.holding_range("short") == (19, 57)
    assert P.holding_range("mid") == (57, 133)
    assert P.holding_range("long") == (133, 285)
    assert P.raw["periods"]["learning"]["end"] == "2019-12-31"
    assert P.raw["protocol"]["holdout_start"] == "2025-09-30"


@pytest.mark.parametrize(
    "score,expected",
    [(100, BUY), (80, BUY), (79.5, BUY), (79.4, HOLD), (50, HOLD), (49, WAIT), (0, WAIT), (-49, WAIT),
     (-49.5, NO_BUY), (-79, NO_BUY), (-80, SELL), (-100, SELL)],
)
def test_score_bands(score, expected):
    assert P.call_for_score(score) == expected


def test_score_out_of_range():
    with pytest.raises(ValueError):
        P.call_for_score(100.5)


@pytest.mark.parametrize(
    "amount,day,rate",
    [
        (100000, date(2015, 3, 1), 0.0100),
        (100000, date(2016, 7, 31), 0.0100),
        (100000, date(2016, 8, 1), 0.0055),
        (40000, date(2019, 1, 1), 0.0060),
        (100000, date(2020, 12, 27), 0.0037),
        (100000, date(2024, 5, 13), 0.0037),
        (100000, date(2024, 5, 14), 0.0033),
        (50000, date(2024, 6, 1), 0.0036),
        (50001, date(2024, 6, 1), 0.0033),
        (3000000, date(2024, 6, 1), 0.0027),
        (20000000, date(2024, 6, 1), 0.00243),
    ],
)
def test_commission_tiers(amount, day, rate):
    assert costs.commission_rate(amount, day, P) == rate


def test_side_cost_and_minimum_commission():
    side = costs.side_cost(100000, date(2024, 6, 1), P)
    assert side.commission == pytest.approx(330.0)
    assert side.sebon_fee == pytest.approx(15.0)
    assert side.dp_charge == 25.0
    assert side.total == pytest.approx(370.0)
    assert costs.side_cost(1000, date(2024, 6, 1), P).commission == 10.0


def test_capital_gains_tax_schedule():
    assert costs.cgt_rate(date(2020, 1, 1), date(2020, 6, 1), P) == 0.05
    assert costs.cgt_rate(date(2022, 1, 1), date(2022, 6, 1), P) == 0.075
    assert costs.cgt_rate(date(2022, 1, 1), date(2023, 1, 1), P) == 0.075
    assert costs.cgt_rate(date(2022, 1, 1), date(2023, 1, 2), P) == 0.05


def test_round_trip_flat_price_loses_costs():
    trip = costs.round_trip(100.0, 100.0, date(2024, 6, 2), date(2024, 7, 1), P)
    assert trip.shares == 1000
    assert trip.net_pnl == pytest.approx(-740.0)
    assert trip.cgt == 0.0
    assert trip.net_return < 0


def test_round_trip_profit_pays_tax():
    trip = costs.round_trip(100.0, 110.0, date(2024, 6, 2), date(2024, 7, 1), P)
    gain = 110000 - (363 + 16.5 + 25) - (100000 + 370)
    assert trip.gain_before_tax == pytest.approx(gain)
    assert trip.cgt == pytest.approx(0.075 * gain)
    assert trip.net_pnl == pytest.approx(gain * 0.925)


def test_shares_whole_and_at_least_one():
    assert costs.shares_for(333.0, P) == 300
    assert costs.shares_for(250000.0, P) == 1


def test_buy_target_hit_is_right():
    bars = path(flat(5) + [(101, 112, 100, 111)] + flat(30, 111))
    out = grade(call(BUY, target=110, stop=90), bars, P)
    assert out.status == GRADED and out.correct is True
    assert out.reason == "target" and out.exit_price == 110
    assert out.net_pnl > 0


def test_buy_partial_target_then_horizon_above_entry_is_right():
    prices = flat(5) + [(103, 106, 102, 105)] + flat(14, 104)
    out = grade(call(BUY, holding=20, target=115, stop=90), path(prices + flat(10, 104)), P)
    assert out.reason == "horizon"
    assert out.exit_price == 104
    assert out.correct is True


def test_buy_small_gain_below_costs_is_wrong():
    prices = flat(19) + [(100.3, 100.3, 100.3, 100.3)]
    out = grade(call(BUY, holding=20, stop=90), path(prices), P)
    assert out.reason == "horizon"
    assert out.net_pnl < 0
    assert out.correct is False


def test_buy_stop_hit_is_wrong():
    prices = flat(6) + [(99, 99.5, 89, 92)] + flat(20, 120)
    out = grade(call(BUY, target=130, stop=90), path(prices), P)
    assert out.reason == "stop" and out.exit_price == 90
    assert out.correct is False
    assert out.hold_net_pnl > 0


def test_buy_gap_through_stop_fills_at_open():
    prices = flat(6) + [(85, 86, 80, 84)] + flat(20, 84)
    out = grade(call(BUY, target=130, stop=90), path(prices), P)
    assert out.exit_price == 85


def test_buy_stop_and_target_same_session_takes_stop():
    prices = flat(6) + [(100, 115, 85, 100)] + flat(20)
    out = grade(call(BUY, target=110, stop=90), path(prices), P)
    assert out.reason == "stop"


def test_buy_stop_above_entry_is_still_wrong():
    prices = [(90, 91, 89, 90), (92, 93, 91, 92), (96, 97, 95, 96), (97, 98, 96, 97), (97, 97, 94, 95)] + flat(20, 120)
    out = grade(call(BUY, target=130, stop=95), path(prices), P)
    assert out.entry_price == 90
    assert out.reason == "stop" and out.exit_price == 95
    assert out.net_pnl > 0
    assert out.correct is False


def test_no_sell_before_settlement():
    prices = [(100, 100, 100, 100), (100, 100, 85, 95), (95, 96, 94, 95), (96, 97, 95, 96)] + flat(20, 96)
    out = grade(call(BUY, target=130, stop=90), path(prices), P)
    assert out.reason == "horizon"
    assert out.exit_date == path(prices)[19].day


def test_entry_locked_upper_circuit_is_unfilled_and_not_backfilled():
    bars = path([(110, 110, 110, 110)] + flat(30, 112))
    out = grade(call(BUY, target=130, stop=90), bars, P)
    assert out.status == UNFILLED and out.reason == LOCKED_UPPER
    assert out.correct is False
    assert out.entry_price is None


def test_entry_no_trade_is_unfilled():
    bars = path(flat(30), no_trade={0})
    out = grade(call(BUY, target=130, stop=90), bars, P)
    assert out.status == UNFILLED and out.reason == NO_TRADE


def test_entry_upper_move_with_range_fills():
    bars = path([(105, 110, 104, 110)] + flat(30, 110))
    out = grade(call(BUY, target=130, stop=90), bars, P)
    assert out.status == GRADED and out.entry_price == 105


def test_locked_lower_circuit_delays_stop_exit():
    prices = flat(5) + [(90, 90, 90, 90), (81, 81, 81, 81), (80, 82, 78, 79)] + flat(20, 79)
    bars = path(prices)
    out = grade(call(BUY, target=130, stop=95), bars, P)
    assert out.reason == "stop"
    assert out.exit_price == 80
    assert out.exit_delay == 2
    assert out.exit_date == bars[7].day


def test_no_trade_on_horizon_day_exits_next_open():
    bars = path(flat(19) + [(101, 101, 101, 101), (102, 103, 101, 102)], no_trade={19})
    out = grade(call(BUY, holding=20, stop=80), bars, P)
    assert out.reason == "horizon"
    assert out.exit_delay == 1
    assert out.exit_price == 102


def test_short_path_is_pending():
    out = grade(call(BUY, holding=20, target=130, stop=80), path(flat(10)), P)
    assert out.status == PENDING and out.correct is None


def test_pre_2018_entry_at_close_and_close_only_checks():
    day = date(2017, 3, 1)
    prices = [(95, 104, 94, 100)] + [(100, 125, 99, 101)] * 25
    out = grade(call(BUY, target=120, stop=90, day=day), path(prices, start=day), P)
    assert out.entry_price == 100
    assert out.reason == "horizon"
    assert out.exit_price == 101


def test_path_crossing_real_open_start_uses_real_prices_after():
    day = date(2018, 2, 10)
    prices = [(95, 96, 94, 100)] + [(100, 125, 99, 101)] * 25
    bars = path(prices, start=day)
    out = grade(call(BUY, target=120, stop=90, day=day), bars, P)
    assert out.entry_price == 100
    assert out.reason == "target" and out.exit_price == 120
    assert out.exit_date >= date(2018, 2, 18)


def test_pre_2018_locked_entry_still_unfilled():
    day = date(2016, 5, 1)
    out = grade(call(BUY, stop=90, day=day), path([(110, 110, 110, 110)] + flat(30, 110), start=day), P)
    assert out.reason == LOCKED_UPPER


def test_no_buy_right_when_buy_would_lose():
    out = grade(call(NO_BUY), path(flat(19) + [(95, 96, 94, 95)] + flat(5, 95)), P)
    assert out.status == GRADED and out.correct is True
    assert out.net_pnl < 0


def test_no_buy_wrong_when_buy_would_profit():
    out = grade(call(NO_BUY), path(flat(19) + [(110, 111, 109, 110)] + flat(5, 110)), P)
    assert out.correct is False


def test_no_buy_unfilled_is_not_graded():
    out = grade(call(NO_BUY), path([(110, 110, 110, 110)] + flat(30, 110)), P)
    assert out.status == UNGRADED and out.correct is None and out.reason == LOCKED_UPPER


def test_sell_right_when_price_falls():
    out = grade(call(SELL), path(flat(19, 100) + [(96, 96, 95, 95)]), P)
    assert out.correct is True and out.exit_price == 95


def test_sell_wrong_when_price_rises():
    out = grade(call(SELL), path(flat(20, 101)), P)
    assert out.correct is False


def test_sell_stop_hit_is_wrong_and_target_is_right():
    up = grade(call(SELL, target=85, stop=108), path(flat(3) + [(104, 109, 103, 105)] + flat(20, 90)), P)
    assert up.reason == "stop" and up.correct is False
    down = grade(call(SELL, target=85, stop=108), path(flat(3) + [(95, 96, 84, 86)] + flat(20, 120)), P)
    assert down.reason == "target" and down.correct is True


def test_hold_recovers_is_right():
    prices = [(95, 96, 93, 94), (94, 97, 93, 96), (96, 101, 95, 100.5)] + flat(20, 100)
    out = grade(call(HOLD, stop=88, reference=100), path(prices), P)
    assert out.correct is True and out.reason == "recovered"


def test_hold_not_recovered_is_wrong():
    out = grade(call(HOLD, stop=80, reference=100), path(flat(25, 97)), P)
    assert out.correct is False and out.reason == "not_recovered"


def test_hold_stop_hit_is_wrong_even_if_it_recovers_later():
    prices = [(95, 96, 87, 90)] + flat(20, 105)
    out = grade(call(HOLD, stop=88, reference=100), path(prices), P)
    assert out.correct is False and out.reason == "stop" and out.exit_price == 88


def test_hold_stop_and_recovery_same_session_takes_stop():
    out = grade(call(HOLD, stop=88, reference=100), path([(95, 101, 87, 100)] + flat(20)), P)
    assert out.reason == "stop"


def test_wait_is_ungraded_and_tracks_missed_moves():
    up = grade(call(WAIT), path(flat(19) + [(110, 111, 109, 110)] + flat(5, 110)), P)
    assert up.status == UNGRADED and up.correct is None
    assert up.missed_buy is True and up.missed_sell is False
    down = grade(call(WAIT), path(flat(19) + [(90, 91, 89, 90)] + flat(5, 90)), P)
    assert down.missed_buy is False and down.missed_sell is True


def test_validation():
    with pytest.raises(ValueError):
        grade(call(BUY, holding=10), path(flat(30)), P)
    with pytest.raises(ValueError):
        grade(call(BUY), path(flat(30), start=CALL_DAY - timedelta(days=1)), P)
    with pytest.raises(ValueError):
        grade(call("SHORT"), path(flat(30)), P)


def test_long_horizon_pays_long_term_tax():
    day = date(2022, 1, 2)
    bars = path(flat(250) + [(130, 131, 129, 130)] + flat(10, 130), start=day, gap_days=2)
    out = grade(call(BUY, holding=251, horizon="long", stop=60, day=day), bars, P)
    trip = costs.round_trip(100.0, 130.0, bars[0].day, bars[250].day, P)
    assert (bars[250].day - bars[0].day).days > 365
    assert trip.cgt_rate == 0.05
    assert out.net_pnl == pytest.approx(trip.net_pnl)


def test_accuracy_counts_unfilled_buy_as_wrong():
    outcomes = [
        grade(call(BUY, target=110, stop=90), path(flat(5) + [(101, 112, 100, 111)] + flat(30, 111)), P),
        grade(call(BUY, target=110, stop=90), path([(110, 110, 110, 110)] + flat(30, 111)), P),
        grade(call(WAIT), path(flat(30)), P),
    ]
    assert accuracy(outcomes) == {"graded": 2, "right": 1, "accuracy": 0.5}


def test_max_drawdown():
    assert max_drawdown([0.1, -0.05, -0.1, 0.2, -0.3]) == pytest.approx(0.3)
    assert max_drawdown([0.1, 0.2]) == 0.0


def test_risk_control_score():
    stopped = grade(call(BUY, target=130, stop=90), path(flat(6) + [(99, 99, 89, 89)] + flat(20, 75)), P)
    target = grade(call(BUY, target=110, stop=90), path(flat(6) + [(101, 111, 100, 110)] + flat(20, 110)), P)
    regret = grade(call(BUY, target=130, stop=90), path(flat(6) + [(99, 99, 89, 89)] + flat(20, 105)), P)
    report = risk_control([stopped, target, regret], P)
    assert report["calls"] == 3
    assert report["stop_exits"] == 2
    assert report["stop_regret_rate"] == 0.5
    assert report["loss_avoided_npr"] == pytest.approx(
        (stopped.net_pnl - stopped.hold_net_pnl) + (regret.net_pnl - regret.hold_net_pnl)
    )
    assert report["tail_mean_hold"] == pytest.approx(stopped.hold_net_return)
    expected = 100 * (report["tail_mean_actual"] - report["tail_mean_hold"]) / abs(report["tail_mean_hold"])
    assert report["score"] == pytest.approx(expected)
    assert 0 < report["score"] <= 100
    assert report["mae_p95"] > 0


def test_risk_control_empty_and_no_tail_loss():
    assert risk_control([], P) == {"calls": 0, "score": None}
    win = grade(call(BUY, target=110, stop=90), path(flat(6) + [(101, 111, 100, 110)] + flat(20, 110)), P)
    assert risk_control([win], P)["score"] is None


def test_registration_parameters_carry_config_hash():
    from src.simulation.register import protocol_parameters

    assert protocol_parameters() == {"protocol": "sim-protocol-v1.1", "config_sha256": P.sha256}


def free_protocol():
    raw = copy.deepcopy(dict(P.raw))
    costs_raw = raw["costs"]
    costs_raw["minimum_commission_npr"] = 0
    for schedule in costs_raw["commission_schedules"]:
        for tier in schedule["tiers"]:
            tier["rate"] = 0.0
    costs_raw["sebon_fee"]["rate"] = 0.0
    costs_raw["dp_charge"]["npr_per_scrip_per_side"] = 0
    return Protocol(raw, "free")


def test_v11_decisions_in_config():
    assert P.raw["protocol"]["previous"]["version"] == "sim-protocol-v1"
    assert P.raw["costs"]["notional_npr"] == 100000
    assert P.settlement_sessions(date(2016, 1, 1)) == 3
    assert P.settlement_sessions(date(2024, 6, 1)) == 3
    assert [name for name, _, _ in P.periods()][-1] == "exam_2025"
    assert P.period_of(date(2025, 9, 29)) == "exam_2025"
    assert P.period_of(date(2025, 9, 30)) is None
    assert P.raw["multiple_testing"]["check_and_exam"] == "romano_wolf_stepdown"
    assert P.raw["multiple_testing"]["legacy_counter_decides_pass"] is False
    assert P.raw["reporting"]["pooled_only_classes"] == ["long"]
    assert P.raw["grading"]["SELL"]["costs"] == "holder's sell-side costs"


def test_long_class_covers_seven_to_fifteen_months_without_gap():
    classes = P.raw["horizons"]["classes"]
    assert (classes["long"]["min_months"], classes["long"]["max_months"]) == (7, 15)
    assert P.holding_range("mid")[1] == P.holding_range("long")[0] == 7 * 19
    assert P.holding_range("long")[1] == 15 * 19
    day = date(2021, 1, 3)
    out = grade(call(BUY, holding=133, horizon="long", stop=80, day=day), path(flat(140), start=day), P)
    assert out.status == GRADED and out.call.horizon_class == "long"
    with pytest.raises(ValueError):
        grade(call(BUY, holding=132, horizon="long", stop=80, day=day), path(flat(140), start=day), P)
    with pytest.raises(ValueError):
        grade(call(BUY, holding=286, horizon="long", stop=80, day=day), path(flat(300), start=day), P)


@pytest.mark.parametrize(
    "score,expected",
    [(79.5, 80), (-79.5, -80), (0.5, 1), (-0.5, -1), (49.4, 49), (-49.5, -50), (49.5, 50), (79.49, 79), (2.5, 3)],
)
def test_score_rounding_halves_away_from_zero(score, expected):
    assert round_score(score) == expected


def test_rounding_decides_band_edges():
    assert P.call_for_score(49.5) == HOLD
    assert P.call_for_score(-79.5) == SELL
    assert P.call_for_score(-0.5) == WAIT


def test_buy_and_hold_need_a_stop():
    with pytest.raises(ValueError, match="stop"):
        grade(call(BUY, target=110), path(flat(30)), P)
    with pytest.raises(ValueError, match="stop"):
        grade(call(HOLD), path(flat(30)), P)
    assert grade(call(NO_BUY), path(flat(30)), P).status == GRADED
    assert grade(call(SELL), path(flat(30)), P).status == GRADED


@pytest.mark.parametrize("bars", [path([(110, 110, 110, 110)] + flat(30, 110)), path(flat(30), no_trade={0})])
def test_unfilled_buy_is_wrong_and_counted(bars):
    out = grade(call(BUY, target=130, stop=90), bars, P)
    assert out.status == UNFILLED and out.correct is False
    assert accuracy([out]) == {"graded": 1, "right": 0, "accuracy": 0.0}


def test_net_zero_makes_buy_and_no_buy_wrong():
    free = free_protocol()
    bars = path(flat(30))
    buy = grade(call(BUY, stop=80), bars, free)
    no_buy = grade(call(NO_BUY), bars, free)
    assert buy.net_pnl == 0.0 and buy.correct is False
    assert no_buy.net_pnl == 0.0 and no_buy.correct is False
    assert grade(call(BUY, stop=80), path(flat(19) + [(100.01,) * 4] + flat(5, 100.01)), free).correct is True
    assert grade(call(NO_BUY), path(flat(19) + [(99.99,) * 4] + flat(5, 99.99)), free).correct is True


def test_sell_includes_holder_selling_costs():
    shares, threshold = sell_threshold(call(SELL), P)
    cost = costs.side_cost(shares * 100.0, CALL_DAY, P).total
    assert shares == 1000
    assert threshold == pytest.approx(100.0 - cost / 1000)
    assert cost == pytest.approx(370.0)
    small_fall = grade(call(SELL), path(flat(19, 100) + [(99.7,) * 4]), P)
    assert small_fall.exit_price == 99.7
    assert small_fall.correct is False
    big_fall = grade(call(SELL), path(flat(19, 100) + [(99.5,) * 4]), P)
    assert big_fall.correct is True
    assert big_fall.total_costs == pytest.approx(370.0)
    assert grade(call(SELL), path(flat(19, 100) + [(99.7,) * 4]), free_protocol()).correct is True


def test_hold_and_sell_use_own_entry_when_holding():
    held = call(HOLD, stop=80, reference=100)
    own = Call("TEST", HOLD, CALL_DAY, 100.0, "short", 20, None, 80, position_entry_price=104.0, position_shares=961)
    prices = flat(25, 102)
    assert grade(held, path(prices), P).correct is True
    assert grade(own, path(prices), P).correct is False
    sell = Call("TEST", SELL, CALL_DAY, 100.0, "short", 20, position_entry_price=104.0, position_shares=961)
    assert sell.reference == 104.0
    assert grade(sell, path(flat(20, 102)), P).correct is True
    assert grade(call(SELL), path(flat(20, 102)), P).correct is False


def test_one_open_call_per_stock():
    book = OpenCalls(P)
    first = call(BUY, stop=90)
    assert book.admit(first) == (OPENED, None)
    for kind in (BUY, HOLD, NO_BUY, WAIT):
        status, current = book.admit(call(kind, stop=90, day=CALL_DAY + timedelta(days=7)))
        assert status == BLOCKED and current == first
    assert book.admit(Call("OTHER", BUY, CALL_DAY, 100.0, "short", 20, stop=90))[0] == OPENED
    assert book.admit(Call("THIRD", WAIT, CALL_DAY, 100.0, "short", 20)) == (NOT_OPEN, None)
    assert "THIRD" not in book.open
    book.resolve(first)
    assert book.admit(call(HOLD, stop=90, day=CALL_DAY + timedelta(days=14)))[0] == OPENED
    assert book.admit(call(SELL, day=CALL_DAY + timedelta(days=21)))[0] == BLOCKED


def test_sell_closes_an_open_buy():
    book = OpenCalls(P)
    buy = call(BUY, target=130, stop=85)
    bars = path(flat(8, 100) + [(105, 106, 104, 105)] * 2 + flat(20, 112))
    assert book.admit(buy)[0] == OPENED
    sell_day = bars[8].day
    sell = call(SELL, day=sell_day)
    status, closed = book.admit(sell)
    assert status == CLOSES_BUY and closed == buy
    assert book.open["TEST"] == sell
    out = grade(buy, bars, P, sell_after=sell_day)
    assert out.reason == SELL_CALL
    assert out.exit_date == bars[9].day and out.exit_price == 105
    assert out.correct is True
    held_sell = position_call(sell, out, P)
    assert held_sell.position_entry_price == 100 and held_sell.position_shares == 1000
    sell_path = [b for b in bars if b.day > sell_day]
    assert grade(held_sell, sell_path, P).correct is False
    with pytest.raises(ValueError):
        grade(call(HOLD, stop=80), bars, P, sell_after=sell_day)


def test_sell_call_waits_for_settlement_and_stop_gap_wins():
    early = grade(call(BUY, holding=19, stop=85, target=130), path(flat(25)), P, sell_after=CALL_DAY)
    assert early.reason == SELL_CALL and early.exit_date == path(flat(25))[3].day
    gap = path(flat(4) + [(80, 82, 79, 80)] + flat(20, 80))
    out = grade(call(BUY, stop=85, target=130), gap, P, sell_after=gap[3].day)
    assert out.reason == "stop" and out.correct is False


def test_holdout_reaching_calls_are_ungraded():
    day = date(2025, 8, 3)
    out = grade(call(BUY, holding=40, stop=80, day=day), path(flat(30), start=day), P, sessions_before_holdout=35)
    assert out.status == UNGRADED and out.reason == REACHES_HOLDOUT and out.correct is None
    assert accuracy([out]) == {"graded": 0, "right": 0, "accuracy": None}
    fits = grade(call(BUY, holding=20, stop=80, day=day), path(flat(30), start=day), P, sessions_before_holdout=30)
    assert fits.status == GRADED
    delayed = path(flat(19) + flat(1), start=day, no_trade={19})
    late = grade(call(BUY, holding=20, stop=80, day=day), delayed, P, sessions_before_holdout=20)
    assert late.status == UNGRADED and late.reason == REACHES_HOLDOUT
    for kind in (HOLD, NO_BUY, SELL, WAIT):
        assert grade(call(kind, holding=40, stop=80, day=day), path(flat(30), start=day), P, sessions_before_holdout=35).reason == REACHES_HOLDOUT
    with pytest.raises(ValueError, match="holdout"):
        grade(call(BUY, stop=80, day=date(2025, 9, 20)), path(flat(30), start=date(2025, 9, 20)), P)


def test_2024_long_grades_hidden_until_2025_exam():
    day = date(2024, 3, 3)
    bars = path(flat(205), start=day, gap_days=2)
    out = grade(call(BUY, holding=200, horizon="long", stop=80, day=day), bars, P)
    assert out.status == GRADED and out.exit_date >= date(2025, 1, 1)
    assert hidden(out, set(), P) is True
    assert hidden(out, {"exam_2024"}, P) is True
    assert hidden(out, {"exam_2024", "exam_2025"}, P) is False
    early = grade(call(BUY, holding=150, horizon="long", stop=80, day=day), bars, P)
    assert early.exit_date < date(2025, 1, 1)
    assert hidden(early, set(), P) is False
    mid = grade(call(BUY, holding=133, horizon="mid", stop=80, day=date(2024, 10, 6)), path(flat(140), start=date(2024, 10, 6)), P)
    assert mid.exit_date >= date(2025, 1, 1) and hidden(mid, set(), P) is False
    hold = grade(call(HOLD, holding=200, horizon="long", stop=80, reference=101, day=day), bars, P)
    assert hold.reason == "not_recovered" and hidden(hold, set(), P) is True
    stopped = grade(call(BUY, holding=200, horizon="long", stop=95, day=day), path(flat(5) + [(94, 94, 94, 94)] + flat(200, 94), start=day, gap_days=2), P)
    assert stopped.exit_date < date(2025, 1, 1) and stopped.hold_exit_date >= date(2025, 1, 1)
    assert hidden(stopped, set(), P) is True


def test_floorsheet_derived_bars_before_2018():
    day = date(2017, 3, 1)
    derived = Bar(date(2017, 3, 2), 98.0, 104.0, 95.0, 100.0, 1000.0, 99.0, FLOORSHEET_DERIVED)
    plain = Bar(date(2017, 3, 2), 98.0, 104.0, 95.0, 100.0, 1000.0, 99.0)
    assert effective_bar(derived, P) == derived
    assert (effective_bar(plain, P).open, effective_bar(plain, P).high, effective_bar(plain, P).low) == (100.0, 100.0, 100.0)
    odd = Bar(date(2017, 3, 2), 98.0, 99.0, 97.0, 100.0, 1000.0, 99.0, FLOORSHEET_DERIVED)
    assert effective_bar(odd, P).high == 100.0
    bars = [derived] + [Bar(date(2017, 3, 2) + timedelta(days=i), 100.0, 125.0, 99.0, 101.0, 1000.0, 100.0, FLOORSHEET_DERIVED) for i in range(1, 25)]
    out = grade(call(BUY, target=120, stop=90, day=day), bars, P)
    assert out.entry_price == 98.0
    assert out.reason == "target" and out.exit_price == 120
