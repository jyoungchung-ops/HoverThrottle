# HoverThrottle — FPV 호버링 스로틀 추천

Betaflight 블랙박스 로그에서 실제 호버링 스로틀을 찾아 `thr_mid` / `thr_expo` 추천값과
Betaflight CLI 명령어를 만들어 주는 Windows 데스크톱 프로그램입니다. 인터넷 연결 없이 동작합니다.

- 1S 타이니훕부터 7인치 6S까지 기체 등급별 참고 범위로 상태 판정
- 호버 구간 자동 검출 (자세 안정 + 스로틀 안정 + 수직 가속도 ≈ 1G)
- 전압별(셀당 4.1 / 3.8 / 3.5V) 예상 호버, 추력비 근사치
- 그래프, HTML 리포트, 호버 원시 데이터 CSV, 기체 프리셋

## 내려받기

[Releases](../../releases/latest) 페이지에서 `HoverThrottle.exe` 를 받으세요. 설치 없이 바로 실행됩니다
(Windows 10/11 64비트).

- 처음 실행할 때 "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행** 을 누르세요.
  디지털 서명이 없는 프로그램이라 나오는 안내입니다.
- 첫 실행은 글꼴 정보를 준비하느라 10~20초 걸릴 수 있습니다.

## 사용법

1. `HoverThrottle.exe` 를 더블클릭합니다 (설치 필요 없음).
2. 왼쪽에 **기체 등급**과 **셀 수**를 고릅니다. 셀 수는 "자동"이면 로그 전압으로 추정합니다.
   이륙 중량을 넣으면 최대 추력 추정치도 나옵니다.
3. 로그 파일(`.bbl` `.bfl` `.txt` `.csv`)을 목록에 끌어다 놓거나 **파일 선택**을 누릅니다.
   한 파일에 비행이 여러 개면 **비행 선택**에서 분석할 비행을 체크합니다.
4. **분석 실행**을 누릅니다.
5. 결과 탭
   - **요약**: 호버 스로틀(스틱 % / 모터 %), 신뢰도, 기체 상태, 추천값과 현재값 비교, 전압별 예상 호버, 추력비
   - **그래프**: 시간축 스로틀·모터(호버 구간 음영), 셀당 전압 대 호버 모터, 호버 분포
   - **CLI**: **클립보드 복사** → Betaflight Configurator CLI 탭에 붙여 넣기
6. **결과 저장 (HTML)** 은 로그와 같은 폴더에 `<로그이름>_hover_report.html` 을 만듭니다.

호버 구간을 못 찾거나 3초 미만이면 **일반 모드로 재분석** 버튼이 나옵니다. 일반 모드 결과는
완만한 크루징이 섞인 "크루징 포함 추정치"입니다.

### 결과 읽는 법

- **스틱 %** 는 블랙박스의 `rcCommand[3]`, 즉 현재 thr_mid/thr_expo 곡선을 *통과한 뒤* 스로틀입니다.
  호버에 필요한 이 값을 `thr_mid` 로 두면 곡선의 평평한 중심이 실제 호버 지점에 놓입니다.
  곡선을 역산한 실제 스틱 위치도 요약 탭에 함께 표시합니다.
- **모터 %** 는 헤더의 `motorOutput` 범위(아이들~최대) 기준 모터 평균 출력입니다.
- `thr_expo` 추천은 시작점입니다. 취향에 맞게 조정하세요.
- 기체 상태 범위와 추력비는 일반적인 경험치에 기반한 **참고값/근사치**입니다.

## 블랙박스 로깅 권장 설정 (Betaflight 4.x)

Configurator CLI 에서:

```
set blackbox_mode = NORMAL
set blackbox_sample_rate = 1/4
set blackbox_disable_rc = OFF
set blackbox_disable_motors = OFF
set blackbox_disable_gyro = OFF
set blackbox_disable_acc = OFF
set blackbox_disable_bat = OFF
save
```

- `sample_rate` 는 1/2~1/8 이면 충분합니다 (호버 분석은 수백 Hz면 됨). 4.2 이하에서는 Blackbox 탭의 로깅 비율로 설정하세요.
- RC·모터·자이로는 **필수**, 배터리(전압 보정 표)와 가속도계(호버 판정 정확도)는 권장입니다.
- 비행 방법: 지면 효과가 없는 높이(1.5m 이상)에서 **10~20초 제자리 호버**를 2~3번 넣고,
  가능하면 배터리 한 팩을 끝까지 비행하면 전압별 예상 호버가 정확해집니다.

## 명령줄 (개발·검증용)

```
python -m hoverthrottle                         # GUI
python -m hoverthrottle list LOG00001.BBL       # 비행 목록
python -m hoverthrottle analyze LOG00001.BBL --class 5inch --cells 6 --weight 650
python -m hoverthrottle analyze a.bbl --class tinywhoop --mode normal --dump-windows out\
```

`--class` 는 `tinywhoop`, `toothpick`, `5inch`, `7inch` 중 하나입니다. `--dump-windows` 는 0.5초
윈도우별 판정 지표를 CSV 로 저장합니다 (임계값 검토용). 임계값과 참고 범위는 모두
`hoverthrottle/config.py` 한곳에 있습니다.

## 빌드

### GitHub Actions (권장)

`.github/workflows/build-windows.yml` 이 push 마다 windows-latest 에서
의존성 설치 → 테스트 → `blackbox_decode.exe` 소스 빌드 → PyInstaller → 실행 확인 순서로 돌고,
`HoverThrottle-windows` Artifact 로 exe 를 올립니다. `v*` 태그를 push 하면 Release 에도 첨부합니다.

```
git tag v0.1.0
git push origin v0.1.0
```

### Windows PC 에서 직접

Python 3.12 64비트를 설치한 뒤:

```
build.bat
```

결과는 `dist\HoverThrottle.exe` 입니다. `build.bat notest` 는 테스트를 건너뜁니다.
`bin\blackbox_decode.exe` 를 넣어 두면 함께 번들됩니다 (없어도 orangebox 디코더로 동작).

### macOS / 개발 환경

```
python3.12 -m venv .venv && source .venv/bin/activate
python tools/install_deps.py
python -m pytest
python -m hoverthrottle
```

`orangebox` 0.5.0 은 최신 pip 에서 스크립트 진입점 오류(종료 코드 1)를 내지만 라이브러리는
정상 설치됩니다. `tools/install_deps.py` 가 이 오류만 무시하고 import 로 확인합니다.

## 프로젝트 구조

```
hoverthrottle/
  __main__.py          진입점 (인자 없으면 GUI, list/analyze 면 CLI)
  config.py            임계값, 기체 등급별 범위, expo 표
  io/log_reader.py     LogReader 인터페이스 — orangebox / blackbox_decode / CSV
  io/normalize.py      열 이름 별칭, 단위·스케일 정규화
  analysis/hover_detect.py   호버 구간 검출
  analysis/recommend.py      추천값 계산 (Betaflight 스로틀 곡선 포함)
  analysis/pipeline.py       읽기 → 검출 → 추천 흐름
  ui/main_window.py    PySide6 메인 창
  ui/plots.py          matplotlib 그래프
  ui/report_html.py    요약·HTML 리포트·CSV
  presets.py           %APPDATA%\HoverThrottle\presets.json
tests/                 합성 로그 테스트 + tests/fixtures 실제 로그 회귀 테스트
```

상세 로그는 `%APPDATA%\HoverThrottle\app.log` 에 기록됩니다.

## 라이선스

HoverThrottle 은 [GPL-3.0](LICENSE) 으로 배포됩니다.

### 서드파티 고지

배포되는 `HoverThrottle.exe` 에는 아래 서드파티 소프트웨어가 포함됩니다.

| 구성 요소 | 라이선스 | 비고 |
| --- | --- | --- |
| [orangebox](https://github.com/atomgomba/orangebox) | GPL-3.0 | 블랙박스 로그 파서 (1순위 디코더) |
| [blackbox-tools](https://github.com/betaflight/blackbox-tools) `blackbox_decode` | GPL-3.0 | 2순위 디코더, CI 에서 소스 빌드해 번들 (`blackbox-tools-LICENSE.txt` 동봉) |
| PySide6 (Qt for Python) | LGPL-3.0 | GUI |
| matplotlib | Matplotlib License (PSF 기반) | 그래프 |
| numpy, pandas | BSD-3-Clause | 데이터 처리 |

GPL-3.0 구성 요소를 함께 묶어 배포하므로 exe 배포본 전체가 GPL-3.0 조건을 따릅니다.
소스코드는 이 저장소에서 받을 수 있습니다.
