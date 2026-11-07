"""Start the existing local services and a single browser entry point.

This is a development launcher, not a production service manager.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from portal_api import PortalAPI
import content_database

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONTENT = ROOT / "电商智能内容生成与商品知识平台"
SHOP = ROOT / "云购多租户电商 SaaS 平台"
CROSS = ROOT / "跨境电商运营智能辅助系统"
LOGS = HERE / ".runtime"
PIDFILE = LOGS / "pids.json"
processes = {}
logs = []


def reap_stale():
    """Kill children a previous hard-killed launcher left behind.

    A Ctrl+C run exits through `finally` and cleans up itself; only a force-kill
    skips that path, which is what this repairs on the next start.
    """
    try:
        stale = json.loads(PIDFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for name, pid in stale.items():
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                os.kill(int(pid), signal.SIGTERM)
            print(f"已清理上次残留进程 {name}（pid={pid}）", flush=True)
        except (OSError, ValueError):
            pass
    PIDFILE.unlink(missing_ok=True)


def port_available(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def port_listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 Node.js，请先安装并配置 PATH。")
    shop_python = SHOP / "flask_shop/.venv/Scripts/python.exe"
    cross_python = CROSS / "backend/.venv/Scripts/python.exe"
    required = [shop_python, cross_python, CONTENT / "node_modules/vite/bin/vite.js",
                SHOP / "vue_shop/node_modules/@vue/cli-service/bin/vue-cli-service.js",
                CROSS / "frontend/node_modules/vite/bin/vite.js"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("缺少项目运行依赖：\n" + "\n".join(missing))
    reap_stale()
    ports = [8120, 8110, 3100, 5174, 5000, 8080, 8000, 5175, 8090]
    busy = [str(port) for port in ports if not port_available(port)]
    if busy:
        raise RuntimeError("端口已被占用：" + ", ".join(busy) + "。请先停止占用这些端口的服务。")
    LOGS.mkdir(exist_ok=True)
    common = os.environ.copy()
    common.update(PYTHONUTF8="1", DATA_HUB_URL="http://127.0.0.1:8120",
                  DATA_HUB_TENANT_SLUG="demo", DATA_HUB_TOKEN=os.environ.get("DATA_HUB_TOKEN", "hub-local-token"))
    hub_env = common | {"HUB_HOST": "127.0.0.1", "HUB_PORT": "8120", "HUB_TOKENS": "demo:" + common["DATA_HUB_TOKEN"]}
    identity_url = os.environ.get("MERCHANT_IDENTITY_URL", "http://127.0.0.1:5000")
    # Content and cross both verify the merchant session against the shop service.
    # Without this the content API answers 503 IDENTITY_NOT_CONFIGURED and the UI
    # looks logged out even though the shared cookie is present.
    content_env = common | {"API_HOST": "127.0.0.1", "API_PORT": "3100",
                            "AI_ORCHESTRATOR_BASE_URL": "http://127.0.0.1:8110",
                            "MERCHANT_IDENTITY_URL": identity_url}
    orchestrator_env = common | {"API_SERVER_BASE_URL": "http://127.0.0.1:3100"}
    content_web_env = common | {"VITE_API_BASE_URL": "http://127.0.0.1:3100"}
    shop_env = common | {"FLASK_PORT": "5000"}
    shop_web_env = common | {"VUE_APP_API_PROXY_TARGET": "http://127.0.0.1:5000", "VUE_APP_PUBLIC_PATH": "/admin/"}
    # Product read is off by default in the cross service; the local workbench
    # demo needs it on for the 商品协作 page to list shops.
    # 演示数据已按要求清空，关闭启动时自动播种，否则空库会被重新填满。
    cross_env = common | {"MERCHANT_IDENTITY_URL": identity_url,
                          "MERCHANT_PRODUCT_READ_ENABLED": "true",
                          "SEED_DEMO_DATA": "false"}
    # The cross preview database is now migrated to alembic head, so the default
    # run uses it directly instead of a throwaway copy.
    cross_env.setdefault("DATABASE_URL", "sqlite+pysqlite:///" + str(CROSS / "backend/data/ops_workbench.db").replace("\\", "/"))
    cross_web_env = common | {"VITE_API_PROXY_TARGET": "http://127.0.0.1:8000"}
    # Separate overrides allow isolated verification and avoid sharing one database
    # URL between services with different storage requirements.
    for key, env in (("UNIFIED_CONTENT_DATABASE_URL", content_env),
                     ("UNIFIED_SHOP_DATABASE_URL", shop_env),
                     ("UNIFIED_CROSS_DATABASE_URL", cross_env)):
        if key in os.environ:
            env["DATABASE_URL"] = os.environ[key]
    specs = [
        ("hub", [sys.executable, "apps/data-hub/app.py"], CONTENT, hub_env),
        ("orchestrator", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8110"], CONTENT / "apps/ai-orchestrator", orchestrator_env),
        ("content-api", [node, "apps/api-server/src/server.js"], CONTENT, content_env),
        ("content-web", [node, str(CONTENT / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5174", "--strictPort"], CONTENT / "apps/studio-web", content_web_env),
        ("shop-api", [str(shop_python), "run_dev_api.py"], SHOP / "flask_shop", shop_env),
        ("shop-web", [node, str(SHOP / "vue_shop/node_modules/@vue/cli-service/bin/vue-cli-service.js"), "serve", "--host", "127.0.0.1", "--port", "8080"], SHOP / "vue_shop", shop_web_env),
        ("cross-api", [str(cross_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], CROSS / "backend", cross_env),
        ("cross-web", [node, str(CROSS / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5175", "--strictPort"], CROSS / "frontend", cross_web_env),
    ]

    portal = PortalAPI()

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") != "127.0.0.1:8090":
                self.send_error(403)
                return
            if portal.get(self):
                return
            if self.path == "/status":
                groups = {"content": (("content-api", 3100), ("content-web", 5174), ("orchestrator", 8110), ("hub", 8120)),
                          "shop": (("shop-api", 5000), ("shop-web", 8080)), "cross": (("cross-api", 8000), ("cross-web", 5175))}
                status = {}
                for key, entries in groups.items():
                    if any(name not in processes or processes[name].poll() is not None for name, _ in entries):
                        status[key] = "failed"
                    elif all(port_listening(port) for _, port in entries):
                        status[key] = "running"
                    else:
                        status[key] = "starting"
                payload = json.dumps(status).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            # Compare on the path only: a cache-busting query string must not 404.
            route = urlsplit(self.path).path
            if route not in ("/", "/index.html", "/legacy.html", "/style.css", "/app.js"):
                self.send_error(404)
                return
            return super().do_GET()

        def end_headers(self):
            # These are hand-edited dev assets; stale copies cost more than a
            # re-fetch, and an old page silently hides fixes.
            if urlsplit(self.path).path in ("/", "/index.html", "/legacy.html"):
                self.send_header("Cache-Control", "no-store, must-revalidate")
            elif urlsplit(self.path).path in ("/style.css", "/app.js"):
                self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def do_POST(self):
            if self.headers.get("Host") != "127.0.0.1:8090":
                self.send_error(403)
                return
            if not portal.post(self):
                self.send_error(404)

        def do_HEAD(self):
            self.send_error(405)

        def log_message(self, *args):
            pass

    server = None
    managed_content_database = False
    try:
        if "UNIFIED_CONTENT_DATABASE_URL" not in os.environ:
            database_env = content_database.start()
            managed_content_database = True
            content_env.pop("DATABASE_URL", None)
            for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
                content_env[key] = database_env[key]
            hub_env["HUB_DATABASE_URL"] = (
                f"postgresql://{quote(database_env['PGUSER'], safe='')}:"
                f"{quote(database_env['PGPASSWORD'], safe='')}@127.0.0.1:"
                f"{database_env['PGPORT']}/{quote(database_env['PGDATABASE'], safe='')}"
            )
            print("内容数据库已就绪。", flush=True)
        server = ThreadingHTTPServer(("127.0.0.1", 8090), partial(Handler, directory=str(HERE)))
        spec_map = {name: (command, cwd, env) for name, command, cwd, env in specs}
        for name, command, cwd, env in specs:
            log = open(LOGS / f"{name}.log", "w", encoding="utf-8")
            logs.append(log)
            processes[name] = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            print(f"已启动 {name}，日志：{log.name}", flush=True)
        PIDFILE.write_text(json.dumps({n: p.pid for n, p in processes.items()}), encoding="utf-8")
        print("\n商家工作台：http://127.0.0.1:8080/admin/\n保持此窗口打开；按 Ctrl+C 停止本次启动的服务。", flush=True)
        if os.environ.get("UNIFIED_NO_BROWSER") != "1":
            threading.Timer(2, lambda: webbrowser.open("http://127.0.0.1:8080/admin/")).start()
        def supervise():
            """Restart a module that dies after a clean start.

            `shop-web` has been observed to exit silently right after reporting
            "Compiled successfully", which leaves the whole module dead with no
            error in the log. Restarting it keeps the workbench usable; the
            restart is capped so a genuinely broken module does not spin.
            """
            restarts = {}
            while not stopping.is_set():
                stopping.wait(5)
                if stopping.is_set():
                    break
                for name, (command, cwd, env) in spec_map.items():
                    process = processes.get(name)
                    if process is None or process.poll() is None:
                        continue
                    count = restarts.get(name, 0)
                    if count >= 3:
                        continue
                    restarts[name] = count + 1
                    print(f"{name} 已退出（code={process.returncode}），第 {count + 1} 次重启", flush=True)
                    log = open(LOGS / f"{name}.log", "a", encoding="utf-8")
                    logs.append(log)
                    processes[name] = subprocess.Popen(
                        command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )

        stopping = threading.Event()
        watchdog = threading.Thread(target=supervise, daemon=True)
        watchdog.start()
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            stopping.set()
    finally:
        PIDFILE.unlink(missing_ok=True)
        if server:
            server.server_close()
        for process in reversed(list(processes.values())):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for log in logs:
            log.close()
        if managed_content_database:
            content_database.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n工作台已停止。")
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        sys.exit(1)
