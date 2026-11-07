// Exercise the content platform's actual completed-job ingestion service.
import { pathToFileURL } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root = process.env.CHAIN_CONTENT_ROOT;
const load = (file) => import(pathToFileURL(path.join(root, "apps/api-server/src", file)).href);
const { createDataHubClient } = await load("lib/data-hub-client.js");
const { createDataHubSyncService } = await load("services/data-hub-sync-service.js");
const repository = {
  getJobById: async () => ({ batchId: "batch-1" }),
  listExportRowsByJobId: async () => [{ productId: "chain-1", productName: "联调便携水杯", marketingCopyTitle: "Portable Cup" }],
  listJobItemsByJobId: async () => [{ id: "item-1", productId: "chain-1", status: "success" }],
  listOutputsByJobId: async () => [],
  listProductsByBatchId: async () => [{ id: "chain-1", normalizedData: { price: 129, stock: 30, sellingPoint: "轻便易携带", faq: [{ q: "容量多少", a: "380ml" }] } }],
};
const service = createDataHubSyncService({ repository, listingPackService: {}, dataHubClient: createDataHubClient() });
const result = await service.ingestJobContent({ jobId: "chain-job" });
assert.equal(result.hub.okCount, 1);
assert.equal(result.items[0].external_id, "cc-chain-1");
console.log("PASS 内容任务产物经实际同步服务写入商品数据中心");
