"""Entry point.

  Mettaloid.exe                 normal start
  Mettaloid.exe --hidden        start in the tray (used by Windows startup)
  Mettaloid.exe --engine job    run the Find Frame engine (no Qt is loaded)
"""
import os
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--engine":
        # lightweight path: only OpenCV/numpy are imported
        from mettaloid.engine import findframe_engine
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        findframe_engine.main()
        return 0
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    from mettaloid import VERSION
    from mettaloid.core import logs
    logs.setup(VERSION)
    from mettaloid.app import App
    return App(sys.argv).run(hidden="--hidden" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
