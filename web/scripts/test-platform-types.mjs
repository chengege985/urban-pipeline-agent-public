import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "../../engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json");
const generator = resolve(here, "generate-platform-types.mjs");
const work = mkdtempSync(resolve(tmpdir(), "platform-type-contract-"));
const out = resolve(work, "out.ts");
const run = (...args) => spawnSync(process.execPath, [generator, ...args], {encoding:"utf8"});
try {
  assert.equal(run(source, out).status, 0);
  const first = readFileSync(out, "utf8");
  assert.equal(first, readFileSync(resolve(here, "../src/generated/platformOpenapi.ts"), "utf8"));
  assert.equal(run(source, out, "--check").status, 0);
  assert.equal(readFileSync(out, "utf8"), first);
  writeFileSync(out, first.replace("export interface", "export type BROKEN"));
  const drift = readFileSync(out, "utf8");
  assert.notEqual(run(source, out, "--check").status, 0);
  assert.equal(readFileSync(out, "utf8"), drift, "check must not overwrite drift");
  assert.notEqual(run("--unexpected").status, 0);
  console.log("platform type contract: 7 assertions passed");
} finally { rmSync(work, {recursive:true, force:true}); }
