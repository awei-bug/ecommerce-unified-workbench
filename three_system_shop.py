"""Exercise cloud shop sync in a disposable SQLite database."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["CHAIN_SHOP_ROOT"]) / "flask_shop"))
from config import TestingConfig
from flask_shop import create_app, db, models
from flask_shop.agents import knowledge
from flask_shop.utils import hub_sync
from flask_shop.utils.tenant_query import tenant_context

TestingConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.environ["CHAIN_SHOP_DB"].replace("\\", "/")
app = create_app("test")
app.config.update(DATA_HUB_URL=os.environ["DATA_HUB_URL"], DATA_HUB_TOKEN=os.environ["DATA_HUB_TOKEN"], DATA_HUB_TENANT_SLUG="chain-test")
external_id = os.environ.get("CHAIN_EXTERNAL_ID", "cc-chain-1")
product_title = os.environ.get("CHAIN_PRODUCT_TITLE", "联调便携水杯")
with app.app_context():
    db.create_all()
    with tenant_context(disabled=True):
        tenant = models.Tenant(name="串联测试", slug="chain-test", status=1)
        db.session.add(tenant)
        db.session.commit()
    result = hub_sync.sync_from_hub(tenant, status="", publish=True, ack=True)
    assert result["summary"]["created"] == 1, result
    item = result["created"][0]
    assert item["external_id"] == external_id
    with tenant_context(tenant_id=tenant.id):
        product = db.session.get(models.Product, item["product_id"])
        assert product.name == product_title and product.number == 30
    cards = knowledge.load_knowledge(tenant.id, product_title, limit=3)
    assert cards and cards[0]["title"] == product_title, cards
    # Repeat sync after a local stock change; hub must not overwrite trading stock.
    product.number = 12
    db.session.commit()
    again = hub_sync.sync_from_hub(tenant, status="", publish=True, ack=True)
    assert again["summary"]["updated"] == 1, again
    assert db.session.get(models.Product, item["product_id"]).number == 12
    print("PASS 云购同步商品、检索知识、重复同步保留本地库存")
