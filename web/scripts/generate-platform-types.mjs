#!/usr/bin/env node
/**
 * 从工程 FastAPI 导出的 OpenAPI 生成前端类型（UNIFY-PRODUCT-01 任务B）。
 *
 * 设计约束：
 * - 只读工程的冻结 OpenAPI 文件，不启动服务、不联网、不生成运行时代码。
 * - 输出确定性：同一 OpenAPI 内容得到同一文件（不写时间戳）。
 * - 未声明响应 schema 的接口不会凭空出现类型；缺口由 docs 与适配器分别标注。
 *
 * 用法：node scripts/generate-platform-types.mjs [openapi.json] [out.ts]
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const DEFAULT_SOURCE = resolve(here, "../../engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json");
const DEFAULT_TARGET = resolve(here, "../src/generated/platformOpenapi.ts");

const RESERVED = new Set([
  "break", "case", "catch", "class", "const", "continue", "default", "delete", "do", "else", "enum",
  "export", "extends", "false", "finally", "for", "function", "if", "import", "in", "instanceof",
  "new", "null", "return", "super", "switch", "this", "throw", "true", "try", "typeof", "var",
  "void", "while", "with", "as", "implements", "interface", "let", "package", "private",
  "protected", "public", "static", "yield",
]);

const args = process.argv.slice(2);
const check = args.includes("--check");
const paths = args.filter((value) => value !== "--check");
if (paths.length > 2 || paths.some((value) => value.startsWith("--"))) {
  throw new Error("Usage: generate-platform-types.mjs [openapi.json] [out.ts] [--check]");
}
const sourcePath = resolve(paths[0] ?? DEFAULT_SOURCE);
const targetPath = resolve(paths[1] ?? DEFAULT_TARGET);
const raw = readFileSync(sourcePath, "utf8");
const sha256 = createHash("sha256").update(raw).digest("hex");
const document = JSON.parse(raw);
const schemas = document.components?.schemas ?? {};

function refName(ref) {
  const prefix = "#/components/schemas/";
  if (typeof ref !== "string" || !ref.startsWith(prefix)) throw new Error(`不支持的引用：${ref}`);
  return ref.slice(prefix.length);
}

function literal(value) {
  return JSON.stringify(value);
}

function typeOf(schema) {
  if (schema === true || schema === undefined) return "unknown";
  if (typeof schema !== "object" || schema === null) return "unknown";
  if (typeof schema.$ref === "string") return refName(schema.$ref);
  if (Array.isArray(schema.anyOf)) return union(schema.anyOf.map(typeOf));
  if (Array.isArray(schema.oneOf)) return union(schema.oneOf.map(typeOf));
  if (Array.isArray(schema.allOf)) return union(schema.allOf.map(typeOf));
  if (schema.const !== undefined) return literal(schema.const);
  if (Array.isArray(schema.enum)) return union(schema.enum.map(literal));
  switch (schema.type) {
    case "string":
      return "string";
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "null":
      return "null";
    case "array":
      return `Array<${typeOf(schema.items)}>`;
    case "object":
      if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
        return `Record<string, ${typeOf(schema.additionalProperties)}>`;
      }
      if (schema.additionalProperties === true || !schema.properties) return "Record<string, unknown>";
      return inlineObject(schema);
    default:
      return "unknown";
  }
}

function union(parts) {
  const unique = [...new Set(parts)];
  return unique.length === 1 ? unique[0] : unique.join(" | ");
}

function propertyName(name) {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name) && !RESERVED.has(name) ? name : JSON.stringify(name);
}

function docComment(text, indent) {
  const lines = String(text).split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length === 0) return "";
  if (lines.length === 1) return `${indent}/** ${lines[0].replace(/\*\//g, "*\\/")} */\n`;
  const body = lines.map((line) => `${indent} * ${line.replace(/\*\//g, "*\\/")}`).join("\n");
  return `${indent}/**\n${body}\n${indent} */\n`;
}

function fields(schema, indent) {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, property]) => {
    const optional = required.has(name) ? "" : "?";
    const description = property?.description ? docComment(property.description, indent) : "";
    return `${description}${indent}${propertyName(name)}${optional}: ${typeOf(property)};`;
  });
}

function inlineObject(schema) {
  const body = fields(schema, "  ");
  return body.length === 0 ? "Record<string, unknown>" : `{\n${body.join("\n")}\n}`;
}

function declare(name, schema) {
  const description = schema?.description ? docComment(schema.description, "") : "";
  if (schema?.type === "object" && !schema.properties && !schema.additionalProperties) {
    return `${description}export type ${name} = Record<string, unknown>;`;
  }
  if (schema?.type === "object" && schema.properties) {
    const body = fields(schema, "  ");
    return `${description}export interface ${name} {\n${body.join("\n")}\n}`;
  }
  return `${description}export type ${name} = ${typeOf(schema)};`;
}

const header = [
  "/* eslint-disable */",
  "/**",
  " * 由 scripts/generate-platform-types.mjs 自动生成，请勿手工修改。",
  ` * 来源：${relative(resolve(here, "../.."), sourcePath).split("\\").join("/")}`,
  ` * 来源 SHA-256：${sha256}`,
  ` * 来源版本：${document.info?.version ?? "unknown"}；schema 数量：${Object.keys(schemas).length}`,
  " * 说明：只有声明了响应 schema 的接口会产出类型；未声明的接口在适配层显式处理。",
  " */",
  "",
].join("\n");

const blocks = Object.keys(schemas).sort().map((name) => declare(name, schemas[name]));
const generated = `${header}${blocks.join("\n\n")}\n`;
if (check) {
  if (readFileSync(targetPath, "utf8") !== generated) {
    throw new Error("Generated platform types are stale; regenerate from the committed OpenAPI.");
  }
} else {
  writeFileSync(targetPath, generated, "utf8");
}
process.stdout.write(`generated ${targetPath}\nsha256=${sha256}\nschemas=${blocks.length}\n`);
