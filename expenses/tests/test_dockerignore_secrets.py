"""`COPY . /app/` copies everything in the build folder that .dockerignore does not exclude, and a local build folder
holds the real .env, database dumps and virtualenvs. These checks keep those out of any image."""
import fnmatch
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)


def patterns():
    lines = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    return [line.strip().rstrip('/') for line in lines if line.strip() and not line.strip().startswith('#')]


def ignored(name):
    """Whether a top-level file or folder name is matched by a .dockerignore pattern (the plain patterns used here)."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns())


class DockerIgnoreTests(SimpleTestCase):
    def test_the_dockerfile_copies_the_whole_folder_so_the_ignore_list_is_what_protects_it(self):
        self.assertIn('COPY . /app/', (ROOT / 'Dockerfile').read_text(encoding='utf-8'))

    def test_secrets_and_environment_files_are_excluded(self):
        for name in ('.env', '.env.local', '.env.production', '.env12', 'server.pem', 'private.key'):
            self.assertTrue(ignored(name), name)

    def test_databases_and_dumps_are_excluded(self):
        for name in ('db.sqlite3', 'db.sqlite3-wal', 'backup_2026-06-06_084556.dump', 'backup_21_03_2026.sql',
                     'backup_pre_ledger_cutover.sql', 'export.sql', 'prod.dump'):
            self.assertTrue(ignored(name), name)

    def test_what_the_app_needs_is_not_excluded(self):
        for name in ('manage.py', 'requirements.txt', 'expenses', 'finance_tracker', 'templates', 'static', 'locale',
                     'gunicorn.conf.py', 'entrypoint.sh'):
            self.assertFalse(ignored(name), name)

    def test_no_tracked_file_is_swallowed_by_the_new_patterns(self):
        """If the repository tracks a file one of these patterns matches, the image would silently lose it."""
        try:
            tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('not a git checkout')
        sensitive = ('.env*', '.env12', '*.sqlite3', '*.sqlite3-shm', '*.sqlite3-wal', 'backup*', '*.sql', '*.dump', '*.pem', '*.key')
        swallowed = [path for path in tracked if path and any(fnmatch.fnmatch(Path(path).name, p) for p in sensitive)]
        self.assertEqual(swallowed, [])
