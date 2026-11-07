"""Audit the shop model against MySQL and a disposable PostgreSQL cluster.

The modelled subset is read from the configured local shop database into a new
temporary PostgreSQL cluster, then discarded. This is not a complete migration.
"""
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOP = ROOT / '云购多租户电商 SaaS 平台' / 'flask_shop'
PYTHON = SHOP / '.venv' / 'Scripts' / 'python.exe'

AUDIT = r'''
from config import Config
from decimal import Decimal
from sqlalchemy import Column, Double, MetaData, String, Table, Text, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from flask_shop import db, models
import os

source_url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if source_url.get_backend_name() != 'mysql' or source_url.host not in ('127.0.0.1', 'localhost'):
    raise RuntimeError('Refusing to inspect non-local MySQL source')
source = create_engine(source_url)
target = create_engine(os.environ['TASK_POSTGRES_URL'])
try:
    src = inspect(source)
    source_tables = set(src.get_table_names())
    model_tables = set(db.metadata.tables)
    print('source tables:', len(source_tables), 'model tables:', len(model_tables))
    print('source-only tables:', ', '.join(sorted(source_tables - model_tables)) or '(none)')
    print('model-only tables:', ', '.join(sorted(model_tables - source_tables)) or '(none)')
    for name in sorted(source_tables & model_tables):
        actual = {c['name'] for c in src.get_columns(name)}
        expected = set(db.metadata.tables[name].columns.keys())
        if actual != expected:
            print(name, 'source-only columns:', ','.join(sorted(actual - expected)) or '-',
                  'model-only columns:', ','.join(sorted(expected - actual)) or '-')
    # Preserve these legacy columns in the isolated experiment, not in the live
    # application's model. Their presence and defaults were inspected in MySQL.
    extra = {
        't_product': [('declare_name_cn', String(128), None), ('declare_name_en', String(128), None),
                      ('declare_value', Double, '0'), ('desc_en', Text, None),
                      ('goods_type', String(32), "'general'"), ('hs_code', String(32), None),
                      ('logistics_note', Text, None)],
        't_order': [('declare_value_total', Double, '0'), ('dest_country', String(8), "'JP'"),
                    ('estimate_tariff', Double, '0'), ('logistics_check', Text, None),
                    ('weight_total', Double, '0')],
    }
    for table, columns in extra.items():
        for name, kind, default in columns:
            if name not in {c['name'] for c in src.get_columns(table)}:
                raise RuntimeError(f'Expected legacy column missing: {table}.{name}')
            db.metadata.tables[table].append_column(Column(name, kind, server_default=text(default) if default else None))
    db.metadata.create_all(target)
    print('PASS model schema and 12 legacy fields created in disposable PostgreSQL')
    # Missing tables remain excluded: this is not a production migration.
    reflected = MetaData()
    with source.connect() as src_conn, target.begin() as dst:
        src_conn.execute(text('SET TRANSACTION READ ONLY'))
        for model in db.metadata.sorted_tables:
            if model.name not in source_tables:
                continue
            original = Table(model.name, reflected, autoload_with=src_conn)
            columns = [original.c[c.name] for c in model.columns if c.name in original.c]
            rows = src_conn.execute(select(*columns)).mappings()
            copied = 0
            while batch := rows.fetchmany(250):
                dst.execute(model.insert(), [dict(row) for row in batch])
                copied += len(batch)
            target_count = dst.execute(select(text('count(*)')).select_from(model)).scalar_one()
            if target_count != copied:
                raise RuntimeError(f'Row count mismatch: {model.name}')
        for table, columns in extra.items():
            original = reflected.tables[table]
            model = db.metadata.tables[table]
            names = ['id'] + [name for name, _, _ in columns]
            double_fields = {name for name, kind, _ in columns if kind is Double}
            def normalized(row):
                return tuple(Decimal(str(value)) if name in double_fields and value is not None else value
                             for name, value in zip(names[1:], row[1:]))
            expected = {r[0]: normalized(r) for r in src_conn.execute(select(*(original.c[n] for n in names)))}
            observed = {r[0]: normalized(r) for r in dst.execute(select(*(model.c[n] for n in names)))}
            if expected != observed:
                mismatched = [name for idx, name in enumerate(names[1:])
                              if any(expected[key][idx] != observed.get(key, (None,) * len(columns))[idx]
                                     for key in expected)]
                for idx, name in enumerate(names[1:]):
                    if name in mismatched:
                        null_to_value = sum(1 for key in expected if expected[key][idx] is None and observed[key][idx] is not None)
                        value_to_null = sum(1 for key in expected if expected[key][idx] is not None and observed[key][idx] is None)
                        print('difference type:', name, 'null_to_value=', null_to_value, 'value_to_null=', value_to_null, flush=True)
                raise RuntimeError(f'Legacy field mismatch: {table}: {mismatched}')
        print('PASS modelled rows and 12 legacy fields copied and compared')
finally:
    source.dispose()
    target.dispose()
'''


def main():
    for tool in ('initdb', 'pg_ctl'):
        if not shutil.which(tool):
            raise RuntimeError(f'Missing PostgreSQL tool: {tool}')
    with tempfile.TemporaryDirectory(prefix='shop-pg-audit-') as tmp:
        cluster = str(Path(tmp) / 'cluster')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        subprocess.run(['initdb', '-D', cluster, '-A', 'trust', '-U', 'postgres', '--no-locale', '--encoding=UTF8'],
                       check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['pg_ctl', '-D', cluster, '-l', str(Path(tmp) / 'pg.log'), '-o',
                        f'-h 127.0.0.1 -p {port}', '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
        try:
            env = os.environ.copy()
            env.pop('DATABASE_URL', None)
            env['PYTHONUTF8'] = '1'
            env['TASK_POSTGRES_URL'] = f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres'
            subprocess.run([str(PYTHON), '-c', AUDIT], cwd=SHOP, env=env, check=True)
        finally:
            subprocess.run(['pg_ctl', '-D', cluster, '-m', 'fast', '-w', 'stop'],
                           check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
