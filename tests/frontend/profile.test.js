const assert = require("node:assert/strict");
const test = require("node:test");
const { FakeSupabaseAuth, createHarness, jsonResponse, settle } = require("./dom-harness");

const USER_A_ID = "11111111-1111-1111-1111-111111111111";
const USER_B_ID = "22222222-2222-2222-2222-222222222222";
const SESSION = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_at: 2000000000,
  user: { id: USER_A_ID, email: "owner@example.test" },
};
const SESSION_B = {
  access_token: "access-two",
  refresh_token: "refresh-two",
  expires_at: 2000000100,
  user: { id: USER_B_ID, email: "second@example.test" },
};

test("signed-out profile page redirects to auth with a fixed same-origin return_to", async () => {
  const harness = createHarness({ page: "profile" });

  await settle();

  assert.equal(harness.window.location.pathname, "/auth");
  assert.equal(harness.window.location.search, "?mode=signin&return_to=%2Fprofile");
});

test("profile page shows a retryable loading failure before populating the form", async () => {
  let requests = 0;
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async (call) => {
      assert.equal(call.url, "/api/profile");
      requests += 1;
      return requests === 1
        ? jsonResponse(503, { detail: { code: "PROFILE_UNAVAILABLE" } })
        : jsonResponse(200, {
          user_id: "11111111-1111-1111-1111-111111111111",
          email: "owner@example.test",
          display_name: "Voyage Alice",
          bio: "Loves noodles.",
          home_city: "Xiamen",
          travel_styles: ["美食", "自然"],
          updated_at: "2026-08-20T08:00:00Z",
        });
    },
  });

  await settle();

  assert.equal(harness.elements.get("profile-loading").hidden, true);
  assert.equal(harness.elements.get("profile-error").hidden, false);
  assert.equal(harness.elements.get("profile-form").hidden, true);

  await harness.elements.get("profile-retry-button").dispatch("click");
  await settle();

  assert.equal(requests, 2);
  assert.equal(harness.elements.get("profile-form").hidden, false);
  assert.equal(harness.elements.get("profile-email").value, "owner@example.test");
  assert.equal(harness.elements.get("profile-display-name").value, "Voyage Alice");
  assert.equal(harness.elements.get("travel-style-food").checked, true);
  assert.equal(harness.elements.get("travel-style-nature").checked, true);
});

test("profile save sends a full replacement payload from form values and reports success", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveSave;
  const saveResponse = new Promise((resolve) => { resolveSave = resolve; });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async (_call, index) => {
      if (index === 0) {
        return jsonResponse(200, {
          user_id: "11111111-1111-1111-1111-111111111111",
          email: "owner@example.test",
          display_name: "",
          bio: "",
          home_city: "",
          travel_styles: [],
          updated_at: null,
        });
      }
      return saveResponse;
    },
  });

  await settle();

  harness.elements.get("profile-display-name").value = "Voyage Alice";
  harness.elements.get("profile-bio").value = "Loves noodles.";
  harness.elements.get("profile-home-city").value = "Xiamen";
  harness.elements.get("travel-style-food").checked = true;
  harness.elements.get("travel-style-culture").checked = true;

  const saving = harness.elements.get("profile-form").dispatch("submit");
  await settle(1);

  assert.equal(harness.elements.get("profile-save-button").disabled, true);
  assert.deepEqual(JSON.parse(harness.fetchCalls[1].options.body), {
    display_name: "Voyage Alice",
    bio: "Loves noodles.",
    home_city: "Xiamen",
    travel_styles: ["美食", "人文"],
  });
  assert.equal(harness.fetchCalls[1].options.method, "PUT");
  assert.equal(harness.fetchCalls[1].options.headers.Authorization, "Bearer access-one");

  resolveSave(jsonResponse(200, {
    user_id: "11111111-1111-1111-1111-111111111111",
    email: "owner@example.test",
    display_name: "Voyage Alice",
    bio: "Loves noodles.",
    home_city: "Xiamen",
    travel_styles: ["美食", "人文"],
    updated_at: "2026-08-20T09:30:00Z",
  }));
  await saving;
  await settle();

  assert.equal(harness.elements.get("profile-save-button").disabled, false);
  assert.match(harness.elements.get("profile-status").textContent, /已保存/);
  assert.match(harness.elements.get("profile-updated-at").textContent, /2026-08-20/);
});

test("profile validation errors are rendered as user-visible feedback", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async (_call, index) => {
      if (index === 0) {
        return jsonResponse(200, {
          user_id: "11111111-1111-1111-1111-111111111111",
          email: "owner@example.test",
          display_name: "",
          bio: "",
          home_city: "",
          travel_styles: [],
          updated_at: null,
        });
      }
      return jsonResponse(422, {
        detail: [
          { loc: ["body", "display_name"], msg: "String should have at most 40 characters" },
          { loc: ["body", "travel_styles", 1], msg: "Input should be one of the allowed travel styles" },
        ],
      });
    },
  });

  await settle();

  harness.elements.get("profile-display-name").value = "x".repeat(41);
  harness.elements.get("travel-style-family").checked = true;
  harness.elements.get("travel-style-outdoor").checked = true;
  await harness.elements.get("profile-form").dispatch("submit");
  await settle();

  assert.equal(harness.elements.get("profile-errors").hidden, false);
  assert.match(harness.elements.get("profile-errors").textContent, /昵称/);
  assert.match(harness.elements.get("profile-status").textContent, /请检查/);
});

test("account switch clears A and ignores A load responses before loading B", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolvePendingA;
  let resolvePendingB;
  const pendingA = new Promise((resolve) => { resolvePendingA = resolve; });
  const pendingB = new Promise((resolve) => { resolvePendingB = resolve; });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async (_call, index) => {
      if (index === 0) {
        return jsonResponse(200, {
          user_id: USER_A_ID,
          email: "owner@example.test",
          display_name: "Alice",
          bio: "A bio",
          home_city: "厦门",
          travel_styles: ["美食"],
          updated_at: "2026-08-20T08:00:00Z",
        });
      }
      if (index === 1) return pendingA;
      if (index === 2) return pendingB;
      throw new Error(`unexpected profile request ${index}`);
    },
  });

  await settle();
  assert.equal(harness.elements.get("profile-display-name").value, "Alice");

  const staleLoad = harness.elements.get("profile-retry-button").dispatch("click");
  await settle(1);
  auth.emit("SIGNED_IN", SESSION_B);
  await settle(1);

  assert.equal(harness.elements.get("profile-display-name").value, "");
  assert.equal(harness.elements.get("profile-email").value, "");
  assert.equal(harness.elements.get("travel-style-food").checked, false);
  assert.equal(harness.fetchCalls.length, 3);
  assert.equal(harness.fetchCalls[1].options.headers.Authorization, "Bearer access-one");
  assert.equal(harness.fetchCalls[2].options.headers.Authorization, "Bearer access-two");

  resolvePendingA(jsonResponse(200, {
    user_id: USER_A_ID,
    email: "owner@example.test",
    display_name: "Stale Alice",
    bio: "stale",
    home_city: "杭州",
    travel_styles: ["自然"],
    updated_at: "2026-08-20T09:00:00Z",
  }));
  await staleLoad;
  await settle();

  assert.equal(harness.elements.get("profile-display-name").value, "");

  resolvePendingB(jsonResponse(200, {
    user_id: USER_B_ID,
    email: "second@example.test",
    display_name: "Bob",
    bio: "B bio",
    home_city: "泉州",
    travel_styles: ["人文"],
    updated_at: "2026-08-20T10:00:00Z",
  }));
  await settle();

  assert.equal(harness.elements.get("profile-display-name").value, "Bob");
  assert.equal(harness.elements.get("profile-email").value, "second@example.test");
  assert.equal(harness.elements.get("travel-style-food").checked, false);
  assert.equal(harness.elements.get("travel-style-culture").checked, true);
});

test("account switch invalidates A save and never submits A values with B token", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveSaveA;
  let resolveLoadB;
  const saveA = new Promise((resolve) => { resolveSaveA = resolve; });
  const loadB = new Promise((resolve) => { resolveLoadB = resolve; });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async (call, index) => {
      if (index === 0) {
        return jsonResponse(200, {
          user_id: USER_A_ID,
          email: "owner@example.test",
          display_name: "Alice",
          bio: "A bio",
          home_city: "厦门",
          travel_styles: ["美食"],
          updated_at: null,
        });
      }
      if (index === 1) return saveA;
      if (index === 2) return loadB;
      if (index === 3) {
        return jsonResponse(200, {
          user_id: USER_B_ID,
          email: "second@example.test",
          display_name: "Bob saved",
          bio: "B-only bio",
          home_city: "泉州",
          travel_styles: ["人文"],
          updated_at: "2026-08-20T11:00:00Z",
        });
      }
      throw new Error(`unexpected profile request ${index}: ${call.options.method}`);
    },
  });

  await settle();
  harness.elements.get("profile-display-name").value = "Alice pending";
  harness.elements.get("profile-bio").value = "A-only bio";
  const staleSave = harness.elements.get("profile-form").dispatch("submit");
  await settle(1);

  auth.emit("SIGNED_IN", SESSION_B);
  await settle(1);

  assert.equal(harness.fetchCalls[1].options.headers.Authorization, "Bearer access-one");
  assert.equal(harness.fetchCalls[2].options.headers.Authorization, "Bearer access-two");
  assert.equal(harness.elements.get("profile-display-name").value, "");
  await harness.elements.get("profile-form").dispatch("submit");
  await settle(1);
  assert.equal(harness.fetchCalls.length, 3);

  resolveLoadB(jsonResponse(200, {
    user_id: USER_B_ID,
    email: "second@example.test",
    display_name: "Bob",
    bio: "B bio",
    home_city: "泉州",
    travel_styles: ["人文"],
    updated_at: null,
  }));
  await settle();

  resolveSaveA(jsonResponse(200, {
    user_id: USER_A_ID,
    email: "owner@example.test",
    display_name: "Alice pending",
    bio: "A-only bio",
    home_city: "厦门",
    travel_styles: ["美食"],
    updated_at: "2026-08-20T10:30:00Z",
  }));
  await staleSave;
  await settle();

  assert.equal(harness.elements.get("profile-display-name").value, "Bob");
  assert.equal(harness.elements.get("profile-bio").value, "B bio");

  harness.elements.get("profile-display-name").value = "Bob saved";
  harness.elements.get("profile-bio").value = "B-only bio";
  await harness.elements.get("profile-form").dispatch("submit");
  await settle();

  assert.equal(harness.fetchCalls[3].options.headers.Authorization, "Bearer access-two");
  assert.deepEqual(JSON.parse(harness.fetchCalls[3].options.body), {
    display_name: "Bob saved",
    bio: "B-only bio",
    home_city: "泉州",
    travel_styles: ["人文"],
  });
  assert.equal(harness.elements.get("profile-display-name").value, "Bob saved");
});

test("profile return navigation accepts same-origin paths and rejects external targets", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const validHarness = createHarness({
    page: "profile",
    auth,
    search: "?return_to=%2Fcommunity%3Fcursor%3Dabc",
    fetch: async () => jsonResponse(200, {
      user_id: "11111111-1111-1111-1111-111111111111",
      email: "owner@example.test",
      display_name: "",
      bio: "",
      home_city: "",
      travel_styles: [],
      updated_at: null,
    }),
  });

  await settle();

  assert.equal(validHarness.elements.get("profile-back-link").href, "/community?cursor=abc");
  await validHarness.elements.get("profile-back-link").dispatch("click");
  assert.equal(validHarness.window.location.pathname, "/community");
  assert.equal(validHarness.window.location.search, "?cursor=abc");

  const invalidHarness = createHarness({
    page: "profile",
    auth,
    search: "?return_to=https%3A%2F%2Fevil.example%2Fsteal",
    fetch: async () => jsonResponse(200, {
      user_id: "11111111-1111-1111-1111-111111111111",
      email: "owner@example.test",
      display_name: "",
      bio: "",
      home_city: "",
      travel_styles: [],
      updated_at: null,
    }),
  });

  await settle();

  assert.equal(invalidHarness.elements.get("profile-back-link").href, "/");
});

test("profile page renders initials fallback when no avatar_url is available", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "profile",
    auth,
    fetch: async () => jsonResponse(200, {
      user_id: USER_A_ID,
      email: "owner@example.test",
      display_name: "Voyage Alice",
      bio: "",
      home_city: "",
      travel_styles: [],
      avatar_url: null,
      updated_at: null,
    }),
  });

  await settle();

  assert.equal(harness.elements.get("profile-avatar-fallback").hidden, false);
  assert.equal(harness.elements.get("profile-avatar-fallback").textContent, "VA");
  assert.equal(harness.elements.get("profile-avatar-image").hidden, true);
});

test("profile save compresses and uploads a new avatar before PUT", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const uploads = [];
  const storage = {
    from_(bucket) {
      return {
        async upload(path, file, options) {
          uploads.push({ bucket, path, file, options });
          return { data: { path }, error: null };
        },
        async remove(paths) {
          uploads.push({ removed: paths });
          return { data: paths, error: null };
        },
      };
    },
  };
  const harness = createHarness({
    page: "profile",
    auth,
    storage,
    createImageBitmap: async () => ({
      width: 1600,
      height: 1200,
      close() {},
    }),
    createCanvas: () => ({
      width: 0,
      height: 0,
      getContext() {
        return { drawImage() {} };
      },
      toBlob(callback, type) {
        callback(new Blob(["compressed-avatar"], { type }));
      },
    }),
    fetch: async (_call, index) => {
      if (index === 0) {
        return jsonResponse(200, {
          user_id: USER_A_ID,
          email: "owner@example.test",
          display_name: "Voyage Alice",
          bio: "",
          home_city: "",
          travel_styles: [],
          avatar_url: null,
          updated_at: null,
        });
      }
      return jsonResponse(200, {
        user_id: USER_A_ID,
        email: "owner@example.test",
        display_name: "Voyage Alice",
        bio: "",
        home_city: "",
        travel_styles: [],
        avatar_url: `https://signed.example.test/${USER_A_ID}/avatar/thread-1.webp`,
        updated_at: "2026-08-21T09:30:00Z",
      });
    },
  });

  await settle();

  const input = harness.elements.get("profile-avatar-input");
  input.files = [{ name: "avatar.jpg", type: "image/jpeg", size: 4096 }];
  await input.dispatch("change");
  await settle();

  assert.equal(harness.elements.get("profile-avatar-image").hidden, false);
  assert.equal(harness.elements.get("profile-avatar-image").src, "blob:mock-1");
  assert.equal(harness.elements.get("profile-avatar-fallback").hidden, true);

  await harness.elements.get("profile-form").dispatch("submit");
  await settle();

  assert.equal(uploads.length > 0, true);
  assert.equal(uploads[0].bucket, "community-media");
  assert.equal(uploads[0].path, `${USER_A_ID}/avatar/thread-1.webp`);
  assert.equal(uploads[0].file.type, "image/webp");
  assert.equal(uploads[0].options.contentType, "image/webp");
  assert.equal(uploads[0].options.upsert, false);
  assert.deepEqual(JSON.parse(harness.fetchCalls[1].options.body), {
    display_name: "Voyage Alice",
    bio: "",
    home_city: "",
    travel_styles: [],
    avatar_path: `${USER_A_ID}/avatar/thread-1.webp`,
  });
  assert.deepEqual(harness.revokedObjectUrls, ["blob:mock-1"]);
});
