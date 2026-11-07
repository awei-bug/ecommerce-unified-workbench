"""Prepare and inspect a disposable demo shop for the automatic worker test."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ['CHAIN_SHOP_ROOT']) / 'flask_shop'))
from flask_shop import create_app, db, models
from flask_shop.utils.tenant_query import tenant_context
from flask_shop.utils.token import generate_token

app = create_app('test', initialize_schema=False, start_background=False)
with app.app_context():
    if sys.argv[1] == 'prepare':
        db.create_all()
        tenant = models.Tenant(name='串联测试', slug='chain-test', status=1, data_mode='demo')
        db.session.add(tenant)
        db.session.commit()
        role = models.Role(tenant_id=tenant.id, name='超级管理员')
        db.session.add(role)
        db.session.commit()
        user = models.User(tenant_id=tenant.id, name='测试管理员', role_id=role.id)
        user.password = 'test-only-password'
        db.session.add(user)
        db.session.add(models.Product(tenant_id=tenant.id, name='冲突测试杯', price=19, number=2, state=0))
        db.session.commit()
        token = generate_token({'typ': 'merchant', 'staff_id': user.id, 'tenant_id': tenant.id})
        Path(os.environ['CHAIN_ADMIN_TOKEN_FILE']).write_text(token, encoding='utf-8')
    elif sys.argv[1] == 'publish':
        with tenant_context(disabled=True):
            tenant = models.Tenant.query.filter_by(slug='chain-test').one()
        with tenant_context(tenant_id=tenant.id):
            knowledge = models.ProductKnowledge.query.filter_by(external_id=os.environ['CHAIN_EXTERNAL_ID']).one()
            assert knowledge.knowledge()['desc_en'] == '生成正文，仅供联调'
            product = db.session.get(models.Product, knowledge.product_id)
            assert product.state == 0 and product.number == 30
            product.state = 2
            product.number = 12
            db.session.commit()
    else:
        raise ValueError('unknown action')
