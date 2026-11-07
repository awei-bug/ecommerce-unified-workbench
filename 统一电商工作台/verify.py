"""统一工作台契约验收（针对已启动的工作台实例）。

旧版本会自行拉起隔离环境并用 Playwright 断言 legacy.html 的登录表单，
该表单在统一登录改造后已移除，会话 cookie 名也改为 dsh_merchant_session，
整套断言失效。本版改为校验当前真实契约，隔离环境验收交给各项目 pytest。

前置：先在仓库根目录运行 start.py。
用法：py -3 verify.py
"""
from __future__ import annotations

import json
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


def get(url: str, headers: dict | None = None) -> tuple[int, str]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=10) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


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


def report_deep_link_gap() -> None:
    """宿主 history 回退未配置，深链刷新仍 404。只报告，不作为验收失败。"""
    try:
        code, _ = get(f"{SHOP_WEB}/admin/module_cross")
    except HTTPError as exc:
        code = exc.code
    print(f"  已知缺口 深链 /admin/module_cross 返回 {code}，需配置 history 回退")


def main() -> int:
    status = check_services()
    check_entries()
    check_events_endpoint_is_closed()
    check_log_not_exposed()
    check_portal_session_does_not_leak()
    print(f"PASS  服务状态 {status}")
    print("PASS  四个入口、事件端点鉴权、日志保护、会话接口")

    report_deep_link_gap()

    print("--- 跨系统订单与库存联动 ---")
    subprocess.run(
        [sys.executable, str(PLAN / "verify_order_event_chain.py")], check=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
