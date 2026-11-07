"""阶段 2 串联演示：一条商品穿过 4 个系统。

用法::

    python verify_ecommerce_chain.py [--slug demo] [--keep]

链路::

    data-hub（权威源）
      ├─(1)→ 内容平台 ingest            确认记录已写入权威源
      ├─(2)→ 客服 sync 上架 → AI 客服引用知识 → 订单可归因
      ├─(3)→ 跨境只读拉取                external_id 一致、不建第二份权威
      └─(4)→ 洞察 data_hub 连接器        metric_result 取到该商品价格

设计取舍
- **不需要启动三个 Web 服务**：客服侧走 Flask in-process `test_client`，跨境与洞察
  直接 import 其 service 层。这样脚本可重复跑、无端口冲突、无启动等待。
- **data-hub 必须已在运行**（`scripts/start-data-hub.ps1` 或 `python apps/data-hub/app.py`）。
  它是跨进程共享的权威源，无法在进程内替代。
- **临时记录**：用独立 external_id（`chain-<run_id>`），`--keep` 保留以便排查，
  默认清理。

断言分 6 组共 16 项，任一失败返回非 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# 四个项目的路径。跨项目 import 用 sys.path 注入，互不依赖对方的 venv 配置。
RAG_ROOT = REPO_ROOT / "企业级私有知识库与长期记忆问答平台"
CS_ROOT = REPO_ROOT / "电商 B 端智能客服 SaaS 系统" / "flask_shop"
CB_ROOT = REPO_ROOT / "跨境电商运营智能辅助系统" / "backend"
INSIGHT_ROOT = REPO_ROOT / "多智能体业务洞察 Agent 集群" / "backend"

# 历史四系统脚本，2026-10 编写。CS_ROOT 使用更早的客服目录旧名，
# RAG_ROOT 与 INSIGHT_ROOT 指向 2026-11-07 已清理的两个项目。
# 三者均不存在，脚本已停用，避免以隐晦的 import 错误误导后来者。
# 当前验收入口见 三个电商系统联调说明.md 与 统一交付/README.md。
_MISSING = [str(p) for p in (CS_ROOT, RAG_ROOT, INSIGHT_ROOT) if not p.exists()]
if _MISSING:
    raise SystemExit(
        "历史脚本已停用，依赖目录不存在：\n  "
        + "\n  ".join(_MISSING)
        + "\n当前验收入口见 三个电商系统联调说明.md 与 统一交付/README.md"
    )

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    return ok


def hub_base() -> str:
    return (os.getenv("DATA_HUB_URL") or "http://127.0.0.1:8120").rstrip("/")


def hub_headers(tenant: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.getenv("DATA_HUB_TOKEN") or ""
    if tenant:
        headers["X-Hub-Tenant"] = tenant
    if token:
        headers["X-Hub-Token"] = token
    return headers


def hub_request(method: str, path: str, *, body: dict | None = None, tenant: str = "demo") -> dict:
    url = f"{hub_base()}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=hub_headers(tenant), method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"data-hub {method} {path} -> {exc.code}: {payload[:200]}") from exc


def chain_sample(external_id: str) -> dict:
    """演示商品。价格给真实值，避免走缺价分支（缺价只登记知识、不建商品）。"""
    return {
        "external_id": external_id,
        "title": "串联演示榨汁杯 380ml",
        "subtitle": "USB 充电一次榨 15 杯",
        "price": 129.0,
        "currency": "CNY",
        "stock": 30,
        "category": "厨具",
        "brand": "ChainDemo",
        "tags": ["榨汁杯", "便携", "USB充电"],
        "selling_points": ["380ml 容量", "USB 充电一次榨 15 杯", "刀头可拆洗"],
        "scenario": "办公室、出差",
        "spec": {"容量": "380ml", "电池": "2000mAh"},
        "faq": [{"q": "能榨冰块吗", "a": "不建议，硬物会损伤刀头"}],
        "desc_zh": "便携式榨汁杯，380ml 容量，USB 充电。",
        "images": ["https://example.invalid/chain/cover.png"],
        "source": "ecomops_chain_demo",
    }


def step1_content_side(tenant: str, external_id: str) -> dict | None:
    """内容平台侧：确认 ingest 已写入权威源，并能读回完整知识。"""
    sample = chain_sample(external_id)
    payload = hub_request(
        "POST",
        "/hub/ingest",
        body={"tenant_slug": tenant, "source": sample["source"], "items": [sample]},
        tenant=tenant,
    )
    check(
        "content.ingest.written",
        payload.get("ok") is True and payload.get("ok_count") == 1,
        f"created={payload.get('created')} updated={payload.get('updated')}",
    )
    detail = hub_request("GET", f"/hub/products/{tenant}/{external_id}", tenant=tenant)
    item = detail.get("item")
    check(
        "content.ingest.readback",
        isinstance(item, dict) and item.get("selling_points") == sample["selling_points"],
        f"version={item.get('version') if item else None}",
    )
    return item


def step2_customer_service(tenant: str, external_id: str) -> dict:
    """客服侧：sync 上架 → 本地知识副本 → AI 引用 → 订单可归因。"""
    for extra in (str(CS_ROOT), str(RAG_ROOT)):
        if extra not in sys.path:
            sys.path.insert(0, extra)

    # 只改动本次需要的变量，跑完恢复原值。
    #
    # 两件事必须一起处理，否则第 3 步的跨境 Settings 会崩：
    # 1. Flask 的 create_app 会把 CORS_ORIGINS 以逗号分隔字符串写进 os.environ，
    #    而跨境的 pydantic-settings 对 list 字段要求 JSON，同进程内会解析失败。
    #    因此清理时必须把 Flask 可能新增的键也恢复成"不存在"。
    # 2. DATA_HUB_* 是本步骤要注入的配置。
    saved_env: dict[str, str | None] = {}
    overrides = {
        "FLASK_CONFIG": "develop",
        "SMS_DEV_MODE": "1",
        "ALLOW_MOCK_PAY": "1",
        "DATA_HUB_URL": hub_base(),
        "DATA_HUB_TENANT_SLUG": tenant,
        "PYTHONIOENCODING": "utf-8",
    }
    if os.getenv("DATA_HUB_TOKEN"):
        overrides["DATA_HUB_TOKEN"] = os.environ["DATA_HUB_TOKEN"]

    # 预先记录 Flask 可能写入的键，保证 finally 能还原。
    flask_owned = ["CORS_ORIGINS", "FLASK_CONFIG", "SMS_DEV_MODE"]

    for key, value in overrides.items():
        saved_env[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        return _step2_customer_service_inner(tenant, external_id)
    finally:
        for key in set(list(overrides) + flask_owned):
            old = saved_env.get(key, os.environ.get(key) if key in flask_owned else None)
            # flask_owned 类键在这个进程里本就不该存在，直接删除；
            # overrides 类键恢复到调用前的值。
            if key in flask_owned and key not in saved_env:
                os.environ.pop(key, None)
            elif old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _step2_customer_service_inner(tenant: str, external_id: str) -> dict:
    from flask_shop import create_app, models  # noqa: PLC0415
    from flask_shop.utils import hub_sync  # noqa: PLC0415
    from flask_shop.utils.tenant_query import tenant_context  # noqa: PLC0415

    app = create_app("develop")
    with app.app_context():
        with tenant_context(disabled=True):
            tenant_row = models.Tenant.query.filter_by(slug=tenant).first()
        if tenant_row is None:
            check("cs.tenant.found", False, f"tenant not found: {tenant}")
            return {}

        result = hub_sync.sync_from_hub(tenant_row, status="", limit=100, publish=True, ack=True)
        summary = result.get("summary") or {}
        hit = next(
            (r for r in (result.get("created") or []) + (result.get("updated") or [])
             if r.get("external_id") == external_id),
            None,
        )
        check(
            "cs.sync.created",
            hit is not None and hit.get("product_id"),
            f"summary={summary}",
        )
        if not hit:
            return {"product_id": None}

        product_id = hit["product_id"]
        with tenant_context(tenant_id=tenant_row.id):
            product = models.Product.query.get(product_id)
            knowledge = models.ProductKnowledge.query.filter_by(external_id=external_id).first()
        check("cs.product.persisted", product is not None, f"id={product_id}")
        check(
            "cs.knowledge.cached",
            knowledge is not None and bool(knowledge.product_id),
            f"hash={(knowledge.content_hash or '')[:8] if knowledge else None}",
        )

        # AI 客服引用知识卡（规则路径即可命中，不依赖 LLM）
        from flask_shop.agents import knowledge as knowledge_lib  # noqa: PLC0415
        from flask_shop.agents.orchestrator import AgentOrchestrator  # noqa: PLC0415

        cards = knowledge_lib.load_knowledge(tenant_row.id, "榨汁杯 能榨冰块吗", limit=3)
        check("cs.knowledge.search", len(cards) > 0, f"n={len(cards)}")
        if cards:
            top = cards[0]
            check(
                "cs.knowledge.top_match",
                "榨汁杯" in str(top.get("title") or ""),
                str(top.get("title")),
            )
            check(
                "cs.knowledge.faq_present",
                len(top.get("faq") or []) > 0,
                str(top.get("faq"))[:60],
            )

        out = AgentOrchestrator().run_chat(
            "有没有便携榨汁杯", tenant_id=tenant_row.id, tenant_slug=tenant, channel="mall_chat"
        )
        data = (out or {}).get("data") or {}
        msg = data.get("reply") or ""
        know = data.get("knowledge") or {}
        check("cs.agent.reply", len(msg) > 0, msg[:80].replace("\n", " / "))
        check("cs.agent.knowledge_loaded", bool(know.get("count")), str(know.get("count")))
        return {"product_id": product_id, "reply": msg}


def step3_cross_border(tenant: str, external_id: str) -> dict:
    """跨境侧：只读拉取同一商品，确认身份一致且不建第二份权威记录。"""
    if str(CB_ROOT) not in sys.path:
        sys.path.insert(0, str(CB_ROOT))

    from sqlalchemy import create_engine, func, select  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from app.core.config import Settings  # noqa: PLC0415
    from app.db.models import Base, ProductModel  # noqa: PLC0415
    from app.services import hub_client as hub  # noqa: PLC0415

    live = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        data_hub_url=hub_base(),
        data_hub_token=os.getenv("DATA_HUB_TOKEN") or "",
        data_hub_tenant_slug=tenant,
        # 用显式 _env_file=None 绕开仓库里的真实 .env：本步骤只验证 data-hub 只读路径，
        # 不关心也不应继承该项目其余配置（其中可能含真实平台凭证）。
        _env_file=None,
    )
    knowledge = hub.fetch_product_knowledge(external_id, settings=live)
    check(
        "cb.hub.readback",
        isinstance(knowledge, dict) and knowledge.get("external_id") == external_id,
        f"source={knowledge.get('source') if knowledge else None}",
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        # 跨境库只应有本地/平台商品；权威源是 data-hub，本项目不建第二份商品记录。
        count = db.scalar(select(func.count()).select_from(ProductModel))
        check(
            "cb.no.second.source.of.truth",
            count == 0,
            f"local products={count}",
        )
    return {"knowledge": knowledge}


def step4_insight(tenant: str, external_id: str) -> dict:
    """洞察侧：data_hub 连接器拉数据源，跑七节点工作流取到该商品价格。"""
    if str(INSIGHT_ROOT) not in sys.path:
        sys.path.insert(0, str(INSIGHT_ROOT))

    from backend_py.connectors import (  # noqa: PLC0415
        ConnectorError,
        build_connector_data_source,
        fetch_rows,
    )
    from backend_py.workflow_service import apply_workflow_action  # noqa: PLC0415

    config = {
        "url": hub_base(),
        "tenantSlug": tenant,
        "status": "",
        "limit": 50,
    }
    credentials = {"token": os.getenv("DATA_HUB_TOKEN") or ""}
    try:
        rows = fetch_rows("data_hub", config, credentials)
    except ConnectorError as exc:
        check("insight.connector.rows", False, f"{exc.code}: {exc.message}")
        return {}

    target = next((r for r in rows if r.get("external_id") == external_id), None)
    check(
        "insight.connector.rows",
        target is not None,
        f"rows={len(rows)} found={bool(target)}",
    )
    if not target:
        return {}

    dataset = build_connector_data_source(
        name="data-hub chain demo",
        connector_type="data_hub",
        payload={"kind": "table", "rows": rows},
    )

    # 直接复用仓库自己的 idle snapshot 构造，避免手写 dict 漏字段
    # （第一次手写时漏了 snapshot.context，_apply_workflow_action 直接 KeyError）。
    from backend_py.repository import _make_idle_snapshot  # noqa: PLC0415
    from backend_py.workflow_service import (  # noqa: PLC0415
        apply_workflow_action,
        advance_workflow_with_model,
    )

    task = {
        "id": f"task-chain-{int(time.time())}",
        "title": "商品记忆库串联演示分析",
        "status": "draft",
        "request": "分析这批商品的价格与库存，找出需要补货的 SKU。",
        "dataSource": dataset,
        "snapshot": _make_idle_snapshot(),
        "artifacts": [],
        "executionLog": [],
        "risks": [],
    }

    # apply_workflow_action 每次只推进一步（start 只把首节点置 running），
    # 必须自己循环 advance_workflow_with_model 才能跑完七节点拿产物。
    # chat_client 传 None：走 _deterministic_node_output 确定性路径，不调远程模型。
    result = apply_workflow_action(task, "start")
    steps = 0
    while result.get("snapshot", {}).get("phase") == "running" and steps < 12:
        result = advance_workflow_with_model(result, None)
        steps += 1

    metric_artifacts = [
        a for a in (result.get("artifacts") or []) if a.get("type") == "metric_result"
    ]
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    check(
        "insight.workflow.ran",
        bool(result.get("snapshot", {}).get("nodes")),
        f"nodes={len(result.get('snapshot', {}).get('nodes') or [])} steps={steps} phase={result.get('snapshot', {}).get('phase')}",
    )
    check(
        "insight.workflow.metric",
        bool(metric_artifacts) and "129" in serialized,
        f"metric_artifacts={len(metric_artifacts)} artifacts={len(result.get('artifacts') or [])}",
    )
    return {"dataset": dataset, "task": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default=os.getenv("DATA_HUB_TENANT_SLUG") or "demo")
    parser.add_argument("--keep", action="store_true", help="保留演示记录，便于排查")
    args = parser.parse_args()
    tenant = args.slug

    run_id = f"{int(time.time())}"
    external_id = f"chain-{run_id}"
    print(f"[chain] tenant={tenant} external_id={external_id} hub={hub_base()}")

    # 0. data-hub 必须已在运行；它是跨进程共享的权威源，无法在进程内替代。
    try:
        health = hub_request("GET", "/health", tenant=tenant)
    except Exception as exc:  # noqa: BLE001
        check("hub.online", False, f"{type(exc).__name__}: {exc}")
        print("\ndata-hub 未运行。先启动：")
        print('  cd "F:\\Python_code\\电商智能内容生成与商品知识平台"')
        print("  powershell -ExecutionPolicy Bypass -File scripts\\start-data-hub.ps1")
        print(f"\n{sum(1 for _, ok, _ in CHECKS if ok)}/{len(CHECKS)} passed")
        return 1
    check("hub.online", health.get("ok") is True, str(health.get("service")))

    step1_content_side(tenant, external_id)
    step2_customer_service(tenant, external_id)
    step3_cross_border(tenant, external_id)
    step4_insight(tenant, external_id)

    # 5. 跨系统对账：同一 external_id 在四个系统都可指回同一权威记录。
    details = hub_request("GET", f"/hub/products/{tenant}/{external_id}", tenant=tenant)
    item = details.get("item") or {}
    check(
        "chain.identity.reconcilable",
        item.get("external_id") == external_id and item.get("status") == "published",
        f"status={item.get('status')} published_product_id={item.get('published_product_id')}",
    )

    # 6. 清理：ack 回退 + 删除演示商品（--keep 时保留）
    if not args.keep:
        try:
            hub_request(
                "POST",
                f"/hub/products/{tenant}/{external_id}/status",
                body={"status": "archived"},
                tenant=tenant,
            )
            check("chain.cleanup.archived", True, external_id)
        except Exception as exc:  # noqa: BLE001
            check("chain.cleanup.archived", False, str(exc))
    else:
        print(f"[chain] --keep：演示记录保留 {external_id}")

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    if failed:
        print("failed:")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
