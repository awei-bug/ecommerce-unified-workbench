"""Exercise the cross-border project's real read-only hub client."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["CHAIN_CROSS_ROOT"]) / "backend"))
from app.core.config import Settings
from app.services.hub_client import fetch_product_knowledge

settings = Settings(_env_file=None, database_url="sqlite+pysqlite:///:memory:", data_hub_url=os.environ["DATA_HUB_URL"], data_hub_token=os.environ["DATA_HUB_TOKEN"], data_hub_tenant_slug="chain-test")
item = fetch_product_knowledge("cc-chain-1", settings=settings)
assert item and item["title"] == "联调便携水杯", item
assert item["faq"][0]["a"] == "380ml" and item["status"] == "published", item

# Run the real authenticated product binding routes against the same live hub.
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import app.core.config as config_module
import app.services.hub_client as hub_module
from app.api.routes.products import router
from app.db.models import Base, ProductModel, ShopModel
from app.db.session import get_db

config_module.get_settings = lambda: settings
hub_module.get_settings = lambda: settings
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
sessions = sessionmaker(bind=engine, expire_on_commit=False)
with sessions.begin() as db:
    db.add(ShopModel(id="shop", organization_id="org-demo-001", platform="douyin", platform_label="抖店", name="演示店", data_source="seeded"))
    db.add(ProductModel(id="product", organization_id="org-demo-001", shop_id="shop", sku="SKU-1", external_id="dy-1", title="本地水杯", platform="douyin", platform_label="抖店", shop_name="演示店", status="online", risk="normal", price=129, cost=50, stock=12, sales_7d=0, revenue_7d=0, conversion_rate=0, updated_at=datetime.now(timezone.utc)))
app = FastAPI()
app.include_router(router)
def override_db():
    with sessions() as db:
        yield db
app.dependency_overrides[get_db] = override_db
headers = {"Authorization": "Bearer demo-access-token"}
with TestClient(app) as client:
    preview = client.get("/products/product/hub-preview", params={"external_id": "cc-chain-1"}, headers=headers)
    assert preview.status_code == 200 and preview.json()["title"] == "联调便携水杯", preview.text
    response = client.put("/products/product/hub-binding", json={"hub_product_id": "cc-chain-1", "expected_hub_product_id": None}, headers=headers)
    assert response.status_code == 200, response.text
    detail = client.get("/products/product", headers=headers)
    assert detail.status_code == 200 and detail.json()["knowledge"]["faq"][0]["a"] == "380ml", detail.text
    with sessions() as db:
        assert db.get(ProductModel, "product").external_id == "dy-1"
    response = client.put("/products/product/hub-binding", json={"hub_product_id": None, "expected_hub_product_id": "cc-chain-1"}, headers=headers)
    assert response.status_code == 200, response.text
    assert client.get("/products/product", headers=headers).json()["knowledge"]["available"] is False
engine.dispose()
print("PASS 跨境系统预览、绑定、读取知识、解除关联，保留平台商品 ID")
