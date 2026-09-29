"""One command for all component/platform unit suites; add --samples for real files."""
import argparse
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
SUITES=('components/power-bi','components/adf','packages/contracts','packages/engines',
        'packages/relationships','apps/generator','apps/library')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--samples',action='store_true');args=ap.parse_args()
    failed=[]
    for suite in SUITES:
        print(f'\nTesting {suite}',flush=True)
        if subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],cwd=ROOT/suite).returncode:
            failed.append(suite)
    if args.samples and subprocess.run([sys.executable,str(ROOT/'scripts/acceptance.py')],cwd=ROOT).returncode:
        failed.append('public samples')
    if failed:
        sys.exit('Failed: '+', '.join(failed))
