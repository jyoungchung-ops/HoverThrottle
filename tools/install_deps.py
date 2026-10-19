"""의존성 설치 (build.bat · GitHub Actions 공용).

orangebox 0.5.0 은 setup 의 스크립트 진입점이 잘못돼 pip 가 오류 코드를 돌려주지만
패키지 자체는 설치된다. 그 오류만 무시하고 실제 import 로 설치 여부를 확인한다.
"""

import subprocess
import sys

ORANGEBOX = "orangebox==0.5.0"


def pip(*args: str, check: bool = True) -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args]
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check).returncode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    build = "--build" in sys.argv
    pip("-r", "requirements.txt")
    if build:
        pip("-r", "requirements-build.txt")
    rc = pip("--no-deps", ORANGEBOX, check=False)
    probe = subprocess.run([sys.executable, "-c", "import orangebox, orangebox.reader; print(orangebox.__file__)"])
    if probe.returncode != 0:
        print(f"orangebox 설치 실패 (pip 종료 코드 {rc})", file=sys.stderr)
        return 1
    if rc != 0:
        print("참고: orangebox 진입점 경고는 무시했습니다 (라이브러리는 정상 설치됨).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
