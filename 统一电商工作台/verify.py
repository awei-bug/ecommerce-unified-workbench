"""统一工作台契约验收（针对已启动的工作台实例）。

旧版本会自行拉起隔离环境并用 Playwright 断言 legacy.html 的登录表单，
该表单在统一登录改造后已移除，会话 cookie 名也改为 dsh_merchant_session，
整套断言失效。本版改为校验当前真实契约，隔离环境验收交给各项目 pytest。

前置：先在仓库根目录运行 start.py。
用法：py -3 verify.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PLAN = HERE.parent

PORTAL = "http://127.0.0.1:8090"
SHOP = "http://127.0.0.1:5000"
SHOP_WEB = "http://127.0.0.1:8080"
PORTAL_HEADERS = {"Host": "127.0.0.1:8090"}
HUB_BASE = "http://127.0.0.1:8120"
HUB_TOKEN = os.environ.get("DATA_HUB_TOKEN", "hub-local-token")


def get(url: str, headers: dict | None = None) -> tuple[int, str]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=10) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def post_json(url: str, body: dict, headers: dict | None = None) -> dict:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def check_services() -> dict:
    code, body = get(f"{PORTAL}/status", PORTAL_HEADERS)
    assert code == 200, f"服务状态接口返回 {code}"
    status = json.loads(body)
    assert status and all(value == "running" for value in status.values()), status
    return status


def check_entries() -> None:
    for label, url in (
        ("工作台门户", f"{PORTAL}/"),
        ("云购宿主", f"{SHOP_WEB}/admin/"),
        ("内容平台", "http://127.0.0.1:5174/"),
        ("跨境运营", "http://127.0.0.1:5175/"),
    ):
        code, _ = get(url)
        assert code == 200, f"{label} 返回 {code}"


def check_events_endpoint_is_closed() -> None:
    try:
        code, _ = get(f"{SHOP}/api/events")
    except HTTPError as exc:
        code = exc.code
    assert code in (401, 403), f"事件端点未鉴权，返回 {code}"


def check_log_not_exposed() -> None:
    try:
        code, _ = get(f"{PORTAL}/.runtime/shop-api.log", PORTAL_HEADERS)
    except HTTPError as exc:
        code = exc.code
    assert code == 404, f"运行时日志可通过 HTTP 访问，返回 {code}"


def check_portal_session_does_not_leak() -> None:
    try:
        code, body = get(f"{PORTAL}/portal/session", PORTAL_HEADERS)
    except HTTPError as exc:
        assert exc.code == 401, f"未登录会话接口返回 {exc.code}"
        return
    assert code in (200, 401)
    lowered = body.lower()
    assert "password" not in lowered and "access_token" not in lowered, "会话接口泄露凭证"


def check_deep_links() -> None:
    """history 回退已配置，深链与刷新必须直接返回宿主页面。"""
    for path in (
        "/admin/unified",
        "/admin/module_cross",
        "/admin/module_content",
        "/admin/module_products",
        "/admin/login",
    ):
        # 浏览器刷新会带 text/html，history 回退中间件据此判断是否回退。
        code, _ = get(f"{SHOP_WEB}{path}", {"Accept": "text/html"})
        assert code == 200, f"深链 {path} 返回 {code}"


def _status_code(
    url: str,
    headers: dict | None = None,
    method: str = "GET",
    body: dict | None = None,
) -> int:
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=8) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def check_hub_authz() -> None:
    """令牌必须配置且与租户绑定，跨租户请求一律拒绝。"""
    valid = {"X-Hub-Tenant": "demo", "X-Hub-Token": HUB_TOKEN}
    assert _status_code(f"{HUB_BASE}/hub/stats") == 401
    assert _status_code(f"{HUB_BASE}/hub/stats", {"X-Hub-Token": HUB_TOKEN}) == 401
    assert _status_code(f"{HUB_BASE}/hub/stats", valid) == 200
    assert _status_code(
        f"{HUB_BASE}/hub/stats",
        {"X-Hub-Tenant": "other", "X-Hub-Token": HUB_TOKEN},
    ) == 401
    assert _status_code(f"{HUB_BASE}/hub/stats?tenant=other", valid) == 403
    # 写路径：body 里的 tenant_slug 越过令牌租户，必须在落库前拒绝。
    assert _status_code(
        f"{HUB_BASE}/hub/ingest",
        {**valid, "Content-Type": "application/json"},
        method="POST",
        body={"tenant_slug": "other", "items": [{"external_id": "authz-probe"}]},
    ) == 403
    assert _status_code(f"{HUB_BASE}/health") == 200


def check_unified_overview() -> None:
    """统一看板：服务端聚合四路真实数据，未登录必须拒绝，登录后可追溯来源。"""
    _, raw = get(f"{SHOP}/api/unified/overview/")
    assert json.loads(raw).get("status") == 401, "统一看板接口未鉴权"

    login = post_json(f"{SHOP}/user/login/", {
        "name": "admin", "pwd": "123456", "tenant_slug": "demo",
    })
    assert login.get("status") == 200, f"云购登录失败: {login}"
    token = login["data"]["token"]

    _, raw = get(f"{SHOP}/api/unified/overview/", {"token": token})
    body = json.loads(raw)
    assert body.get("status") == 200, body
    data = body["data"]
    sources = data["sources"]
    assert set(sources) == {"shop", "cross", "content", "hub"}
    assert data["summary"]["healthy"] == 4, f"存在不可用来源: {sources}"
    for key, item in sources.items():
        assert item["status"] == "ok", f"{key} 不可用: {item.get('error')}"
        assert isinstance(item.get("latency_ms"), int), f"{key} 缺延迟记录"
    assert data["summary"]["orders"] is not None
    assert data["summary"]["alerts"] is not None
    assert data["summary"]["drafts"] is not None
    assert data["summary"]["hub_products"] is not None


def check_douyin_sync_is_protected() -> None:
    assert _status_code(
        "http://127.0.0.1:8000/api/v1/integrations/douyin/sync",
        {"Content-Type": "application/json"},
        method="POST",
    ) == 401


def main() -> int:
    status = check_services()
    check_entries()
    check_events_endpoint_is_closed()
    check_log_not_exposed()
    check_portal_session_does_not_leak()
    check_deep_links()
    check_hub_authz()
    check_douyin_sync_is_protected()
    check_unified_overview()
    print(f"PASS  服务状态 {status}")
    print("PASS  四个入口、事件端点鉴权、日志保护、会话接口、模块深链")
    print("PASS  data-hub 租户隔离、抖店同步写接口鉴权")
    print("PASS  统一看板四路聚合可用且逐路可追溯")

    print("--- 跨系统订单与库存联动 ---")
    subprocess.run(
        [sys.executable, str(PLAN / "verify_order_event_chain.py")], check=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
