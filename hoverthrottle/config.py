"""모든 임계값·참고 범위·추천 표를 한곳에 모은 설정 모듈.

섹션 5·6의 값은 출발점이다. 실제 로그로 결과가 이상하면 근거와 함께 여기서만 조정한다.
"""

from dataclasses import dataclass

APP_NAME = "HoverThrottle"

# ---------------------------------------------------------------------------
# 기체 등급
# ---------------------------------------------------------------------------
CLASS_TINYWHOOP = "tinywhoop"
CLASS_TOOTHPICK = "toothpick"
CLASS_5INCH = "5inch"
CLASS_7INCH = "7inch"

CRAFT_CLASSES = {
    CLASS_TINYWHOOP: "타이니훕 (65~75mm)",
    CLASS_TOOTHPICK: "투스·시네훕 (2~3인치)",
    CLASS_5INCH: "5인치",
    CLASS_7INCH: "7인치 이상",
}

# 6-5. 기체 등급별 정상 호버 범위 (모터 %) 와 판정 문구 — "참고 범위"
CLASS_HOVER_RANGE = {
    CLASS_TINYWHOOP: {
        "range": (35.0, 55.0),
        "above": "무겁거나 출력 부족 — 출력 여유가 적습니다",
        "below": "양호 (과출력 기체)",
    },
    CLASS_TOOTHPICK: {
        "range": (30.0, 45.0),
        "above": "무게·프롭 점검을 권장합니다",
        "below": "과출력 — 스로틀 리밋을 고려해 보세요",
    },
    CLASS_5INCH: {
        "range": (18.0, 30.0),
        "above": "무게 과다 가능성이 있습니다",
        "below": "과출력 — 스로틀 리밋을 고려해 보세요",
    },
    CLASS_7INCH: {
        "range": (20.0, 32.0),
        "above": "장거리 효율이 떨어질 수 있습니다",
        "below": "과출력",
    },
}

# ---------------------------------------------------------------------------
# 5. 호버 구간 검출
# ---------------------------------------------------------------------------
MODE_STRICT = "strict"
MODE_NORMAL = "normal"
MODES = {MODE_STRICT: "엄격 (순수 호버만)", MODE_NORMAL: "일반 (완만한 크루징 포함)"}


@dataclass(frozen=True)
class HoverThresholds:
    gyro_rp_rms: float          # 롤·피치 자이로 RMS 상한 (deg/s)
    gyro_yaw_rms: float         # 요 자이로 RMS 상한 (deg/s)
    stick_rp_dev_pct: float     # 롤·피치 스틱 중앙 편차 상한 (% of full deflection)
    throttle_std_pct: float     # 스로틀 스틱 표준편차 상한 (%)
    motor_std_pct: float        # 모터 평균 출력 표준편차 상한 (%)
    acc_z_tol_g: float          # Z 가속도 1G 허용 오차 (G)
    throttle_min_pct: float = 5.0
    throttle_max_pct: float = 95.0


THRESHOLDS = {
    MODE_STRICT: HoverThresholds(25.0, 40.0, 5.0, 3.0, 4.0, 0.10),
    MODE_NORMAL: HoverThresholds(60.0, 90.0, 15.0, 6.0, 8.0, 0.20),
}

SMOOTH_WINDOW_S = 0.1           # 노이즈 평활 이동평균 길이
WINDOW_S = 0.5                  # 판정 윈도우 길이
WINDOW_OVERLAP = 0.5            # 윈도우 겹침 비율
MIN_SEGMENT_S = 1.0             # 호버 구간으로 채택할 최소 연속 길이
LOW_CONFIDENCE_TOTAL_S = 3.0    # 채택 구간 총합이 이보다 짧으면 "신뢰도 낮음"
TAKEOFF_LANDING_TRIM_S = 2.0    # 이륙 직후 / 착륙 직전 제외 시간
PROPWASH_FACTOR = 3.0           # 자이로 RMS가 임계값의 이 배수를 넘으면 기동으로 간주
PROPWASH_HOLDOFF_S = 1.0        # 기동 직후 제외 시간
STICK_PERCENTILE = 95           # 윈도우 내 롤·피치 스틱 편차를 볼 백분위수

# 비행(공중) 구간 판정: 스로틀이 이 값을 넘는 구간을 비행으로 본다.
AIRBORNE_THROTTLE_PCT = 5.0
AIRBORNE_MERGE_GAP_S = 1.0      # 이보다 짧은 끊김은 같은 비행으로 합친다
AIRBORNE_MIN_S = 3.0            # 이보다 짧은 비행 구간은 무시

# rcCommand[0..2] 최대 편향 (Betaflight: 중앙 기준 ±500)
RC_DEFLECTION_FULL = 500.0

# ---------------------------------------------------------------------------
# 6. 추천값
# ---------------------------------------------------------------------------
THR_MID_LIMITS = (10, 90)

# 6-3. (호버 스틱 % 상한(미만), 추천 expo 범위, 의미)
THR_EXPO_TABLE = [
    (20.0, (30, 40), "과출력 기체 — 호버 근처 해상도 확보"),
    (30.0, (20, 30), "일반적인 프리스타일"),
    (45.0, (10, 20), "표준"),
    (float("inf"), (0, 10), "출력 여유 적음 — 곡선 왜곡 최소화"),
]

# 6-4. 스로틀 리밋 제안
THROTTLE_LIMIT_SUGGEST_BELOW_MOTOR_PCT = 15.0
THROTTLE_LIMIT_TARGET_HOVER = 0.25          # 리밋 적용 후 호버가 25%가 되도록
THROTTLE_LIMIT_PERCENT_RANGE = (60, 100)

# 6-1. 전압 보정
VOLTAGE_POINTS = [(4.1, "초반"), (3.8, "중반"), (3.5, "후반")]
VOLTAGE_R2_MIN = 0.3

# 6-6. 추력비 추정 지수 범위
TWR_EXPONENTS = (1.3, 1.8)

# 셀 수 자동 추정에 쓰는 셀당 최대 전압 (HV 포함)
CELL_MAX_V = 4.35
