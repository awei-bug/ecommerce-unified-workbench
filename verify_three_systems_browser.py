"""Browser acceptance with temporary databases and local-only services."""
import os
from contextlib import closing
import socket
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTENT = ROOT / "电商智能内容生成与商品知识平台"
CROSS = ROOT / "跨境电商运营智能辅助系统"
SHOP = ROOT / "云购多租户电商 SaaS 平台"


def free_port():
    for _ in range(100):
        candidate = 20000 + secrets.randbelow(20000)
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", candidate))
                return candidate
        except OSError:
            continue
    raise RuntimeError("没有可用的本地测试端口")


def wait(url, process):
    for _ in range(600):
        if process.poll() is not None:
            raise RuntimeError(f"服务启动失败，退出码 {process.returncode}: {url}")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"服务启动超时: {url}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    processes = []
    with tempfile.TemporaryDirectory(prefix="ecommerce-browser-") as temp:
        ports = set()
        while len(ports) < 10:
            ports.add(free_port())
        hub_port, api_port, web_port, shop_port, mall_port, content_api_port, content_web_port, mock_port, proxy_port, admin_port = ports
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", HUB_HOST="127.0.0.1", HUB_PORT=str(hub_port),
                   HUB_DB_PATH=str(Path(temp) / "hub.sqlite"), HUB_TOKENS="chain-test:local-chain-test-token",
                   DATA_HUB_URL=f"http://127.0.0.1:{proxy_port}", DATA_HUB_TOKEN="local-chain-test-token",
                   CHAIN_REAL_HUB=f"http://127.0.0.1:{hub_port}", CHAIN_HUB_PROXY_PORT=str(proxy_port),
                   DATA_HUB_TENANT_SLUG="chain-test", CHAIN_CONTENT_ROOT=str(CONTENT),
                   DATABASE_URL="sqlite+pysqlite:///" + str(Path(temp) / "cross.sqlite").replace("\\", "/"),
                   DATABASE_AUTO_CREATE="true", CELERY_TASK_ALWAYS_EAGER="true",
                   ASSET_STORAGE_LOCAL_PATH=str(Path(temp) / "assets"),
                   CHAIN_SHOP_ROOT=str(SHOP), CHAIN_SHOP_DB=str(Path(temp) / "shop.sqlite"),
                   CHAIN_SHOP_PORT=str(shop_port), CHAIN_MALL_URL=f"http://127.0.0.1:{mall_port}",
                   CHAIN_ADMIN_TOKEN_FILE=str(Path(temp) / "admin-token.txt"), CHAIN_ADMIN_URL=f"http://127.0.0.1:{admin_port}",
                   AGENTS_LLM="0", AGNES_ENABLED="0", AGENTS_INTENT_ALWAYS_LLM="0",
                   AGENTS_CS_ALWAYS_LLM="0",
                   VITE_API_PROXY_TARGET=f"http://127.0.0.1:{api_port}",
                   CHAIN_WEB_URL=f"http://127.0.0.1:{web_port}")
        def start(command, cwd, name):
            log = open(Path(temp) / (name + ".log"), "w", encoding="utf-8")
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append((process, log, name))
            return process
        try:
            hub = start([sys.executable, str(CONTENT / "apps/data-hub/app.py")], temp, "hub")
            wait(env["CHAIN_REAL_HUB"] + "/health", hub)
            proxy = start([sys.executable, str(HERE / "hub_fault_proxy.py")], temp, "hub-proxy")
            wait(env["DATA_HUB_URL"] + "/health", proxy)
            cross_database = env["DATABASE_URL"]
            env.update(CHAIN_ID_FILE=str(Path(temp) / "product-id.txt"), CHAIN_PRODUCT_TITLE="内容入口测试杯", CHAIN_CONTENT_API=f"http://127.0.0.1:{content_api_port}", CHAIN_MOCK_PORT=str(mock_port),
                       CHAIN_CONTENT_WEB=f"http://127.0.0.1:{content_web_port}", API_PORT=str(content_api_port),
                       API_HOST="127.0.0.1", AI_ORCHESTRATOR_BASE_URL=f"http://127.0.0.1:{mock_port}",
                       VITE_API_BASE_URL=f"http://127.0.0.1:{content_api_port}", AUTH_MODE="off",
                       DATABASE_URL="", PGHOST="", POSTGRES_HOST="", INTERNAL_CALLBACK_TOKEN="",
                       LOCAL_ASSETS_DIR=str(Path(temp) / "content-assets"))
            mock = start([sys.executable, str(HERE / "content_mock_orchestrator.py")], temp, "mock-orchestrator")
            wait(f"http://127.0.0.1:{mock_port}/health", mock)
            content_api = start(["node", "apps/api-server/src/server.js"], CONTENT, "content-api")
            wait(env["CHAIN_CONTENT_API"] + "/api/health", content_api)
            content_web = start(["node", str(CONTENT / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(content_web_port), "--strictPort"], CONTENT / "apps/studio-web", "content-web")
            wait(env["CHAIN_CONTENT_WEB"], content_web)
            subprocess.run(["node", "e2e/content-chain.browser.mjs"], env=env, cwd=CROSS / "frontend", check=True)
            env["CHAIN_EXTERNAL_ID"] = (Path(temp) / "product-id.txt").read_text(encoding="utf-8").strip()
            env["DATABASE_URL"] = cross_database
            env.pop("VITE_API_BASE_URL", None)
            subprocess.run(["node", str(HERE / "three_system_pending.mjs")], env=env, cwd=CONTENT, check=True)
            shop_python = str(SHOP / "flask_shop/.venv/Scripts/python.exe")
            env.update(FLASK_CONFIG="test", TEST_DATABASE_URL="sqlite:///" + env["CHAIN_SHOP_DB"].replace("\\", "/"),
                       DATA_HUB_AUTO_SYNC_ENABLED="1", DATA_HUB_AUTO_SYNC_INTERVAL_SECONDS="30")
            subprocess.run([shop_python, str(HERE / "three_system_auto_shop.py"), "prepare"], env=env, cwd=SHOP / "flask_shop", check=True)
            worker = start([shop_python, "scripts/jobs/auto_sync_from_hub.py"], SHOP / "flask_shop", "shop-worker")
            def await_knowledge_status(expected):
                for _ in range(450):
                    if worker.poll() is not None:
                        raise RuntimeError("自动同步进程退出")
                    with closing(sqlite3.connect(env["CHAIN_SHOP_DB"])) as conn:
                        row = conn.execute('SELECT status FROM t_product_knowledge WHERE external_id=?', (env['CHAIN_EXTERNAL_ID'],)).fetchone()
                    if row and row[0] == expected:
                        return
                    time.sleep(0.1)
                raise RuntimeError(f"自动同步未在规定时间达到状态 {expected}")
            await_knowledge_status('draft')
            duplicate = subprocess.run([shop_python, "scripts/jobs/auto_sync_from_hub.py"], cwd=SHOP / "flask_shop", env=env, capture_output=True, timeout=15)
            if duplicate.returncode != 1:
                raise RuntimeError('重复同步进程未被阻止')
            subprocess.run([shop_python, str(HERE / "three_system_auto_shop.py"), "publish"], env=env, cwd=SHOP / "flask_shop", check=True)
            await_knowledge_status('published')
            print("PASS 云购独立自动同步：草稿导入、人工上架后更新知识并保留库存", flush=True)
            shop_api = start([shop_python, str(HERE / "three_system_shop_server.py")], SHOP / "flask_shop", "shop-api")
            wait(f"http://127.0.0.1:{shop_port}/api/c/ping", shop_api)
            env.update(VUE_APP_API_PROXY_TARGET=f"http://127.0.0.1:{shop_port}", VUE_APP_PUBLIC_PATH="/")
            admin = start(["node", str(SHOP / "vue_shop/node_modules/@vue/cli-service/bin/vue-cli-service.js"), "serve", "--host", "127.0.0.1", "--port", str(admin_port)], SHOP / "vue_shop", "admin")
            wait(env["CHAIN_ADMIN_URL"], admin)
            subprocess.run(["node", "e2e/shop-admin-hub.browser.mjs"], env=env, cwd=CROSS / "frontend", check=True)
            mall_env = env.copy()
            mall_env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{shop_port}"
            old_target = env["VITE_API_PROXY_TARGET"]
            env["VITE_API_PROXY_TARGET"] = mall_env["VITE_API_PROXY_TARGET"]
            mall = start(["node", str(SHOP / "mall/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(mall_port), "--strictPort"], SHOP / "mall", "mall")
            env["VITE_API_PROXY_TARGET"] = old_target
            wait(env["CHAIN_MALL_URL"], mall)
            subprocess.run(["node", "e2e/shop-chain.browser.mjs"], env=env, cwd=CROSS / "frontend", check=True)
            api = start([str(CROSS / "backend/.venv/Scripts/python.exe"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)], CROSS / "backend", "api")
            wait(f"http://127.0.0.1:{api_port}/health", api)
            vite = start(["node", str(CROSS / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"], CROSS / "frontend", "web")
            wait(env["CHAIN_WEB_URL"], vite)
            subprocess.run(["node", "e2e/hub-binding.browser.mjs"], env=env, cwd=CROSS / "frontend", check=True)

        except Exception:
            for _, log, name in processes:
                log.flush()
                print(f"{name}:\n{(Path(temp) / (name + '.log')).read_text(encoding='utf-8', errors='replace')[-3000:]}")
            raise
        finally:
            for process, log, _ in reversed(processes):
                if os.name == "nt" and process.poll() is None:
                    # A Windows venv Python launcher can own a child Python process.
                    # Stop only this test's process tree before removing its database.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                log.close()
            if os.name == "nt":
                # Windows releases descendant process file handles asynchronously.
                time.sleep(2)


if __name__ == "__main__":
    main()
