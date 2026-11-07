"""Serve the already-seeded temporary cloud-shop test database."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["CHAIN_SHOP_ROOT"]) / "flask_shop"))
from config import TestingConfig
from flask_shop import create_app

TestingConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.environ["CHAIN_SHOP_DB"].replace("\\", "/")
app = create_app("test")
app.config.update(DATA_HUB_URL=os.environ["DATA_HUB_URL"], DATA_HUB_TOKEN=os.environ["DATA_HUB_TOKEN"], DATA_HUB_TENANT_SLUG="chain-test")
app.run(host="127.0.0.1", port=int(os.environ["CHAIN_SHOP_PORT"]), use_reloader=False)
