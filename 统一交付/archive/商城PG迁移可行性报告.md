# 商城 PostgreSQL 迁移可行性报告

目标：验证云购商城能否从 MySQL 迁到 PostgreSQL（统一数据库的最后一块）。本轮**只做到可行性验证 + 模型补全，未切库**。

## 一、结论

**可行。** 313 行数据、31 张非空表在 PostgreSQL 上**逐表行数一致、零失败**。

迁移前需修的一处已修：**12 个 MySQL 独有列未建模**，直接迁会静默丢数据。

## 二、发现并修复的真实问题

### 问题：12 个遗留列未建模（会导致丢数据）

首轮 dry-run 暴露：`t_product` 与 `t_order` 有 12 个列存在于 MySQL 但不在 ORM 里。用 `SELECT *` 迁移时直接报 `UndefinedColumn`。

先审计这些列的真实数据量：

| 表 | 列 | 非空行 | 去重值 | 判断 |
|---|---|---|---|---|
| `t_product` | `declare_name_cn` | 2 | 2 | **有真实内容** |
| `t_product` | `declare_name_en` | 1 | 1 | **有真实内容** |
| `t_product` | `desc_en` | 1 | 1 | **有真实内容** |
| `t_product` | `logistics_note` | 1 | 1 | **有真实内容** |
| `t_product` | `declare_value` | 17 | 2 | 默认值 0 为主 |
| `t_product` | `goods_type` | 17 | 2 | 默认值 `general`/`battery` |
| `t_product` | `hs_code` | 0 | 0 | 全空 |
| `t_order` | `weight_total` | 13 | **1** | 全 0.0 |
| `t_order` | `estimate_tariff` | 13 | **1** | 全 0.0 |
| `t_order` | `declare_value_total` | 13 | **1** | 全 0.0 |
| `t_order` | `dest_country` | 9 | **1** | 全 'JP' |
| `t_order` | `logistics_check` | 0 | 0 | 全空 |

**处理**：12 列全部补入 ORM 模型（`t_product` 25 列、`t_order` 41 列）。理由是 3 行有真实内容，建模比论证"可以丢"更省事，也避免将来按列名查询时踩 `unknown column`。

**过程中的一次错误**：首次插入用 `rindex` 反向查找 `to_dict`，把列加到了 `Attribute` 类而非 `Product`。已撤销并用明确的类体锚点重做，验证落在 `class Product` 与 `ProductAttr` 之间。

### 问题：外键依赖顺序

第二轮 dry-run 显示：按表名字母序迁移会让子表先于父表插入，`t_tenants` 未就位导致 20+ 张表外键失败。

**处理**：按 ORM 声明的外键做拓扑排序（父表优先），迁移期间 `SET session_replication_role = replica` 关闭约束检查，结束后恢复。

### 环境问题：沙箱管道限制

`subprocess.run(capture_output=True)` 在本环境会**挂起**（受限模式下不能开命名管道）。所有子进程改为输出重定向到文件。

## 三、验证证据

### 1. Schema 建表（临时 PG 集群）

```
PG 建表数: 38
ORM 表数: 38
缺失: 无
多出: 无
```

### 2. 数据迁移（MySQL → PostgreSQL）

```
TOTAL mysql=313 pg=313
行数不一致的表: 无
失败表数: 0
未迁移的遗留列: 无
```

31 张非空表逐表核对，含 `t_product`(17)、`t_order`(13)、`t_order_detail`(13)、`t_audit_logs`(36)、`t_agent_traces`(31)、`t_product_knowledge`(20)、`tr_role_menu`(21)、`t_menus`(24) 等。

### 3. 回归

云购 pytest：**296 passed / 0 fail**（模型改动后无破坏）。

### 4. 隔离性

全程只读 MySQL；PostgreSQL 用临时集群（端口 55704/55705/55706），用完删除。

## 四、5 张未建模表（不阻塞迁移）

| 表 | 行数 | 处理建议 |
|---|---|---|
| `schema_migrations` | 14 | 迁移记录，**不迁**（PG 用 `alembic_version`）|
| `t_shipments` | 0 | 空表，代码**零引用** |
| `t_shipment_documents` | 0 | 空表，代码**零引用** |
| `t_shipment_events` | 0 | 空表，代码**零引用** |
| `t_warehouse_stocks` | 0 | 空表，代码**零引用** |

实测 grep：`t_shipments` / `t_shipment_*` / `t_warehouse_stocks` / `shipment` / `warehouse_stock` 在云购全仓（`.py/.js/.vue`）**均 0 处命中**。

**结论**：4 张空表可只搬结构或不搬；`schema_migrations` 不需要迁。

## 五、正式切库还需做的事（本轮未做）

1. **迁移脚本产品化**：本轮验证脚本是临时的，需要落成可重复运行的迁移器（幂等、可回滚、带对账输出）。
2. **`ensure_schema_compat` 适配 PG**：它硬编码了 19 条 MySQL 风格 `ALTER TABLE ... ADD COLUMN`，PG 下语法与幂等语义不同。
3. **`DATABASE_URL` 切换与配置**：`.env` 现为 MySQL 五元组，需支持 PG URL（配置层已支持 `DATABASE_URL` 覆盖）。
4. **恢复演练**：备份 MySQL（42/43 张表）+ 在隔离 PG 恢复。
5. **写入切换策略**：停机窗口或双写过渡，需业务确认。
6. **5 张未建模表的最终处置**：确认可弃或补建模。

## 六、方法论：沙箱内跑子进程

- 不要用 `subprocess.run(..., capture_output=True)`，会挂起。
- 改为 `subprocess.call(args, stdout=open(log,'a'), stderr=subprocess.STDOUT)`。
- 需要看输出时读日志文件，而不是读管道。
