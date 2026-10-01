from __future__ import annotations

from datetime import date

PROTOCOL_VERSION = "accuracy-v1"
GRADE_VERSION = "accuracy-v1"
VARIANT_FAMILY = "scorecard_accuracy_v1"

DEVELOPMENT_START = date(2014, 6, 1)
DEVELOPMENT_END = date(2025, 1, 19)
HOLDOUT_START = date(2025, 9, 30)
FIRST_REAL_OPEN_SESSION = date(2018, 2, 18)

SHORT = (5, 10, 20)
MID = (40, 80, 120)
LONG = (160, 240)
HORIZONS = SHORT + MID + LONG
COSTS = (0.005, 0.010, 0.015)
PRIMARY_COST = 0.010
MAX_EXIT_DEFERRAL = 20
DEFAULT_PROBABILITY = 0.5

STATUS_PENDING = "pending"
STATUS_FILLED = "filled"
STATUS_UNFILLED = "unfilled"
STATUS_BLOCKED = "blocked"
STATUS_STRANDED = "stranded"
STATUS_DATA_ERROR = "data_error"
STATUSES = (STATUS_PENDING, STATUS_FILLED, STATUS_UNFILLED, STATUS_BLOCKED, STATUS_STRANDED, STATUS_DATA_ERROR)

GATE_WIN_RATE = 0.62
GATE_LOWER_BOUND = 0.55
GATE_EDGE = 0.08
GATE_MIN_CALLS = 150
GATE_MIN_DATES = 60
GATE_FOLDS = 4
GATE_FOLDS_POSITIVE = 3
GATE_ECE = 0.05
GATE_MEAN_CALIBRATION = 0.05
GATE_MAX_DATA_ERROR = 0.01
ONE_SIDED_ALPHA = 0.10
RELIABILITY_MIN_BUCKET = 30
IMPLAUSIBLE_WIN_RATE = 0.80

FAILURE_MARGIN = 0.02
AUDIT_SAMPLE_DATES = 40
AUDIT_SEED = 20261001

MONITOR_CALLS = 60
MONITOR_SESSIONS = 120
MONITOR_STEP = 20

SITUATIONS = (
    "all",
    "market_bull",
    "market_sideways",
    "market_overheated",
    "market_bear",
    "pre_book_close",
    "post_book_close",
    "new_listing",
    "post_upper_circuit",
    "post_lower_circuit",
    "volume_anomaly",
    "rate_rising",
    "rate_falling",
)
OVERHEATED_RATIO = 1.20
PRE_BOOK_CLOSE_SESSIONS = 15
POST_BOOK_CLOSE_SESSIONS = 10
NEW_LISTING_SESSIONS = 60
UPPER_STREAK_MIN = 2
POST_UPPER_SESSIONS = 10
POST_LOWER_SESSIONS = 5
VOLUME_RATIO = 5.0
VOLUME_LOOKBACK = 60
VOLUME_MIN_HISTORY = 40
RATE_LOOKBACK_SESSIONS = 60
RATE_CHANGE_POINTS = 1.0


def horizon_class(horizon: int) -> str:
    if horizon in SHORT:
        return "short"
    if horizon in MID:
        return "mid"
    return "long"
