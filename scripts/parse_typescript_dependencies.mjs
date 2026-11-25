/** Offline TypeScript AST/module-resolution helper for module-boundary checks. */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
const payload = JSON.parse(readFileSync(0, 'utf8'));
const root = path.resolve(payload.root);
const require = createRequire(path.join(root, 'web/package.json'));
const ts = require('typescript');
const contents = new Map(Object.entries(payload.files).map(([name, text]) => [path.resolve(root, name), text]));
const host = {
  fileExists: name => contents.has(path.resolve(name)),
  readFile: name => contents.get(path.resolve(name)),
  directoryExists: name => [...contents.keys()].some(file => file.startsWith(path.resolve(name) + path.sep)),
  realpath: name => path.resolve(name),
  getCurrentDirectory: () => root,
};
const configPath = path.join(root, 'web/tsconfig.json');
const rawConfig = contents.get(configPath) ?? '{}';
const config = ts.parseConfigFileTextToJson(configPath, rawConfig);
if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
const options = ts.convertCompilerOptionsFromJson(config.config.compilerOptions ?? {}, path.dirname(configPath));
if (options.errors.length) throw new Error(options.errors.map(e => ts.flattenDiagnosticMessageText(e.messageText, '\n')).join('\n'));
const rel = name => path.relative(root, name).split(path.sep).join('/');
const result = { edges: [], facades: [], unresolved: [], dynamic_unknown: [], parse_errors: [] };
for (const [filename, text] of contents) {
  if (!/\.(?:[cm]?ts|tsx|[cm]?js|jsx)$/.test(filename)) continue;
  const source = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true);
  const origin = rel(filename);
  for (const diagnostic of source.parseDiagnostics) result.parse_errors.push({ source: origin, reason: ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n') });
  const statements = source.statements.filter(s => !ts.isEmptyStatement(s));
  // Only genuine export-forwarding files are followed as facades. No executable statements.
  if (statements.length && statements.every(s => ts.isExportDeclaration(s) && s.moduleSpecifier)) result.facades.push(origin);
  function record(node, specifier, kind) {
    if (!specifier || !ts.isStringLiteralLike(specifier)) {
      result.dynamic_unknown.push({ source: origin, kind, line: source.getLineAndCharacterOfPosition(node.pos).line + 1 });
      return;
    }
    const spec = specifier.text;
    const resolved = ts.resolveModuleName(spec, filename, options.options, host).resolvedModule;
    const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    if (resolved && contents.has(path.resolve(resolved.resolvedFileName))) {
      result.edges.push({ source: origin, target: rel(resolved.resolvedFileName), kind, specifier: spec, line });
    } else if (spec.startsWith('.') || spec.startsWith('/') || Object.keys(options.options.paths ?? {}).some(p => spec.startsWith(p.split('*')[0]))) {
      if (!/\.(?:css|scss|woff2|svg|png|jpg|jpeg|json)$/.test(spec)) result.unresolved.push({ source: origin, specifier: spec, line });
    }
  }
  function visit(node) {
    if (ts.isImportDeclaration(node)) record(node, node.moduleSpecifier, 'import');
    else if (ts.isExportDeclaration(node) && node.moduleSpecifier) record(node, node.moduleSpecifier, 'export');
    else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) record(node, node.moduleReference.expression, 'import_equals');
    else if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === 'require'))) record(node, node.arguments[0], node.expression.kind === ts.SyntaxKind.ImportKeyword ? 'dynamic_import' : 'require');
    else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) record(node, node.argument.literal, 'import_type');
    ts.forEachChild(node, visit);
  }
  visit(source);
}
process.stdout.write(JSON.stringify(result));
