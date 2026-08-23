const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..", "..");
const css = fs.readFileSync(path.join(ROOT, "app", "static", "iconfont.css"), "utf8");

test("community iconfont mapping covers the first UI batch", () => {
  for (const name of ["search", "like", "like-active", "comment", "retry", "back", "forward", "add", "profile"]) {
    assert.match(css, new RegExp(`voyage-icon--${name}::before`));
  }
  assert.match(css, /font_712012_e58cglk9ys6/);
});
