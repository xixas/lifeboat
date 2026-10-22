import sys


def main(argv: list[str] | None = None) -> int:
    from lifeboat.app.application import App
    return App().run([sys.argv[0]] + list(argv or []))
