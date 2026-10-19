import sys

from .applog import setup_logging

CLI_COMMANDS = {"list", "analyze", "-h", "--help"}


def main() -> int:
    setup_logging()
    if len(sys.argv) > 1 and sys.argv[1] in CLI_COMMANDS:
        from .cli import main as cli_main

        return cli_main()
    # 인자가 없거나 파일 경로뿐이면 GUI (exe 에 로그 파일을 끌어다 놓은 경우 포함)
    from .ui.main_window import run_app

    return run_app(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
