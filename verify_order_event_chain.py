"""跨系统订单联动验证。

触发一笔真实的云购订单，等待后台事件消费器把它投影到跨境订单读模型，
并断言投影行的来源、平台与幂等键。脚本不 mock 任何一端的业务逻辑。

前置：统一电商工作台已启动（start.py 会拉起 order-events 消费进程）。
用法：py -3 verify_order_event_chain.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOP_BASE = "http://127.0.0.1:5000"
CROSS_BASE = "http://127.0.0.1:8000"
PORTAL_STATUS = "http://127.0.0.1:8090/status"
CROSS_DB = ROOT / "跨境电商运营智能辅助系统" / "backend" / "data" / "ops_workbench.db"

LOGIN = {"name": "admin", "pwd": "123456", "tenant_slug": "demo"}


def http_json(method: str, url: str, body=None, headers=None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    merged = {"Content-Type": "application/json", **(headers or {})}
    request = Request(url, data=payload, method=method, headers=merged)
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _first_item_list(node):
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            return node
        for child in node:
            found = _first_item_list(child)
            if found:
                return found
    elif isinstance(node, dict):
        for value in node.values():
            found = _first_item_list(value)
            if found:
                return found
    return []


def check_services() -> None:
    try:
        status = http_json("GET", PORTAL_STATUS, headers={"Host": "127.0.0.1:8090"})
    except (URLError, OSError, ValueError) as exc:
        raise SystemExit(f"工作台未就绪，请先启动 start.py。原因: {exc}")
    groups = {key: value for key, value in status.items() if isinstance(value, str)}
    bad = [key for key, value in groups.items() if value != "running"]
    if bad:
        raise SystemExit(f"以下模块未就绪: {', '.join(bad)}")


def login() -> tuple[str, int]:
    body = http_json("POST", f"{SHOP_BASE}/user/login/", LOGIN)
    if body.get("status") != 200:
        raise SystemExit(f"云购登录失败: {body}")
    data = body["data"]
    return data["token"], int(data["tenant"]["id"])


def pick_product(token: str) -> tuple[int, int | None]:
    headers = {"token": token}
    for path in ("/products/?page=1&limit=50", "/products/"):
        try:
            body = http_json("GET", f"{SHOP_BASE}{path}", headers=headers)
        except (URLError, OSError, ValueError):
            continue
        for item in _first_item_list(body):
            pid = item.get("id")
            if not pid:
                continue
            skus = item.get("skus") or []
            active = [s for s in skus if isinstance(s, dict) and s.get("status", 1) == 1]
            if active:
                return int(pid), int(active[0]["id"])
            stock = item.get("number", item.get("stock", 0)) or 0
            if float(stock) > 0:
                return int(pid), None
    raise SystemExit("未找到可售商品，请先在云购后台录入一个有库存的商品")


def ensure_mapping(tenant_id: int) -> str:
    if not CROSS_DB.exists():
        raise SystemExit(f"跨境运行库不存在: {CROSS_DB}")
    with sqlite3.connect(CROSS_DB) as conn:
        row = conn.execute(
            "SELECT organization_id, is_active FROM merchant_organization_mappings "
            "WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if row is None:
            organization_id = f"org-yungou-{tenant_id}"
            conn.execute(
                "INSERT INTO merchant_organization_mappings "
                "(tenant_id, organization_id, is_active) VALUES (?, ?, 1)",
                (tenant_id, organization_id),
            )
            print(f"已激活租户映射 tenant={tenant_id} -> {organization_id}")
        else:
            organization_id = row[0]
            if not row[1]:
                conn.execute(
                    "UPDATE merchant_organization_mappings SET is_active = 1 "
                    "WHERE tenant_id = ?", (tenant_id,)
                )
                print(f"已激活既有租户映射 tenant={tenant_id} -> {organization_id}")
        conn.commit()
    return organization_id


def projection_count(order_no: str):
    if not CROSS_DB.exists():
        return None
    with sqlite3.connect(CROSS_DB) as conn:
        row = conn.execute(
            "SELECT event_id, source, is_demo, payload FROM order_projections "
            "WHERE order_no = ?", (order_no,)
        ).fetchone()
    if row is None:
        return None
    return {
        "event_id": row[0],
        "source": row[1],
        "is_demo": row[2],
        "payload": json.loads(row[3]),
    }


def inventory_projection(product_id: int):
    if not CROSS_DB.exists():
        return None
    with sqlite3.connect(CROSS_DB) as conn:
        row = conn.execute(
            "SELECT event_id, stock_after, source, item_title "
            "FROM inventory_projections WHERE product_id = ? "
            "ORDER BY event_id DESC LIMIT 1", (str(product_id),)
        ).fetchone()
    if row is None:
        return None
    return {
        "event_id": row[0],
        "stock_after": row[1],
        "source": row[2],
        "item_title": row[3],
    }


def check_inventory_alert(token: str, product_id: int):
    body = http_json(
        "GET", f"{CROSS_BASE}/api/v1/inventory/alerts?threshold=10000",
        headers={"Authorization": f"Bearer {token}"},
    )
    return next(
        (item for item in body.get("items", [])
         if item["product_id"] == str(product_id)),
        None,
    )


def main() -> int:
    check_services()
    token, tenant_id = login()
    ensure_mapping(tenant_id)
    pid, sku_id = pick_product(token)

    item = {"pid": pid, "number": 1}
    if sku_id:
        item["sku_id"] = sku_id
    order = http_json(
        "POST", f"{SHOP_BASE}/orders/",
        {"items": [item], "address": "跨系统联动验证地址"},
        headers={"token": token},
    )
    if order.get("status") != 200:
        raise SystemExit(f"云购下单失败: {order}")
    order_no = order["data"]["order_no"]
    print(f"云购已下单 {order_no}，等待跨境读模型投影 ...")

    deadline = time.time() + 45
    while time.time() < deadline:
        row = projection_count(order_no)
        if row is not None:
            assert row["source"] == "event", f"来源异常: {row['source']}"
            assert row["is_demo"] == 0, "真实事件被错误标记为演示数据"
            assert row["event_id"] is not None, "幂等键缺失"
            assert row["payload"]["platform"] == "yungou", "平台归属异常"
            print("PASS  云购订单已出现在跨境读模型")
            print(f"      event_id={row['event_id']} shop={row['payload']['shop_id']} "
                  f"amount={row['payload']['amount']}")

            stock_deadline = time.time() + 45
            inventory = None
            while time.time() < stock_deadline:
                inventory = inventory_projection(pid)
                if inventory is not None:
                    break
                time.sleep(2)
            assert inventory is not None, "云购库存变动未投影到跨境读模型"
            assert inventory["source"] == "event", "库存投影来源异常"
            print("PASS  云购库存变动已出现在跨境读模型")
            print(f"      event_id={inventory['event_id']} "
                  f"stock_after={inventory['stock_after']} item={inventory['item_title']}")

            alert = check_inventory_alert(token, pid)
            assert alert is not None, "低库存预警接口未返回该商品"
            assert alert["source"] == "event", "预警数据不是来自真实事件"
            print(f"PASS  低库存预警接口读到真实事件数据 "
                  f"stock_after={alert['stock_after']}")
            return 0
        time.sleep(2)

    raise SystemExit(
        "超时：跨境未收到该订单。检查 .runtime/order-events.log 与租户映射 is_active。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
