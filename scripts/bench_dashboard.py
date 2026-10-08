#!/usr/bin/env python3
"""Sequential TTFB benchmark for a deployed dashboard (stdlib only).

The session cookie is a CREDENTIAL. It is read from the environment, never from the
command line or the file, so it doesn't land in shell history or git:

    export DASHBOARD_BENCH_SESSIONID='<sessionid cookie value>'   # use a throwaway/own account
    python scripts/bench_dashboard.py https://trackmyrupee.com/dashboard/ -n 30

Reports p50/p95/max TTFB (time to response headers), TLS/connect time, and - if the server
has ENABLE_SERVER_TIMING on - the server-side total/db/query numbers it reports.
Each request uses a fresh connection (as a new visitor would); pass --keepalive to reuse one.
"""
import argparse
import http.client
import os
import re
import statistics
import sys
import time
from urllib.parse import urlsplit


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('-n', type=int, default=20)
    ap.add_argument('--delay', type=float, default=0.5, help='seconds between requests')
    ap.add_argument('--keepalive', action='store_true')
    args = ap.parse_args()

    sid = os.environ.get('DASHBOARD_BENCH_SESSIONID')
    if not sid:
        sys.exit('Set DASHBOARD_BENCH_SESSIONID (see --help). Not reading cookies from argv on purpose.')
    u = urlsplit(args.url)
    if u.scheme not in ('http', 'https'):
        sys.exit('URL must be http(s)')
    cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
    path = (u.path or '/') + (f'?{u.query}' if u.query else '')
    headers = {'Cookie': f'sessionid={sid}', 'Accept-Encoding': 'gzip', 'User-Agent': 'bench-dashboard/1'}

    ttfb, connect, timings = [], [], []
    conn = None
    for i in range(args.n):
        t0 = time.perf_counter()
        if conn is None or not args.keepalive:
            conn = cls(u.netloc, timeout=30)
            conn.connect()
        t1 = time.perf_counter()
        conn.request('GET', path, headers=headers)
        resp = conn.getresponse()
        t2 = time.perf_counter()
        resp.read()
        if resp.status != 200:
            sys.exit(f'HTTP {resp.status} (login redirect? check the session cookie)')
        if not args.keepalive:
            conn.close()
        ttfb.append((t2 - t1) * 1000)
        connect.append((t1 - t0) * 1000)
        st = resp.getheader('Server-Timing')
        if st:
            timings.append(st)
        print(f'{i + 1:3d}: connect {connect[-1]:6.0f}ms  ttfb {ttfb[-1]:6.0f}ms  {st or ""}')
        time.sleep(args.delay)

    print(f'\nTTFB ms  n={len(ttfb)}  p50={statistics.median(ttfb):.0f}  p95={pct(ttfb, 95):.0f}  max={max(ttfb):.0f}')
    print(f'connect ms  p50={statistics.median(connect):.0f}  p95={pct(connect, 95):.0f}')
    if timings:
        def nums(key):
            return [float(m.group(1)) for t in timings for m in [re.search(key + r';dur=([\d.]+)', t)] if m]
        q = [int(m.group(1)) for t in timings for m in [re.search(r'queries;desc="(\d+)"', t)] if m]
        print(f'server total ms p50={statistics.median(nums("total")):.0f} p95={pct(nums("total"), 95):.0f} | '
              f'db ms p50={statistics.median(nums("db")):.0f} | queries min/median/max={min(q)}/{statistics.median(q):.0f}/{max(q)}')
        print('(queries ~5 means the warm dashboard cache hit; ~80 means a cold rebuild)')


if __name__ == '__main__':
    main()
