import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parse } from "vue/compiler-sfc";
import { describe, expect, it } from "vitest";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    return entry.isDirectory()
      ? sourceFiles(file)
      : /\.(ts|vue)$/.test(file) && !file.endsWith(".d.ts")
        ? [file]
        : [];
  });
}

function imports(file: string): string[] {
  let source = readFileSync(file, "utf8");
  if (file.endsWith(".vue")) {
    const { descriptor } = parse(source, { filename: file });
    source = [descriptor.script?.content, descriptor.scriptSetup?.content].join(
      "\n",
    );
  }
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const values: string[] = [];
  function visit(node: ts.Node) {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      values.push(node.moduleSpecifier.text);
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments[0] &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      values.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  return values;
}

describe("前端目录边界", () => {
  it("跨目录导入使用别名，且不引用旧目录", () => {
    const violations = sourceFiles(root).flatMap((file) =>
      imports(file)
        .filter(
          (value) =>
            value.startsWith("../") ||
            /^@\/(lib|pages|stores)(\/|$)/.test(value),
        )
        .map((value) => `${relative(root, file)}: ${value}`),
    );
    expect(violations).toEqual([]);
  });

  it("共享基础设施与通用组件不反向依赖业务或页面编排", () => {
    const shared = [
      "api",
      "composables",
      "utils",
      "types",
      "components/common",
      "components/charts",
    ];
    const violations = shared.flatMap((directory) =>
      sourceFiles(join(root, directory)).flatMap((file) =>
        imports(file)
          .filter(
            (value) =>
              /^@\/(features|views|router|layouts)\//.test(value) ||
              value === "@/config/lists",
          )
          .map((value) => `${relative(root, file)}: ${value}`),
      ),
    );
    expect(violations).toEqual([]);
  });
});
