# public_demo_data

`dataset.json` 是比赛端唯一合成数据集，由前端数据源导出：

```bash
cd ../web
node_modules/.bin/vite-node scripts/export-demo-data.ts
```

内容：24 条管段、17 个节点、5 个服务对象、5 个情景、10 个隔离方案、3 个候选方案、离线基线评分/影响，以及 10 个合成时间线图事件和 15 条任务记录（基准 `2026-11-01T08:00:00+08:00`）。

- 只含合成数据，不对应真实设施。
- 修改 `web/src/data/network.ts` 或 `web/src/data/timeline.ts` 后必须重新导出并重跑前端/API 测试。
- 不要把工程原始材料、真实管网、企业接口或凭据复制到本目录。
