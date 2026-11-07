# 统一交付基线

建立日期 2026-11-07。本页替代此前 15 份过程报告，作为唯一活跃文档。历史报告全部移入 `archive/`，内容保留，状态冻结，不再更新。

## 一、主线

唯一主线是三系统合并体。由云购后台作宿主，跨境与内容平台接入同一会话与数据链路。载体为 `统一电商工作台/` 与三个产品仓库。

2026-11-07 已彻底清理两个未接入产品项目目录，`电商经营数据洞察 Agent 集群` 与 `电商商品知识库与智能问答平台`。两者含各自 `.git`，删除后本地历史一并消失。三系统对它们零运行时依赖。

整合计划仓库内的历史阶段文档、旧验证脚本同样冻结，仅作历史追溯材料，不作为当前验收依据。这些文档里对已清理项目的引用、路径和 2026-12 之类的未来日期标记，均为当时快照，现已作废。为保持历史原貌，未逐行改写。

## 二、六仓库基线（2026-11-07 固化）

| 仓库 | 提交 | 说明 |
|---|---|---|
| 云购多租户电商 SaaS 平台 | `58020c3` | 统一登录与宿主外壳改造 |
| 电商智能内容生成与商品知识平台 | `f93c95b` | 统一会话与 PostgreSQL 接入 |
| 跨境电商运营智能辅助系统 | `cac9693` | 云购会话接入与身份对齐 |
| 电商经营数据洞察 Agent 集群 | `dbc2131` | 清理前基线，目录已删除，提交随之失效 |
| 电商商品知识库与智能问答平台 | 无改动 | 工作区原本干净，目录已删除 |
| 电商垂类整合计划 | 见本仓库 log | 报告归档与本基线 |

固化前六仓库合计 346 个未提交文件，10-23 至 11-02 两周成果全部处于游离状态。现在每个仓库都有可回退提交。

## 三、归档报告索引

全部位于 `archive/`。

| 主题 | 文件 |
|---|---|
| 规划与验收 | `本轮统一交付规划.md`、`本轮验收结论.md`、`全功能检查报告.md`、`遗留补齐报告.md` |
| 身份与界面 | `统一登录完成报告.md`、`D4执行报告.md`、`阻塞解除报告.md`、`中性色统一报告.md`、`现状观察与改进点.md` |
| 数据与清理 | `重复能力清单与删除建议.md`、`R01执行报告.md`、`商城PG迁移可行性报告.md`、`商城PG迁移执行报告.md`、`测试数据清空报告.md` |
| 求职材料 | `个人优势-简历版.md` |

## 四、主线完成标准

三条可跑的验收，通过后才算这一个能用。

1. 干净目录从零启动，八个服务全部就绪，业务级健康探测全绿。
2. 一条业务链路端到端跑通，商品资料到内容生成到上架到订单到经营指标，每步可追溯。
3. 下列六条必修全绿，且验收脚本覆盖写接口与越权用例。

## 五、六条必修（按此顺序）

| 序 | 事项 | 状态 | 证据位置 |
|---|---|---|---|
| 1 | 放行 PG 方言校验，迁移脚本遇未建模列硬失败 | 已完成。未建模表与含数据的未建模列均拒绝迁移并返回非零退出码 | `flask_shop/config.py:149`、`flask_shop/scripts/migrate_to_postgres.py` |
| 2 | data-hub 令牌 fail-closed 且绑定租户，抖店同步写接口补鉴权 | 已完成 | `apps/data-hub/app.py`、`backend/app/api/routes/integrations.py` |
| 3 | 已有商品售价仅在新建时初始化 | 已完成 | `flask_shop/flask_shop/utils/hub_sync.py`、`tests/test_hub_sync_price.py` |
| 4 | 单一令牌源、回跳契约、统一登出广播、上游故障区分 401 与 503 | 已完成 | `vue_shop/src/views/ModuleHostView.vue`、`frontend/src/app/unified-login.ts` |
| 5 | 重写验收脚本，替换已失效的 `/portal/login` 与旧 cookie 断言 | 已完成。改为校验当前工作台契约，并调用联动验证 | `统一电商工作台/verify.py` |
| 6 | 演示订单从代码常量改为数据库投影表，测试不再依赖固定编号 | 已完成 | `backend/app/services/order_repository.py`、`backend/tests/test_api.py` |

## 六、跨系统订单联动（2026-11-07 已落地）

主线从「门户拼装」推进到业务事实联动，第一条链路已打通。

| 环节 | 实现 | 位置 |
|---|---|---|
| 生产方 | 订单、明细与库存扣减共用 commit，同事务写 `t_domain_events`；库存失败时回滚不残留 | `flask_shop/flask_shop/order/views.py`、`models.py` 的 `DomainEvent` |
| 契约 | `GET /api/events?after=&limit=&tenant_id=`，自增 id 作水位，平台令牌 fail-closed，跨租户查询显式绕过自动隔离 | `flask_shop/flask_shop/events/` |
| 消费方 | `order_projections` 读模型与 `event_watermarks` 水位表，event_id 唯一约束幂等，未激活租户映射丢弃 | `backend/app/services/order_event_consumer.py`、`alembic/versions/20261107_0024_order_projection.py` |
| 页面 | 跨境订单列表、详情、动作任务改读投影表；演示订单改为显式种子，仅在 `seed_demo_data` 开启时写入 | `backend/app/services/order_repository.py` |
| 第二条链路 | 云购库存扣减与回滚同事务写 `stock.changed`；跨境投影到 `inventory_projections`，`GET /api/v1/inventory/alerts` 读真实事件给出低库存预警 | `flask_shop/flask_shop/utils/stock.py`、`backend/app/api/routes/inventory.py` |
| 编排 | 工作台拉起 `order-events` 消费进程，启动时生成一次性内网令牌并注入两端 | `统一电商工作台/start.py` |
| 验证 | `verify_order_event_chain.py` 触发一笔真实云购订单，同时断言跨境订单投影、库存投影与预警接口；`统一电商工作台/verify.py` 校验当前工作台契约 | 仓库根目录、工作台目录 |

回归结果：云购新增 3 项事件测试，跨境新增 3 项消费契约测试，原有套件全绿。`verify_order_event_chain.py` 实机跑通，云购下单后跨境读模型出现该订单，携带 event_id 与真实金额，非演示标记。

部署提示：`backend/.env` 关闭了 `DATABASE_AUTO_CREATE`，新环境须先执行 `backend/scripts/init_order_projection.py` 或 `alembic upgrade head`（版本 `20261107_0024`）。缺表时消费器会每轮报错重试，日志在 `.runtime/order-events.log`，水位不会误推进。

库存事件已接通，低库存预警现在读真实事件数据。第三条链路（库存事件驱动内容草稿）见第九节。UI 源码层合并仍未开始。

## 七、界面契约层（2026-11-07 傍晚已落地）

| 项 | 实现 | 位置 |
|---|---|---|
| 深链与刷新 | devServer 配置 history 回退，`/admin/module_*` 直接返回 200 | `vue_shop/vue.config.js` |
| 重新加载 | iframe 改绑带 nonce 的 `currentSrc`，按钮不再空转 | `vue_shop/src/views/ModuleHostView.vue` |
| 双套侧栏 | 内嵌时隐藏跨境 SideNav 并把布局左内边距归零 | `frontend/src/components/layout/AppShell.vue` |
| 会话下发 | iframe 载入后 postMessage 下发令牌，只接受宿主 origin 且来自父窗口 | `ModuleHostView.vue`、`AppShell.vue`、`studio-web/src/session.js` |
| 回跳契约 | 登录页解析 `redirect` 与 `module_state`，子应用 URL 走 origin 白名单；iframe 内失效改顶层跳转，登录页不再嵌在框里 | `LoginView.vue`、`frontend/src/app/unified-login.ts` |
| 登出广播 | 宿主清自身并通知所有子应用清理各自存储 | `HomeView.vue` |
| 路由同步 | 子应用把当前页标题上报宿主，模块栏显示「当前页面」 | `AppShell.vue`、`ModuleHostView.vue` |
| 故障语义 | 上游不可达或 5xx 返回 503，4xx 才是 401，前端不再被误登出 | `backend/app/services/merchant_session.py`、`app/core/identity.py` |

验收：`统一电商工作台/verify.py` 把深链、端点鉴权、日志保护、会话接口与订单库存联动串成一条命令。三个前端各自构建与测试全绿，跨境后端新增 3 项故障语义测试。

仍未完成：三个模块仍是三套独立前端由 iframe 承载，源码层没有合并。

## 八、安全与权威边界（2026-11-07 傍晚已落地）

六条必修至此全部完成。

| 项 | 实现 | 位置 |
|---|---|---|
| data-hub 鉴权 | 未配置令牌时受保护端点一律 401；令牌必须与 `X-Hub-Tenant` 一一对应；请求体、路径或 query 里的目标租户不得越过令牌所属租户，越界返回 403 | `apps/data-hub/app.py`、`apps/data-hub/test_authz.py` |
| 抖店真实同步 | `POST /douyin/sync` 补 `PRODUCT_WRITE` 动作鉴权，组织 ID 从已验证会话传入，不再使用固定 `DEFAULT_ORG` | `backend/app/api/routes/integrations.py`、`app/services/platform_sync.py` |
| 售价权威 | hub 仅在新建商品时提供初始价，重复同步只改资料字段，商城改价与 SKU 定价不被覆盖 | `flask_shop/flask_shop/utils/hub_sync.py` |
| 可重跑验收 | `verify.py` 覆盖 6 种 hub 请求组合、匿名抖店同步、模块深链与三段业务联动 | `统一电商工作台/verify.py` |

## 九、库存事件驱动的补货草稿（2026-11-07 夜已落地）

第三条链路：同一批云购库存事件，跨境用它做预警，内容平台用它生成草稿。两个消费方各自持水位，互不影响。

| 环节 | 实现 | 位置 |
|---|---|---|
| 事件契约 | `stock.changed` 增补 `external_id`（取 `t_product_knowledge`），商品在数据中心一侧的标识随事件下发；本地自建商品该字段为 null | `flask_shop/flask_shop/utils/stock.py` |
| 消费方 | 内容平台独立 worker 轮询同一端点，`event_id` 作幂等键，水位表 `inventory_draft_watermark` 记录断点，低于阈值才生成草稿 | `apps/api-server/src/services/inventory-draft-consumer.js`、`src/repositories/inventory-drafts-repository.js` |
| 存储 | `inventory_content_drafts`，`event_id` 唯一约束，按租户过滤；建表在仓储层幂等执行，不动 `infra/sql` 基线 | 同上 |
| 查询 | `GET /api/inventory-drafts`，走云购会话身份，按 `identity.tenantId` 过滤，只读 | `apps/api-server/src/routes/inventory-drafts.js` |
| 页面 | 跨境新增 `/inventory` 「库存预警」页，读事件投影，展示商品、库存、变动、来源与事件时间 | `frontend/src/views/InventoryAlertsView.vue` |
| 编排 | 工作台拉起 `content-drafts` 进程；`/status` 新增 `pipelines` 组，按进程存活判定 `order-events` 与 `content-drafts` | `统一电商工作台/start.py` |
| 验证 | `verify_order_event_chain.py` 下单把库存压到阈值以下，断言草稿生成且 `eventId` 可回溯；`verify.py` 一条命令串起三段联动 | 仓库根目录、工作台目录 |

阈值由工作台注入 `INVENTORY_LOW_STOCK_THRESHOLD`（默认 25），消费间隔 10 秒。草稿文案是确定性模板，不调用大模型：这一层的价值在事件驱动与可追溯，接生成模型只需替换 `buildDraft` 一个函数。

实测：下单 8 件后库存 27 → 20，云购事件 `event_id=18`，跨境预警与内容草稿同源；再跑一次 `event_id=20`，草稿表新增一行而非重复。

## 十、已知口径约束

历史报告里"功能全部跑通"的验收范围只覆盖无参 GET 与页面巡检，写接口、鉴权边界、跨租户路径均未纳入。该结论与本页必修清单并不矛盾，两者测的是不同对象。对外表述须把演示与生产分开。
