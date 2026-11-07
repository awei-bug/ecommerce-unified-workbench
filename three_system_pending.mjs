// Add a separate test-only product with no price to exercise the merchant workflow.
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import assert from 'node:assert/strict'
const clientPath = path.join(process.env.CHAIN_CONTENT_ROOT, 'apps/api-server/src/lib/data-hub-client.js')
const { createDataHubClient } = await import(pathToFileURL(clientPath).href)
const client = createDataHubClient()
const result = await client.ingest({ items: [
  { external_id: 'cc-pending-demo', title: '待补价演示杯', stock: 5, selling_points: ['测试卖点'] },
  { external_id: 'cc-conflict-demo', title: '冲突测试杯', stock: 5, price: 20 },
], jobId: 'pending-test' })
assert.equal(result.ok_count, 2)
