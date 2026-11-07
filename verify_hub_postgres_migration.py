"""Exercise a real SQLite-to-PostgreSQL hub copy in an isolated cluster."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB = ROOT / '电商智能内容生成与商品知识平台' / 'apps' / 'data-hub'
SOURCE = Path(__file__).resolve().parent / 'database-backups' / '20261030-131154-offline-ae817519' / 'hub.sqlite'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(port, path, data=None):
    body = None if data is None else json.dumps(data).encode('utf-8')
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body,
                                 headers={'Content-Type': 'application/json',
                                          'X-Hub-Tenant': 'demo', 'X-Hub-Token': 'test-token'})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def main():
    with tempfile.TemporaryDirectory(prefix='hub-migration-') as temp:
        cluster = str(Path(temp) / 'cluster')
        pg_port, hub_port = free_port(), free_port()
        subprocess.run(['initdb', '-D', cluster, '-A', 'trust', '-U', 'postgres',
                        '--no-locale', '--encoding=UTF8'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['pg_ctl', '-D', cluster, '-l', str(Path(temp) / 'pg.log'), '-o',
                        f'-h 127.0.0.1 -p {pg_port}', '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
        server = None
        try:
            env = os.environ.copy()
            env.update(HUB_DATABASE_URL=f'postgresql://postgres@127.0.0.1:{pg_port}/postgres',
                       HUB_HOST='127.0.0.1', HUB_PORT=str(hub_port), HUB_TOKENS='demo:test-token', PYTHONUTF8='1')
            command = [sys.executable, str(HUB / 'migrate_sqlite_to_postgres.py'), '--source', str(SOURCE)]
            subprocess.run(command, env=env, check=True)
            second = subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if second.returncode == 0:
                raise RuntimeError('Repeated import must refuse a nonempty target')
            server = subprocess.Popen([sys.executable, str(HUB / 'app.py')], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                try:
                    if request(hub_port, '/health')['db'] == 'postgresql':
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError('PostgreSQL hub did not start')
            item = {'external_id': 'migration-verification-only', 'title': '验证商品', 'price': 8.23, 'stock': 2}
            body = {'tenant_slug': 'demo', 'items': [item]}
            assert request(hub_port, '/hub/ingest', body)['created'] == 1
            assert request(hub_port, '/hub/ingest', body)['unchanged'] == 1
            mixed = request(hub_port, '/hub/ingest', {'tenant_slug': 'demo', 'items': [
                {'external_id': 'invalid-stock-test', 'title': '错误库存', 'stock': 2 ** 40},
                {'external_id': 'valid-after-error-test', 'title': '正常库存', 'stock': 1},
            ]})
            assert mixed['error_count'] == 1 and mixed['created'] == 1
            result = request(hub_port, '/hub/products/demo/migration-verification-only/ack',
                             {'product_id': 123, 'status': 'published'})
            assert result['product_id'] == 123
            fetched = request(hub_port, '/hub/products/demo/migration-verification-only')['item']
            assert fetched['status'] == 'published' and fetched['price'] == 8.23
            assert request(hub_port, '/hub/search', {'tenant_slug': 'demo', 'query': '验证商品'})['count'] > 0
            print('PASS PostgreSQL hub migration, duplicate protection, ingest, ack and search')
        finally:
            if server:
                server.terminate()
                server.wait(timeout=10)
            subprocess.run(['pg_ctl', '-D', cluster, '-m', 'fast', '-w', 'stop'],
                           check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
