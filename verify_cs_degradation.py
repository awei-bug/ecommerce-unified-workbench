"""客服主链路对 data-hub 故障的降级验证（阶段 2 验收标准 3）。

用法::

    python verify_cs_degradation.py [--slug demo]

证明：data-hub 不可用时，客服主回复**仍然成功**，只是拿不到商品知识卡。
这是 data-hub-integration.md 第八节声明的边界，必须持续成立——
否则一次中间件故障会让整个商城客服瘫掉。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CS_ROOT = REPO_ROOT / "电商 B 端智能客服 SaaS 系统" / "flask_shop"

# 指向一个保证没有服务监听的端口，模拟 data-hub 宕机。
DEAD_HUB_URL = "http://127.0.0.1:9"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="demo")
    args = parser.parse_args()

    if str(CS_ROOT) not in sys.path:
        sys.path.insert(0, str(CS_ROOT))

    # 先确认端口真的没人监听，否则这个测试没有意义。
    import socket
    from urllib.parse import urlsplit

    parsed = urlsplit(DEAD_HUB_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        reachable = sock.connect_ex((host, port)) == 0
    if reachable:
        check("degraded.precondition", False, f"{DEAD_HUB_URL} 竟然可达，测试无效")
        print(f"\n{sum(1 for _, ok, _ in CHECKS if ok)}/{len(CHECKS)} passed")
        return 1

    saved_env: dict[str, str | None] = {}
    overrides = {
        "FLASK_CONFIG": "develop",
        "SMS_DEV_MODE": "1",
        "ALLOW_MOCK_PAY": "1",
        "DATA_HUB_URL": DEAD_HUB_URL,
        "DATA_HUB_TENANT_SLUG": args.slug,
    }
    for key, value in overrides.items():
        saved_env[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        from flask_shop import create_app, models  # noqa: PLC0415
        from flask_shop.utils.tenant_query import tenant_context  # noqa: PLC0415

        app = create_app("develop")
        with app.app_context():
            with tenant_context(disabled=True):
                tenant = models.Tenant.query.filter_by(slug=args.slug).first()
            if tenant is None:
                check("degraded.tenant.found", False, f"tenant not found: {args.slug}")
                print(f"\n{sum(1 for _, ok, _ in CHECKS if ok)}/{len(CHECKS)} passed")
                return 1
            check("degraded.tenant.found", True, f"id={tenant.id}")

            from flask_shop.agents import knowledge as knowledge_lib  # noqa: PLC0415
            from flask_shop.agents.orchestrator import AgentOrchestrator  # noqa: PLC0415

            # 1. 知识检索降级：不可用时应为空，而不是抛异常或卡住
            try:
                cards = knowledge_lib.load_knowledge(tenant.id, "榨汁杯", limit=3)
                check(
                    "degraded.knowledge.no.crash",
                    isinstance(cards, list),
                    f"cards={len(cards)}",
                )
            except Exception as exc:  # noqa: BLE001
                check("degraded.knowledge.no.crash", False, f"{type(exc).__name__}: {exc}")

            # 2. 关键断言：客服主回复仍然成功
            try:
                out = AgentOrchestrator().run_chat(
                    "有没有便携榨汁杯",
                    tenant_id=tenant.id,
                    tenant_slug=args.slug,
                    channel="mall_chat",
                )
                data = (out or {}).get("data") or {}
                msg = data.get("reply") or ""
                check(
                    "degraded.chat.still.replies",
                    len(msg) > 0,
                    msg[:60].replace("\n", " / "),
                )
            except Exception as exc:  # noqa: BLE001
                check("degraded.chat.still.replies", False, f"{type(exc).__name__}: {exc}")

            # 3. 同步失败必须可预期：抛 HubError 且不留下半同步状态。
            #    这不是缺陷——sync_from_hub 的契约就是「要么成功要么抛错」，
            #    由 Web 层捕获转 500（product/views.py L799-801）。
            #    真正要防的是：抛错前已经写了部分数据。
            try:
                from flask_shop.utils import hub_sync  # noqa: PLC0415
                from flask_shop.utils.hub_client import HubError  # noqa: PLC0415

                before = models.Product.query.count()
                try:
                    hub_sync.sync_from_hub(tenant, status="", limit=5, publish=False, ack=False)
                    check("degraded.sync.raises.huberror", False, "未抛错，与契约不符")
                except HubError as exc:
                    check("degraded.sync.raises.huberror", True, f"HubError: {str(exc)[:60]}")
                after = models.Product.query.count()
                check(
                    "degraded.sync.no.partial.write",
                    before == after,
                    f"products before={before} after={after}",
                )
            except Exception as exc:  # noqa: BLE001
                check("degraded.sync.no.crash", False, f"{type(exc).__name__}: {exc}")
    finally:
        for key, old in saved_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
