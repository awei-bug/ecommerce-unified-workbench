"""Exercise the workbench using isolated SQLite databases and real local pages."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN = HERE.parent
ROOT = PLAN.parent
SHOP = ROOT / "云购多租户电商 SaaS 平台"
CROSS = ROOT / "跨境电商运营智能辅助系统"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="unified-workbench-") as folder:
        temp = Path(folder)
        env = os.environ.copy()
        for name in list(env):
            if name.startswith(("PG", "POSTGRES_", "OPENAI_", "AGNES_", "MODELSCOPE_", "DATA_HUB_")):
                env.pop(name, None)
        env.update(UNIFIED_NO_BROWSER="1", PYTHONUTF8="1", HUB_DB_PATH=str(temp / "hub.sqlite"),
                   UNIFIED_CONTENT_DATABASE_URL="", UNIFIED_SHOP_DATABASE_URL="sqlite:///" + str(temp / "shop.sqlite").replace("\\", "/"),
                   UNIFIED_CROSS_DATABASE_URL="sqlite+pysqlite:///" + str(temp / "cross.sqlite").replace("\\", "/"),
                   CHAIN_SHOP_ROOT=str(SHOP), CHAIN_SHOP_DB=str(temp / "shop.sqlite"),
                   CHAIN_ADMIN_TOKEN_FILE=str(temp / "token.txt"), FLASK_CONFIG="test",
                   TEST_DATABASE_URL="sqlite:///" + str(temp / "shop.sqlite").replace("\\", "/"),
                   ASSET_STORAGE_LOCAL_PATH=str(temp / "assets"), LOCAL_ASSETS_DIR=str(temp / "content-assets"),
                   DATABASE_AUTO_CREATE="true", MERCHANT_PRODUCT_READ_ENABLED="true", AI_LIVE_GENERATION_ENABLED="false", AGNES_ENABLED="0", AGENTS_LLM="0", AUTH_MODE="off")
        subprocess.run([str(SHOP / "flask_shop/.venv/Scripts/python.exe"), str(PLAN / "three_system_auto_shop.py"), "prepare"], cwd=SHOP / "flask_shop", env=env, check=True)
        process = subprocess.Popen([sys.executable, str(HERE / "start.py")], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(900):
                if process.poll() is not None:
                    raise RuntimeError("启动器提前退出：" + process.stdout.read().decode("utf-8", errors="replace"))
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8090/status", timeout=5) as response:
                        status = json.load(response)
                    if "failed" in status.values():
                        raise RuntimeError(f"服务退出：{status}，请查看 .runtime 日志")
                    if all(value == "running" for value in status.values()):
                        break
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("服务未在规定时间内启动")
            try:
                urllib.request.urlopen("http://127.0.0.1:8090/.runtime/shop-api.log")
                raise AssertionError("日志不应通过工作台 HTTP 访问")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
            session_check = (
                "import os; from pathlib import Path; "
                "from app.services.merchant_session import validate_merchant_session, validate_merchant_staff; "
                "token=Path(os.environ['CHAIN_ADMIN_TOKEN_FILE']).read_text(encoding='utf-8'); "
                "session=validate_merchant_session('http://127.0.0.1:5000', token); "
                "assert session.tenant_slug == 'chain-test' and session.is_super; "
                "validate_merchant_staff('http://127.0.0.1:5000', token, session.tenant_id, session.staff_id); "
                "print('PASS 云购实际会话通过跨境校验')"
            )
            subprocess.run(
                [str(CROSS / "backend/.venv/Scripts/python.exe"), "-c", session_check],
                cwd=CROSS / "backend", env=env, check=True,
            )
            with closing(sqlite3.connect(temp / "shop.sqlite")) as shop_db:
                staff_id, tenant_id = shop_db.execute(
                    "SELECT id, tenant_id FROM t_users WHERE name=?", ("测试管理员",)
                ).fetchone()
            with closing(sqlite3.connect(temp / "cross.sqlite")) as cross_db:
                shop_id, org_id, title = cross_db.execute(
                    "SELECT s.id, s.organization_id, p.title FROM shops s JOIN products p "
                    "ON s.id=p.shop_id AND s.organization_id=p.organization_id "
                    "WHERE s.data_source='seeded' LIMIT 1"
                ).fetchone()
                cross_db.execute(
                    "INSERT INTO merchant_organization_mappings (tenant_id, organization_id, is_active) VALUES (?, ?, 1)",
                    (tenant_id, org_id),
                )
                cross_db.execute(
                    "INSERT INTO merchant_shop_grants (tenant_id, staff_id, shop_id, is_active, can_read_products) VALUES (?, ?, ?, 1, 1)",
                    (tenant_id, staff_id, shop_id),
                )
                cross_db.commit()
            env.update(PORTAL_TEST_SHOP=shop_id, PORTAL_TEST_TITLE=title)
            browser_test = r"""
const { chromium, expect } = require('@playwright/test');
(async () => {
 const browser = await chromium.launch({ channel: 'chrome', headless: true });
 try {
  for (const viewport of [{width:1440,height:900},{width:390,height:844}]) {
   const page = await browser.newPage({ viewport });
   await page.goto('http://127.0.0.1:8090/legacy.html');
   await expect(page.locator('#status')).toHaveText('服务端口已就绪', {timeout: 20000});
   const blocked = await page.request.post('http://127.0.0.1:8090/portal/login', {headers:{Origin:'https://untrusted.example'},data:{}});
   if (blocked.status() !== 403) throw new Error('跨站登录请求未被拒绝');
   for (const [name, id] of [['内容中心','content'],['商城中心','shop'],['跨境运营','cross']]) {
    await page.getByRole('button',{name}).click();
    const frame = page.locator(`iframe#${id}`);
    await expect(frame).toBeVisible();
    await expect(page.getByRole('heading',{name})).toBeVisible();
    const target = {content:'商品批量内容',shop:'登录后台',cross:'登录运营中枢'}[id];
    await expect(page.frameLocator(`iframe#${id}`).getByText(target, {exact:true}).first()).toBeVisible({timeout:30000});
   }
   await page.getByRole('button',{name:'商品协作'}).click();
   await page.getByLabel('团队标识').fill('chain-test');
   await page.getByLabel('商家账号').fill('测试管理员');
   await page.getByLabel('密码').fill('test-only-password');
   await page.getByRole('button',{name:'登录并继续'}).click();
   await expect(page.getByText('请选择已授权的演示店铺。')).toBeVisible();
   const shopSelect = page.getByLabel('授权演示店铺');
   await expect(shopSelect.locator('option')).toHaveCount(2);
   await shopSelect.selectOption(process.env.PORTAL_TEST_SHOP);
   await page.getByRole('button',{name:'查看商品',exact:true}).click();
   await expect(page.getByText(process.env.PORTAL_TEST_TITLE,{exact:true})).toBeVisible();
   const cookies = await page.context().cookies();
   const cookie = cookies.find(c => c.name === 'workbench_session');
   if (!cookie || !cookie.httpOnly) throw new Error('会话 cookie 缺少 HttpOnly');
   const sessionData = await (await page.request.get('http://127.0.0.1:8090/portal/session')).text();
   if (sessionData.includes('token') || sessionData.includes('test-only-password')) throw new Error('会话接口泄露凭证');
   await page.screenshot({path: process.env.SCREENSHOT_DIR + '/products-' + viewport.width + '.png'});
   await page.getByRole('button',{name:'退出商家账号'}).click();
   await expect(page.getByRole('button',{name:'登录并继续'})).toBeVisible();
   const sessionResponse = await page.request.get('http://127.0.0.1:8090/portal/session');
   if (sessionResponse.status() !== 401) throw new Error('退出后会话仍可用');
   if (viewport.width === 1440) {
    await page.getByRole('button',{name:'商城中心'}).click();
    const shop = page.frameLocator('iframe#shop');
    await shop.getByPlaceholder('demo').fill('chain-test');
    await shop.getByPlaceholder('admin').fill('测试管理员');
    await shop.getByPlaceholder('密码').fill('test-only-password');
    await shop.getByRole('button',{name:'进入工作台'}).click();
    await expect(shop.getByText('登录后台', {exact:true})).not.toBeVisible({timeout:15000});
    await page.getByRole('button',{name:'跨境运营'}).click();
    const cross = page.frameLocator('iframe#cross');
    await cross.getByRole('button',{name:'进入工作台'}).click();
    await expect(cross.getByText('登录运营中枢', {exact:true})).not.toBeVisible({timeout:15000});
   }
   await page.screenshot({path: process.env.SCREENSHOT_DIR + '/workbench-' + viewport.width + '.png'});
   await page.close();
  }
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
"""
            env["SCREENSHOT_DIR"] = str(HERE / ".runtime")
            subprocess.run(["node", "-e", browser_test], cwd=CROSS / "frontend", env=env, check=True)
            print("PASS 工作台：三模块启动、桌面和手机切换、页面加载与日志访问保护")
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.terminate()
                process.wait(timeout=15)
                if os.name == "nt":
                    time.sleep(2)


if __name__ == "__main__":
    main()
