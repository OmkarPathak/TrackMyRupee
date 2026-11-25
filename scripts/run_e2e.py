#!/usr/bin/env python3
"""Run the browser tests (e2e/) in several processes at once.

Django's own --parallel cannot do this: the browser tests need a database file (the app and the test run on different
threads) and on macOS Django's parallel runner only knows how to clone an in-memory one. So this starts one
`manage.py test` per group of test files, each with its own database file (E2E_DB_SUFFIX) and its own browser, and
reports them together.

    python scripts/run_e2e.py                      # everything, 6 processes
    python scripts/run_e2e.py -w 8                 # 8 processes
    python scripts/run_e2e.py loans                # only the files with "loans" in their name
    python scripts/run_e2e.py loans phone -w 4     # several words: any of them
    python scripts/run_e2e.py --fresh              # build each test database again (default: keep them, which is faster)
    python scripts/run_e2e.py --list               # show how the files are split, run nothing

Test files are shared out by how many tests they hold, the biggest first, so the processes finish at about the same time.
The exit status is 0 only when every process passed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / 'e2e'
LOGS = Path(os.environ.get('TMPDIR', '/tmp')) / 'tmr_e2e_logs'
TIMINGS = ROOT / '.e2e_timings.json'       # seconds each file took last time (not in git): the next run shares the files out by it


def known_seconds():
    try:
        return json.loads(TIMINGS.read_text())
    except (OSError, ValueError):
        return {}


def modules(words):
    """[(file, weight)]: seconds it took last time, else its number of tests times a typical 3 seconds."""
    seen = known_seconds()
    found = []
    for path in sorted(E2E.glob('test_*.py')):
        count = len(re.findall(r'^\s+def test_', path.read_text(), re.M))
        if count and (not words or any(word in path.stem for word in words)):
            found.append((path.stem, seen.get(path.stem) or count * 3.0))
    return found


def remember(numbers):
    """Fold the timings the processes wrote into the file, replacing the files that ran."""
    seen = known_seconds()
    totals = {}
    for number in numbers:
        path = LOGS / f'timings{number}.txt'
        if path.exists():
            for line in path.read_text().splitlines():
                name, _space, seconds = line.partition(' ')
                totals[name] = totals.get(name, 0.0) + float(seconds)
    seen.update({name: round(seconds, 1) for name, seconds in totals.items()})
    TIMINGS.write_text(json.dumps(seen, indent=1, sort_keys=True))


def split(found, workers):
    """Greedy: the biggest file goes to the process with the least so far."""
    groups = [{'modules': [], 'weight': 0} for _ in range(min(workers, max(1, len(found))))]
    for name, count in sorted(found, key=lambda item: -item[1]):
        target = min(groups, key=lambda group: group['weight'])
        target['modules'].append(name)
        target['weight'] += count
    return [group for group in groups if group['modules']]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('words', nargs='*', help='run only the files whose name contains any of these words')
    parser.add_argument('-w', '--workers', type=int, default=6, help='processes at once (default 6)')
    parser.add_argument('--fresh', action='store_true', help='build the test databases again instead of keeping them')
    parser.add_argument('--list', action='store_true', help='show the split and stop')
    args = parser.parse_args()

    found = modules(args.words)
    if not found:
        print('No test files match.')
        return 2
    groups = split(found, args.workers)
    print(f'{len(found)} files, {len(groups)} processes')
    for number, group in enumerate(groups, 1):
        print(f'  process {number}: about {group["weight"]:>4.0f}s  {", ".join(group["modules"])}')
    if args.list:
        return 0

    LOGS.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    running = []
    for number, group in enumerate(groups, 1):
        log = LOGS / f'process{number}.log'
        command = [sys.executable, 'manage.py', 'test', *[f'e2e.{name}' for name in group['modules']], '--noinput']
        if not args.fresh:
            command.append('--keepdb')
        (LOGS / f'timings{number}.txt').unlink(missing_ok=True)
        env = dict(os.environ, RUN_E2E='1', E2E_DB_SUFFIX=str(number), E2E_TIMINGS=str(LOGS / f'timings{number}.txt'))
        handle = open(log, 'w')
        running.append((number, group, log, handle, subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT)))

    failed = False
    for number, group, log, handle, process in running:
        code = process.wait()
        handle.close()
        text = log.read_text(errors='replace')
        summary = [line for line in text.splitlines() if re.match(r'^(Ran \d+ tests?|OK|FAILED)', line)]
        problems = [line for line in text.splitlines() if re.match(r'^(ERROR|FAIL):', line)]
        status = 'passed' if code == 0 else 'FAILED'
        print(f'process {number}: {status}  {" / ".join(summary)}')
        for line in problems:
            print(f'    {line}')
        if code:
            failed = True
            print(f'    full output: {log}')
    remember(range(1, len(groups) + 1))
    print(f'{"FAILED" if failed else "All passed"} in {time.monotonic() - started:.0f}s')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
