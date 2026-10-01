"""Stand-in for pbi-tools.exe in tests: `extract SRC -extractFolder DEST -modelSerialization Raw`.

FAKE_PBI_MODE: ok (write a Raw extract), fail (exit 3), crash (cause first, long stack trace last, exit 247), hang (sleep), tree (start a
grandchild that sleeps and write its PID to FAKE_PBI_PIDFILE, then sleep).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
assert args[0] == "extract" and args[2] == "-extractFolder" and args[4:] == ["-modelSerialization", "Raw"], args
dest = Path(args[3])
mode = os.environ.get("FAKE_PBI_MODE", "ok")
print(f"fake pbi-tools {mode}: {args[1]}", flush=True)
if mode == "fail":
    print("Power BI Desktop could not open the file.", flush=True)
    sys.exit(3)
if mode == "crash":
    print("ERROR: Could not load file or assembly 'Microsoft.AnalysisServices.Tabular' for D:\\Reports\\Secret.pbix", flush=True)
    print("Unhandled exception. System.IO.FileNotFoundException: Could not load file or assembly", flush=True)
    for i in range(40):
        print(f"   at Frame.Number{i}.Method{i}(Object sender, EventArgs e) in C:\\build\\src\\Frame{i}.cs:line {i}", flush=True)
    sys.exit(247)
if mode == "hang":
    time.sleep(600)
if mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    Path(os.environ["FAKE_PBI_PIDFILE"]).write_text(str(child.pid))
    time.sleep(600)
model = json.loads(Path(os.environ["FAKE_PBI_MODEL"]).read_text(encoding="utf-8"))
(dest / "Model").mkdir(parents=True)
(dest / "Model" / "database.json").write_text(json.dumps({"name": "Fake", "compatibilityLevel": 1550, **model}),
                                               encoding="utf-8")
page = dest / "Report" / "sections" / "000_ReportSection"
page.mkdir(parents=True)
(dest / "Report" / "report.json").write_text(json.dumps({"config": "{}", "layoutOptimization": 0}), encoding="utf-8")
(page / "section.json").write_text(json.dumps({"displayName": "Overview", "name": "ReportSection", "ordinal": 0}),
                                   encoding="utf-8")
