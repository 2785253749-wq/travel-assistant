const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..", "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("shared pages expose the coastal glass visual system", () => {
  const styles = read("app/static/styles.css");

  for (const token of [
    "--coastal-sky",
    "--coastal-seafoam",
    "--coastal-teal",
    "--coastal-glass",
    "--coastal-line",
    "--coastal-shadow",
  ]) {
    assert.match(styles, new RegExp(`${token}:`), `${token} should be defined`);
  }

  assert.match(styles, /background:\s*var\(--coastal-sky\)/);
  assert.match(styles, /backdrop-filter:\s*blur\(/);
  assert.match(styles, /\.auth-page[\s\S]*var\(--coastal-glass\)/);
  assert.match(styles, /\.profile-page[\s\S]*var\(--coastal-glass\)/);
  assert.match(styles, /\.assistant-toggle[\s\S]*var\(--coastal-teal\)/);
});

test("explore, auth, and profile pages share the stylesheet", () => {
  for (const page of ["index.html", "auth.html", "profile.html"]) {
    const html = read(`app/static/${page}`);
    assert.match(html, /href="\/static\/styles\.css"/, `${page} should use shared styles`);
  }
});

test("large page surfaces avoid expensive backdrop blur while small overlays keep glass", () => {
  const styles = read("app/static/styles.css");
  const largeSurfaceBlocks = [
    styles.match(/#explore-page,[\s\S]*?\.profile-page \{([\s\S]*?)\}/)?.[1],
    styles.match(/\.auth-page-card,[\s\S]*?\.profile-page-card \{([\s\S]*?)\}/)?.[1],
  ];

  for (const block of largeSurfaceBlocks) {
    assert.ok(block, "expected a large surface style block");
    assert.doesNotMatch(block, /backdrop-filter\s*:/);
  }

  assert.match(
    styles,
    /\.assistant-panel[\s\S]*backdrop-filter\s*:/,
    "small assistant overlay may keep the glass effect",
  );
});

test("assistant keeps the outer shell fixed and scrolls only its messages", () => {
  const styles = read("app/static/styles.css");

  assert.match(styles, /\.assistant-panel\s*\{[\s\S]*display:\s*flex[\s\S]*overflow:\s*hidden/);
  assert.match(styles, /\.chat-panel\s*\{[\s\S]*min-height:\s*0/);
  assert.match(styles, /\.chat-messages\s*\{[\s\S]*overflow-y:\s*auto/);
});
