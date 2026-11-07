"""Run status persistence tests on a fresh temporary PostgreSQL cluster."""
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / '电商智能内容生成与商品知识平台'
initdb = shutil.which('initdb')
pg_ctl = shutil.which('pg_ctl')
if not initdb or not pg_ctl:
    raise SystemExit('需要本机 PostgreSQL initdb 和 pg_ctl')
with tempfile.TemporaryDirectory(prefix='hub-pg-') as temp:
    cluster = str(Path(temp) / 'cluster')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    subprocess.run([initdb, '-D', cluster, '-A', 'trust', '-U', 'postgres', '--no-locale'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([pg_ctl, '-D', cluster, '-l', str(Path(temp) / 'postgres.log'), '-o', f'-h 127.0.0.1 -p {port}', '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
    try:
        env = os.environ.copy()
        env['CHAIN_TEST_POSTGRES_URL'] = f'postgresql://postgres@127.0.0.1:{port}/postgres'
        subprocess.run(['node', 'scripts/test-hub-sync-postgres.mjs'], cwd=ROOT, env=env, check=True)
    finally:
        subprocess.run([pg_ctl, '-D', cluster, '-m', 'fast', '-w', 'stop'], check=True, stdout=subprocess.DEVNULL)
