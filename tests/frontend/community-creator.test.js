const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "app", "static", "community-creator.html"), "utf8");
const script = fs.readFileSync(path.join(ROOT, "app", "static", "community-creator.js"), "utf8");

test("creator page has public profile and gallery containers", () => {
  assert.match(html, /id="community-creator-profile"/);
  assert.match(html, /id="community-creator-grid"/);
  assert.match(html, /community-creator\.js/);
  assert.doesNotMatch(script, /innerHTML\s*=/);
});

test("creator page supports empty avatar and pagination", () => {
  assert.match(script, /initials/);
  assert.match(script, /next_cursor/);
  assert.match(script, /community-creator-retry/);
  assert.match(script, /textContent/);
  assert.match(script, /safeUrl/);
});

test("creator page consumes only the public creator projection", () => {
  assert.match(html, /iconfont\.css/);
  assert.match(script, /payload\.creator/);
  assert.match(script, /creator_slug/);
  assert.doesNotMatch(script, /author_id|source_trip_id|storage_path|review_reason/);
});
