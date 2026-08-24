const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.join(__dirname, "..", "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("the app opens account access without rendering an inline login popover", () => {
  const html = read("app/static/index.html");
  const source = read("app/static/app.js");

  assert.doesNotMatch(html, /id="account-menu"/);
  assert.doesNotMatch(html, /class="account-popover"/);
  assert.match(html, /id="account-entry"/);
  assert.match(source, /navigateToAuth\("signin"\)/);
});

test("the standalone account page provides a safe return action", () => {
  const html = read("app/static/auth.html");
  const source = read("app/static/auth.js");

  assert.match(html, /id="auth-page-back"/);
  assert.match(html, /返回/);
  assert.match(source, /return_to/);
  assert.match(source, /startsWith\("\/\/"\)/);
});

test("a standalone profile page exposes the requested account fields", () => {
  const html = read("app/static/profile.html");

  for (const id of [
    "profile-avatar-input",
    "profile-phone",
    "profile-email",
    "profile-display-name",
    "profile-password",
    "profile-save-button",
    "profile-password-button",
    "profile-back-link",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `${id} should be present`);
  }
});

test("the profile page persists editable identity fields through the auth client", () => {
  const source = read("app/static/profile.js");

  assert.match(source, /auth\.updateUser/);
  assert.match(source, /phone/);
  assert.match(source, /display_name/);
  assert.match(source, /avatar_url/);
  assert.match(source, /password/);
});

test("the server exposes the profile page directly", () => {
  const source = read("app/main.py");
  assert.match(source, /@app\.get\("\/profile"/);
  assert.match(source, /static" \/ "profile\.html/);
});
