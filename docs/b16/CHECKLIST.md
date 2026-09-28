# B16 checklist: unattended PBIX extraction on a real Windows host

B16 is the release gate for R3 hosted processing (handoff 10, 12). It has to show that a
real PBIX extracts with Power BI Desktop and pbi-tools under the account the worker will
use:

- after a reboot;
- with the session locked;
- with the account signed out, but only if unattended operation is claimed.

Microsoft documents restrictions on running Power BI Desktop without a user session, so
nothing is assumed. The result lists exactly the conditions that passed. R3 is released
only for those. Anything not listed stays on the supported route: the generator on the
person's own computer.

Budget about two hours, most of it waiting for scheduled runs and a reboot.

The script `packaging/windows/b16/b16_gate.py` does the measuring. Each run:

- extracts a PBIX with pbi-tools, generates a shared document and validates it;
- appends one JSON line to a results file: timings, object counts, versions, account,
  session id, window station, whether an input desktop exists (none when locked or
  signed out), and uptime (which proves a run came after a reboot);
- keeps no report content (no measure names, queries or data). Error messages can include
  file paths, so read the report before sharing it.

## 0. Prepare (signed in as an administrator)

1. Pick the machine and the account.
   - The account is the one the worker will really run as: a dedicated domain or local
     account, not LocalSystem (the handoff makes no LocalSystem promise).
   - The account needs *Log on as a batch job*. Task Scheduler grants it when you save a
     task with a password.
2. As that account, install:
   - Power BI Desktop (the same version you will support; record Microsoft Store vs
     installer);
   - pbi-tools Desktop (not `pbi-tools.core`);
   - the BI Documentation Generator (`bidoc-setup-<version>.exe`).
3. As that account, run `bidoc config --pbi-tools "C:\Tools\pbi-tools\pbi-tools.exe"` and
   then `bidoc doctor`. `bidoc doctor` must report PBIX extraction available.
4. Create `C:\b16\`. Copy in one representative PBIX of normal size, and a larger one if
   you have it. Give the account read access.
5. Put the script where the account can run it, for example
   `C:\b16\b16_gate.py`, from `packaging/windows/b16/`. Use the generator's Python:
   either the one in a development checkout's virtual environment, or any Python 3.11
   with `pip install bi-doc-generator` from the repository. The installed `bidoc.exe`
   cannot run scripts.

In the commands below, `py` means that Python, and `--results C:\b16\results.jsonl` is the
same file for every run.

## 1. Baseline, signed in and unlocked

Signed in as the account:

```bat
py C:\b16\b16_gate.py env
py C:\b16\b16_gate.py extract --scenario interactive --pbix C:\b16\Sample.pbix --results C:\b16\results.jsonl
```

- `env` must show `"interactive_station": true` and `"input_desktop_available": true`.
- Run `extract` **three times**; each must say `interactive: passed`.
- If this fails, stop: fix the installation first (the error and `pbi-tools info` output
  are in the results).

## 2. After a reboot, before anyone signs in

1. Create the startup task. It asks for the account's password, so the task can run
   whether or not anyone is signed in:

   ```bat
   py C:\b16\b16_gate.py schedule --scenario after_reboot --account CONTOSO\svc-bidoc ^
      --pbix C:\b16\Sample.pbix --results C:\b16\results.jsonl
   ```

2. Restart the machine and **do not sign in** for 10 minutes.
3. Sign in and check `C:\b16\results.jsonl`. The new `after_reboot` line should show a
   small `uptime_minutes`, a non-interactive window station, and the status.
4. Repeat the restart twice more, for three `after_reboot` runs in total.

## 3. Session locked

1. Signed in as the account, schedule a run 5 minutes ahead. `--at` is the local time:

   ```bat
   py C:\b16\b16_gate.py schedule --scenario locked --at 14:05 --account CONTOSO\svc-bidoc ^
      --pbix C:\b16\Sample.pbix --results C:\b16\results.jsonl
   ```

2. Press **Win+L** and wait until after the scheduled time plus 5 minutes.
3. Unlock and check the new `locked` line. It must show `"input_desktop_available": false`,
   which proves the session was locked.
4. Repeat twice more with new times (the same command with a new `--at` replaces the task).

## 4. Signed out (only if unattended operation will be claimed)

1. Schedule a run 10 minutes ahead. It asks for the password:

   ```bat
   py C:\b16\b16_gate.py schedule --scenario logged_off --at 14:20 --account CONTOSO\svc-bidoc ^
      --pbix C:\b16\Sample.pbix --results C:\b16\results.jsonl
   ```

2. **Sign out** (not just lock). Wait until after the scheduled time plus 5 minutes.
3. Sign back in and check the new `logged_off` line. It should show the task ran in a
   non-interactive session.
4. Repeat twice more.

## 5. The worker end to end

This needs a library that the worker machine can reach, and an administrator there.

1. In the library, go to **Settings → Enroll a worker**, and copy the token.
2. As the account, run `bidoc worker connect https://<library>` and paste the token.
3. **Signed in.** Upload the PBIX under **Process PBIX**, then run:

   ```bat
   py C:\b16\b16_gate.py worker --scenario worker_interactive --results C:\b16\results.jsonl
   ```

   The job must reach **Published**, and **Open document** must show the report.
4. **Unattended.**
   1. Upload another copy of the PBIX, as a new version.
   2. Schedule the worker:

      ```bat
      py C:\b16\b16_gate.py schedule --scenario worker_unattended --at 15:10 --account CONTOSO\svc-bidoc ^
         --results C:\b16\results.jsonl
      ```

   3. Lock the screen or sign out. Afterwards, check that the job is **Published** and
      the new line is `passed`.
5. **Recovery (A16, A17 on real hardware).**
   1. Upload once more.
   2. Run `bidoc worker run --once` in a console. While the job shows **Processing —
      extracting**, end the `bidoc` process and its `pbi-tools` / Power BI Desktop
      children in Task Manager.
   3. After 2 minutes (the lease), run:

      ```bat
      py C:\b16\b16_gate.py worker --scenario worker_recovery --results C:\b16\results.jsonl
      ```

   4. The job must show attempt 2, then **Published**. The document must have exactly
      one new version, and no Power BI Desktop or pbi-tools process must be left running.
6. **Cancellation.** Upload, start `bidoc worker run --once`, and press **Cancel** in the
   library while it extracts. The job must show **Cancelled**, the processes must be
   gone, and `%LOCALAPPDATA%\bidoc\worker\<job id>` must not exist.

## 6. Report and clean up

```bat
py C:\b16\b16_gate.py report --results C:\b16\results.jsonl --out C:\b16\VERIFICATION.md
py C:\b16\b16_gate.py unschedule
```

**Verdicts.** A condition is **supported** only if it passed at least twice and none of
its latest three runs failed. Otherwise it is **not supported**, or **not verified** if it
never ran. The conclusion says what may be released.

**Committing the report.** Read it for anything sensitive (host names, paths in error
messages). Then commit it as `docs/b16/VERIFICATION.md` with `results.jsonl` beside it,
or send both back and I will add them.

## What the result decides

| Verified | R3 release |
|---|---|
| Nothing, or `interactive` fails | Not released. Processing stays unavailable, and the generator is the route |
| `interactive` only | Released only while the worker account stays signed in and unlocked. Say so in the deployment notes and on the Processing page |
| `after_reboot` and `locked` | Released for a signed-in worker account that may be locked, and starts after reboots |
| `logged_off` as well | Released for unattended operation, as a scheduled task running whether signed in or not |

The worker scenarios must pass for any release. Record the Power BI Desktop and pbi-tools
versions from the report in the release notes. Other versions are unverified until
re-tested.
