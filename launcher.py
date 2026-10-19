"""PyInstaller 진입점 (python -m hoverthrottle 과 같은 동작)."""

import sys

from hoverthrottle.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
