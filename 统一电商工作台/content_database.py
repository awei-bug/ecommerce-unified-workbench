"""Local development PostgreSQL for the content service; never touches other clusters."""
import json
import os
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / '.runtime'
CLUSTER = RUNTIME / 'content-postgres'
CONFIG = RUNTIME / 'content-postgres.json'
PORT = 55432
CONTENT = ROOT.parent.parent / '电商智能内容生成与商品知识平台'
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def run(args, **kwargs):
    return subprocess.run(args, check=True, creationflags=FLAGS, **kwargs)


def available():
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', PORT))
            return True
        except OSError:
            return False


def start():
    initdb, pg_ctl, node = (shutil.which(x) for x in ('initdb', 'pg_ctl', 'node'))
    if not all((initdb, pg_ctl, node)):
        raise RuntimeError('内容数据库需要 PostgreSQL 工具和 Node.js')
    RUNTIME.mkdir(exist_ok=True)
    if not available():
        raise RuntimeError(f'内容数据库端口 {PORT} 已被占用，请先核查占用进程')
    if not CLUSTER.exists() and not CONFIG.exists():
        password = secrets.token_hex(32)
        pwfile = RUNTIME / 'content-postgres-init.tmp'
        pwfile.write_text(password, encoding='ascii')
        try:
            run([initdb, '-D', str(CLUSTER), '-U', 'content_local', '--auth=scram-sha-256',
                 '--pwfile', str(pwfile), '--encoding=UTF8', '--no-locale'],
                stdout=subprocess.DEVNULL)
            CONFIG.write_text(json.dumps({'password': password}), encoding='utf-8')
        finally:
            pwfile.unlink(missing_ok=True)
    if not CLUSTER.is_dir() or not CONFIG.is_file():
        raise RuntimeError('内容数据库配置不完整，请检查 .runtime，未自动重建以保护数据')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    env = os.environ.copy()
    env.update(PGHOST='127.0.0.1', PGPORT=str(PORT), PGUSER='content_local',
               PGPASSWORD=config['password'], PGDATABASE='postgres')
    env.pop('DATABASE_URL', None)
    run([pg_ctl, '-D', str(CLUSTER), '-l', str(RUNTIME / 'content-postgres.log'),
         '-o', f'-h 127.0.0.1 -p {PORT}', '-w', 'start'], stdout=subprocess.DEVNULL)
    try:
        run([node, 'scripts/init-core-schema.js'], cwd=CONTENT, env=env, stdout=subprocess.DEVNULL)
    except Exception:
        stop()
        raise
    return env


def stop():
    pg_ctl = shutil.which('pg_ctl')
    if pg_ctl and CLUSTER.is_dir():
        run([pg_ctl, '-D', str(CLUSTER), '-m', 'fast', '-w', 'stop'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
