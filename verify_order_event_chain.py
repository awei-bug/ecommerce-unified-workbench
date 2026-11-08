"""跨系统联动验证。

触发一笔真实的云购订单，依次断言三条链路：
1. 订单投影到跨境订单读模型；
2. 库存变动投影到跨境库存读模型，并出现在低库存预警接口；
3. 内容平台按同一批库存事件生成补货草稿，回推数据中心的独立 ops_note 字段；
4. 云购同步后 C 端商品介绍与卖点保持干净，运营提示只在后台可见。
脚本不 mock 任何一端的业务逻辑，数据全部由真实事件驱动。

前置：统一电商工作台已启动（start.py 会拉起 order-events 与 content-drafts 进程）。
用法：py -3 verify_order_event_chain.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOP_BASE = "http://127.0.0.1:5000"
CROSS_BASE = "http://127.0.0.1:8000"
CONTENT_BASE = "http://127.0.0.1:3100"
HUB_BASE = "http://127.0.0.1:8120"
HUB_TENANT = "demo"
HUB_TOKEN = os.environ.get("DATA_HUB_TOKEN", "hub-local-token")
PORTAL_STATUS = "http://127.0.0.1:8090/status"
CROSS_DB = ROOT / "跨境电商运营智能辅助系统" / "backend" / "data" / "ops_workbench.db"

# 与 start.py 注入的消费器阈值保持一致：下单数量要能把库存压到阈值以下。
DRAFT_THRESHOLD = int(os.environ.get("INVENTORY_LOW_STOCK_THRESHOLD", "25"))

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


def pick_product(token: str) -> tuple[int, int | None, int]:
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
                stock = int(active[0].get("stock") or 0)
                if stock > 0:
                    return int(pid), int(active[0]["id"]), stock
                continue
            stock = int(item.get("number", item.get("stock", 0)) or 0)
            if stock > 0:
                return int(pid), None, stock
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


def all_drafts(product_id: int, token: str) -> list[dict]:
    """该商品在内容平台的全部补货草稿（按 id 倒序，最新在前）。"""
    try:
        body = http_json(
            "GET", f"{CONTENT_BASE}/api/inventory-drafts?limit=200",
            headers={"Authorization": f"Bearer {token}"},
        )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise SystemExit(f"内容平台草稿接口返回 {exc.code}: {detail}")
    items = ((body.get("data") or {}).get("items")) or []
    return [i for i in items if str(i.get("productId")) == str(product_id)]


def content_draft(product_id: int, token: str):
    drafts = all_drafts(product_id, token)
    return drafts[0] if drafts else None


def publish_to_hub(token: str, product_id: int) -> dict:
    """商品上行：只有先登记到数据中心，库存事件才会带 external_id。"""
    body = http_json(
        "POST", f"{SHOP_BASE}/products/hub/publish/",
        {"product_id": product_id}, headers={"token": token},
    )
    if body.get("status") != 200:
        raise SystemExit(f"商品上行失败: {body}")
    return body["data"]


def sync_from_hub(token: str) -> dict:
    body = http_json(
        "POST", f"{SHOP_BASE}/products/hub/sync/",
        {"publish": True}, headers={"token": token},
    )
    if body.get("status") != 200:
        raise SystemExit(f"数据中心同步失败: {body}")
    return body["data"]


def product_copy(token: str, product_id: int) -> tuple[str, str]:
    body = http_json("GET", f"{SHOP_BASE}/products/?page=1&limit=50", headers={"token": token})
    for item in _first_item_list(body):
        if int(item.get("id") or 0) == product_id:
            return item.get("introduce") or "", item.get("sell_points") or ""
    return "", ""


def hub_record(external_id: str) -> dict:
    body = http_json(
        "GET", f"{HUB_BASE}/hub/products/{HUB_TENANT}/{external_id}",
        headers={"X-Hub-Tenant": HUB_TENANT, "X-Hub-Token": HUB_TOKEN},
    )
    return body.get("item") or {}


def shop_ops_notes(token: str) -> dict:
    body = http_json("GET", f"{SHOP_BASE}/products/ops-notes/", headers={"token": token})
    if body.get("status") != 200:
        raise SystemExit(f"运营提示接口失败: {body}")
    return body.get("data") or {}


def main() -> int:
    check_services()
    token, tenant_id = login()
    ensure_mapping(tenant_id)
    pid, sku_id, stock = pick_product(token)

    published = publish_to_hub(token, pid)
    print(f"商品已登记到数据中心 external_id={published['external_id']} "
          f"（{published['action']}）")

    # 下单前先记下已有草稿的事件水位：草稿可能在下单后 1 秒内就生成，
    # 若等前面的投影断言跑完再取基线，会把本次草稿错当成历史数据。
    baseline = max((int(d["eventId"]) for d in all_drafts(pid, token)), default=0)

    # 把库存打到阈值以下，才能触发低库存草稿；不足阈值时扣 1 件即可。
    target = max(DRAFT_THRESHOLD - 5, 0)
    quantity = max(1, stock - target) if stock > target else 1

    item = {"pid": pid, "number": quantity}
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
    print(f"云购已下单 {order_no} x{quantity}，等待跨境与内容平台消费 ...")

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

            draft_deadline = time.time() + 120
            draft = None
            while time.time() < draft_deadline:
                candidate = content_draft(pid, token)
                # 只认本次下单产生的新事件：老草稿已经推送过，不能当作本次证据。
                if candidate and int(candidate["eventId"]) > baseline \
                        and candidate.get("hubStatus") == "pushed":
                    draft = candidate
                    break
                time.sleep(3)
            assert draft is not None, (
                "内容平台未回推出新草稿。检查 .runtime/content-drafts.log、"
                "EVENTS_URL 与 INVENTORY_LOW_STOCK_THRESHOLD。"
            )
            assert draft["source"] == "yungou.stock.changed", draft["source"]
            assert draft["externalId"] == published["external_id"], draft.get("externalId")
            assert draft["stockAfter"] <= DRAFT_THRESHOLD, draft["stockAfter"]
            print("PASS  内容平台已生成补货草稿并回推数据中心")
            print(f"      event_id={draft['eventId']} kind={draft['kind']} "
                  f"stock_after={draft['stockAfter']} hub={draft['hubStatus']}")

            record = hub_record(published["external_id"])
            assert "【运营提示】" in (record.get("ops_note") or ""), (
                f"运营提示未写入独立字段：{record.get('ops_note')!r}"
            )
            assert "【运营提示】" not in (record.get("desc_zh") or ""), "数据中心商品介绍被污染"
            assert not any(
                str(p).startswith("库存提示：") for p in (record.get("selling_points") or [])
            ), "数据中心卖点被污染"
            print("PASS  运营提示进入数据中心独立字段 ops_note")
            print(f"      ops_note={(record.get('ops_note') or '')[:48]}")

            sync_from_hub(token)
            introduce, sell_points = product_copy(token, pid)
            assert "【运营提示】" not in introduce, (
                f"云购 C 端商品介绍出现运营提示：{introduce[:80]}"
            )
            assert "库存提示：" not in sell_points, "云购 C 端卖点出现运营提示"
            notes = shop_ops_notes(token)
            assert "【运营提示】" in notes.get(str(pid), ""), (
                f"商城后台未读到运营提示：{notes}"
            )
            print("PASS  云购同步后 C 端文案干净，运营提示只在后台可见")
            print(f"      商品介绍：{introduce[:52]}")
            print(f"      后台提示：{notes[str(pid)][:52]}")
            return 0
        time.sleep(2)

    raise SystemExit(
        "超时：跨境未收到该订单。检查 .runtime/order-events.log 与租户映射 is_active。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
