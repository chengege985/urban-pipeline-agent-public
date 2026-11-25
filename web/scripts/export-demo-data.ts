// 导出比赛端唯一的合成数据集，供独立 FastAPI 实例读取。
// 运行：node_modules/.bin/vite-node scripts/export-demo-data.ts
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { DemoProvider } from "../src/providers/demoProvider";

const provider = new DemoProvider();
const dataset = await provider.loadDataset();
const target = resolve(process.cwd(), "../public_demo_data/dataset.json");
writeFileSync(target, `${JSON.stringify(dataset, null, 2)}\n`, "utf8");
console.log(`wrote ${target}: ${dataset.pipes.length} pipes, ${dataset.scenarios.length} scenarios`);
