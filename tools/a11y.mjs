// Run axe-core over every built page, headlessly, in a jsdom DOM.
//
// This is the WCAG gate. It loads each page into a real DOM implementation and runs
// axe-core's WCAG 2.0/2.1/2.2 A and AA rule sets plus the best-practice set, and exits
// non-zero on any violation. It is not a substitute for a person looking at the pages:
// jsdom does no layout and computes no colours, so the rules that depend on rendered
// geometry or on painted pixels cannot fire here. Colour contrast is measured
// separately, off the palette itself, in tests/test_site.py, and what still needs a
// person is named in README.md.
//
// Usage: node tools/a11y.mjs <directory-of-html-files>

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";
import axe from "axe-core";

const TAGS = [
  "wcag2a",
  "wcag2aa",
  "wcag21a",
  "wcag21aa",
  "wcag22aa",
  "best-practice",
];

// Rules jsdom cannot decide. Left running they would report "incomplete", not "pass",
// and a gate that treats an unrunnable rule as a pass teaches a reader the wrong thing.
// They are listed here so the gap is on the record.
const NEEDS_A_RENDERER = new Set([
  "color-contrast", // no layout, no painted pixels; measured off the palette instead
  "target-size", // 2.5.8 needs box geometry
]);

async function checkPage(path) {
  const html = readFileSync(path, "utf8");
  // "outside-only" gives an eval to inject axe with, without ever running a script that
  // came out of the page. These pages ship no script, and the checker should not start
  // executing one if that ever changes.
  // axe probes for a canvas to decide whether it can sample colours. jsdom has none, so
  // it reports that once per page. Everything else the page or axe says is forwarded.
  const console_ = new VirtualConsole();
  console_.forwardTo(console, { jsdomErrors: "none" });
  console_.on("jsdomError", (error) => {
    if (!/getContext\(\) method/.test(error.message)) {
      console.error(error.message);
    }
  });

  const dom = new JSDOM(html, {
    pretendToBeVisual: true,
    runScripts: "outside-only",
    virtualConsole: console_,
  });
  const { window } = dom;
  window.eval(axe.source);
  const results = await window.axe.run(window.document, {
    runOnly: { type: "tag", values: TAGS },
    resultTypes: ["violations"],
  });
  dom.window.close();
  return results;
}

const dir = process.argv[2];
if (!dir) {
  console.error("usage: node tools/a11y.mjs <directory-of-html-files>");
  process.exit(2);
}

const pages = readdirSync(dir)
  .filter((name) => name.endsWith(".html"))
  .sort();

if (pages.length === 0) {
  console.error(`no .html files in ${dir}; build the site first`);
  process.exit(2);
}

let failed = 0;
for (const name of pages) {
  const results = await checkPage(join(dir, name));
  const violations = results.violations.filter(
    (v) => !NEEDS_A_RENDERER.has(v.id),
  );
  if (violations.length === 0) {
    console.log(`ok   ${name}  (${TAGS.join(", ")})`);
    continue;
  }
  failed += violations.length;
  console.error(`FAIL ${name}`);
  for (const v of violations) {
    console.error(`  [${v.impact}] ${v.id}: ${v.help}`);
    console.error(`    ${v.helpUrl}`);
    for (const node of v.nodes.slice(0, 5)) {
      console.error(`    at ${node.target.join(" ")}`);
      console.error(`      ${node.failureSummary?.replace(/\n/g, "\n      ")}`);
    }
    if (v.nodes.length > 5) {
      console.error(`    ...and ${v.nodes.length - 5} more`);
    }
  }
}

if (failed > 0) {
  console.error(`\n${failed} accessibility violation(s)`);
  process.exit(1);
}
console.log(`\n${pages.length} page(s) clean against ${TAGS.length} rule sets`);
