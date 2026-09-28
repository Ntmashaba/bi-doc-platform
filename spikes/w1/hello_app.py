"""W1 packaging probe: a frozen app that imports pywebview and proves bundled data
files are readable. `--smoke` exits without opening a window (CI has no desktop)."""
import json
import sys
from pathlib import Path

import webview  # noqa: F401  (import proves pywebview was bundled)

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def main():
    page = (BASE / "hello.html").read_text(encoding="utf-8")
    report = {"python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
              "asset_bytes": len(page.encode()), "pywebview": webview.__version__ if hasattr(webview, "__version__") else "unknown"}
    print(json.dumps(report))
    if "--smoke" not in sys.argv:
        webview.create_window("W1 probe", html=page)
        webview.start()


if __name__ == "__main__":
    main()
