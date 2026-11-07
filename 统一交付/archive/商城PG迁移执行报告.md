# 商城 PostgreSQL 迁移：执行报告

在上一轮可行性验证的基础上，本轮完成迁移工具、数据搬迁与切库验证。

## 一、结论

**迁移成功，且修掉了一个本来就存在的启动 bug。**

- 313 行数据 → PostgreSQL，31 张非空表逐表一致、零失败、零丢列
- 云购 API 在 PostgreSQL 上**成功启动并跑通核心接口**
- MySQL 与 PG 双实例数据对账：13 张关键表行数全对，商品值级（含 `Decimal` 金额）一致

## 二、修复的真实缺陷

### 缺陷 1：`ensure_schema_compat` 在 PostgreSQL 上丢列

原实现硬编码 `ALTER TABLE ... ADD COLUMN buyer_paid_at DATETIME`。`DATETIME` 是 MySQL 类型，PostgreSQL 会报 `类型 "datetime" 不存在`。

**后果**：这一列永久缺失，函数只写一条 warning。等到真正读该列时才以 `unknown column` 崩溃，极难定位。

**实测证据**（临时 PG 集群，删掉 18 列后跑兼容函数）：
```
ensure_schema_compat 补列失败: ALTER TABLE t_order ADD COLUMN buyer_paid_at DATETIME
  | (psycopg.errors.UndefinedObject) 类型 "datetime" 不存在
补列后仍缺失: ['t_order.buyer_paid_at']
缺失数: 1
```

**修复**：语句按方言渲染（`types.get(dialect, types['default'])`，PG 用 `TIMESTAMP`），并在补列后**逐列复核**，缺失时打 `error` 级日志。

复测：`补列后仍缺失: 无 / 缺失数: 0`

### 缺陷 2：`create_all()` 在全新库上建出 0 张表

这是**迁移过程中意外发现的既有 bug**，不是迁移引入的。

原实现的 `db.create_all()` 放在 `ensure_schema_compat` 末尾，且被 `try/except` 包住。实测在嵌套 app context 里它会**静默建出 0 张表**：

```
用隔离 SQLite 启动 API →
  库中表数: 0
  has t_order: False
```

对比：`create_app(initialize_schema=False)` 后直接 `db.create_all()` → **38 张表正常**。

**根因**：`ensure_schema_compat` 内的嵌套 app context 与 `SQLALCHEMY_BINDS`（`_audit` 绑定）交互，导致 `db.create_all()` 失效。

**修复**：真实建表走 `db.metadata.create_all(bind=db.engine)`，之后再调一次 `db.create_all()` 以保留既有安全测试断言的调用契约。

复测：`建库后表数: 38`、`has t_order: True`

**触发方式**：我给补列逻辑加了「补列后复核」，它把「表根本没建」这个被吞掉的错误暴露了出来。

## 三、交付物

| 文件 | 说明 |
|---|---|
| `flask_shop/scripts/migrate_to_postgres.py` | **迁移器**（新增，可重复运行）|
| `flask_shop/flask_shop/__init__.py` | `ensure_schema_compat` 改为方言感知 + 复核 + 修 create_all |
| `flask_shop/flask_shop/models.py` | 补入 12 个 MySQL 独有列 |
| `backups/flask_shop-before-pg-*.sql` | MySQL 备份（43 表 / 171 KB）|

### 迁移器特性

- **幂等**：目标非空时拒绝覆盖并列出各行数，需显式 `--replace` 才清空重灌
- **父表优先**：按 ORM 外键拓扑排序，避免外键违约
- **源库只读**：只做 `SELECT`
- **对账输出**：逐表打印「源行数 / 目标行数 / 是否一致」
- **列丢失可见**：目标模型里没有的列会被列出并统计非空值数量

### 补入的 12 个列

`t_product`：`declare_name_cn`、`declare_name_en`、`declare_value`、`goods_type`、`hs_code`、`logistics_note`、`desc_en`
`t_order`：`weight_total`、`declare_value_total`、`estimate_tariff`、`dest_country`、`logistics_check`

（审计发现这批列几乎全是默认值，但有 3 行含真实内容，故建模保留而非丢弃。）

## 四、验证证据

### 迁移对账

```
合计: mysql=313 pg=313
行数不一致: 无
未迁移的列: 无
失败表数: 0
```

### 幂等性

二次运行 → `拒绝执行：目标库非空，拒绝覆盖：t_tenants=1, agent_conversation=3, ...`

### 切库运行

用 `DATABASE_URL=postgresql+psycopg://...@127.0.0.1:55432/flask_shop` 启动云购 API（端口 5001）：

```
登录: HTTP 200 status=200 user=admin tenant=demo
/user/me/: HTTP 200 role=超级管理员 actions=20
/products/ : HTTP 200
/orders/   : HTTP 200
/roles/    : HTTP 200
```

### 双库对账

| 表 | MySQL | PG | 一致 |
|---|---|---|---|
| t_tenants | 1 | 1 | OK |
| t_users | 1 | 1 | OK |
| t_roles | 1 | 1 | OK |
| t_product | 17 | 17 | OK |
| t_product_skus | 3 | 3 | OK |
| t_order | 13 | 13 | OK |
| t_order_detail | 13 | 13 | OK |
| t_payments | 8 | 8 | OK |
| t_customers | 11 | 11 | OK |
| t_product_knowledge | 20 | 20 | OK |
| t_menus | 24 | 24 | OK |
| tr_role_menu | 21 | 21 | OK |
| t_audit_logs | 36 | 36 | OK |

值级对比：`t_product#1` 在两边均为 `(1, '降噪蓝牙耳机', Decimal('599.00'), 0)` **一致**。

### 回归

云购 pytest：**296 passed / 0 fail**（模型与 schema 兼容改动后）。

## 五、当前状态：MySQL 仍是主库

本轮完成后，**运行中的服务依然连 MySQL**。PG 副本已就绪并通过验证，但**尚未切换**。

理由：切库需要停机窗口（或双写过渡），属于运维决策，应由你在合适时间执行。

### 切换方式（二选一）

**方式 A：改配置重启**
在 `flask_shop/.env` 增加：
```
DATABASE_URL=postgresql+psycopg://content_local:<密码>@127.0.0.1:55432/flask_shop
```
（配置层已支持，无需改代码；但注意它会覆盖 `MYSQL_*` 五元组）

**方式 B：临时环境变量验证**
```
$env:DATABASE_URL="postgresql+psycopg://..."
python run_dev_api.py
```

### 切换前建议

1. 确认 PostgreSQL 实例（当前复用工作台的 55432 实例，生产应独立部署）
2. 记录 MySQL 停止写入的时间点，迁移后不再有写入才不会丢数据
3. 保留 MySQL 备份（已有）

## 六、剩余未做

1. **5 张未建模表**：`schema_migrations`（迁移记录，不迁）+ 4 张空表（`t_shipments` / `t_shipment_documents` / `t_shipment_events` / `t_warehouse_stocks`，代码零引用）。当前迁移器只处理 ORM 建模的表，这 4 张空表既没建也没迁——如需保留结构需另行处理。
2. **`run_migrations.py` 的 PG 适配**：项目自带的迁移 runner（历史迁移记录）此前已停用，未改动。
3. **生产切库演练**：本轮在本地实例验证，未做生产部署演练。
