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
| 2 | data-hub 令牌 fail-closed 且绑定租户，抖店同步写接口补鉴权 | 待做 | `apps/data-hub/app.py:60-70`、`backend/app/api/routes/integrations.py:41,177` |
| 3 | 已有商品售价仅在新建时初始化 | 待做 | `flask_shop/flask_shop/utils/hub_sync.py:135-136` |
| 4 | 单一令牌源、回跳契约、统一登出广播、上游故障区分 401 与 503 | 待做 | `vue_shop/src/views/ModuleHostView.vue`、`frontend/src/app/unified-login.ts` |
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

库存事件已接通，低库存预警现在读真实事件数据。下一阶段让预警驱动内容平台生成草稿，再回推 hub 与云购上架。UI 契约层排在数据流之后。

## 七、已知口径约束

历史报告里"功能全部跑通"的验收范围只覆盖无参 GET 与页面巡检，写接口、鉴权边界、跨租户路径均未纳入。该结论与本页必修清单并不矛盾，两者测的是不同对象。对外表述须把演示与生产分开。
