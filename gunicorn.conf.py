"""Gunicorn defaults, auto-loaded from the working directory.

Anything passed on the command line or via env (e.g. WEB_CONCURRENCY) still wins, so this only
changes behaviour for deployments that start `gunicorn finance_tracker.wsgi` with no tuning.

Why threads rather than more workers: production uses a per-process LocMemCache (no Redis), so every
extra worker process is another cold cache. A single worker with several threads shares one cache
(LocMemCache is thread-safe) while still letting a slow request (cold dashboard render) not block
cheap ones like /settings/. Raise GUNICORN_WORKERS only after adding a shared cache.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = 'gthread'
workers = int(os.environ.get('GUNICORN_WORKERS', os.environ.get('WEB_CONCURRENCY', '1')))
threads = int(os.environ.get('GUNICORN_THREADS', '8'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '60'))
keepalive = 5
# Recycle workers occasionally to bound memory on the small instance.
max_requests = 2000
max_requests_jitter = 200
