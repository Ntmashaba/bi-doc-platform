"""Frozen entry point for the desktop app (windowed: no console).

A windowed executable has no stdout or stderr, so output goes to desktop.log in the
generator home. `--check` reports what the window needs (pywebview and the WebView2
runtime) as JSON in that log and in the exit code, without opening a window.
"""
import json
import sys

from bidoc_generator.doctor import home


def main() -> int:
    base = home()
    base.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        log = open(base / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    args = sys.argv[1:]
    if "--check" in args:
        from bidoc_generator.doctor import webview2_version  # noqa: PLC0415
        try:
            import webview  # noqa: F401, PLC0415
            ok_webview = True
        except ImportError:
            ok_webview = False
        report = {"frozen": bool(getattr(sys, "frozen", False)), "pywebview": ok_webview,
                  "webview2": webview2_version()}
        (base / "desktop-check.json").write_text(json.dumps(report), encoding="utf-8")
        print(json.dumps(report))
        return 0 if ok_webview and report["webview2"] else 3
    from bidoc_generator.cli import main as cli  # noqa: PLC0415
    return cli(["desktop", *args])


if __name__ == "__main__":
    sys.exit(main())
