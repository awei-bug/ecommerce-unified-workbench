"""Run three-project integration against isolated temporary databases.

No existing service, environment file, or business database is used.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTENT = ROOT / "电商智能内容生成与商品知识平台"
SHOP = ROOT / "云购多租户电商 SaaS 平台"
CROSS = ROOT / "跨境电商运营智能辅助系统"

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="ecommerce-chain-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", HUB_HOST="127.0.0.1", HUB_PORT=str(port),
                   HUB_DB_PATH=str(Path(temp) / "hub.sqlite"), HUB_TOKENS="chain-test:local-chain-test-token",
                   DATA_HUB_URL=f"http://127.0.0.1:{port}", DATA_HUB_TOKEN="local-chain-test-token",
                   DATA_HUB_TENANT_SLUG="chain-test", CHAIN_CONTENT_ROOT=str(CONTENT),
                   CHAIN_SHOP_ROOT=str(SHOP), CHAIN_CROSS_ROOT=str(CROSS),
                   CHAIN_SHOP_DB=str(Path(temp) / "shop.sqlite"))
        hub = subprocess.Popen([sys.executable, str(CONTENT / "apps/data-hub/app.py")], env=env,
                               cwd=temp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if hub.poll() is not None:
                    raise RuntimeError("data-hub 未能启动")
                try:
                    with urllib.request.urlopen(env["DATA_HUB_URL"] + "/health", timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("data-hub 启动超时")
            jobs = [(["node", str(HERE / "three_system_content.mjs")], CONTENT),
                    ([str(SHOP / "flask_shop/.venv/Scripts/python.exe"), str(HERE / "three_system_shop.py")], SHOP / "flask_shop"),
                    ([str(CROSS / "backend/.venv/Scripts/python.exe"), str(HERE / "three_system_crossborder.py")], CROSS / "backend")]
            for command, cwd in jobs:
                subprocess.run(command, cwd=cwd, env=env, check=True)
            print("PASS 三系统商品知识链路验证完成", flush=True)
        finally:
            hub.terminate()
            try:
                hub.wait(timeout=5)
            except subprocess.TimeoutExpired:
                hub.kill()
                hub.wait()

if __name__ == "__main__":
    main()
