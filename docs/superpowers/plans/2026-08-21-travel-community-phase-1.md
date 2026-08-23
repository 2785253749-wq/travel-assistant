# Voyage Travel Community Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-ready version of Voyage's image-first travel-note community with moderated publishing, masonry discovery, media storage, likes, bookmarks, moderated comments, reports, creator pages, and an administrator review queue.

**Architecture:** Add a new `app/travel_notes` domain and migration 011 rather than widening the legacy itinerary-snapshot `community_posts` model. Public reads use explicit approved-content projections, authenticated mutations use the caller's Supabase JWT, media lives in a private Storage bucket, and administrator transitions are enforced by both API dependencies and database RPCs. New community pages are separate static documents and scripts so the already-large `app/static/app.js` does not absorb another subsystem.

**Tech Stack:** FastAPI, Pydantic v2, Supabase Postgres/Auth/Storage/RLS/RPC, plain JavaScript, HTML/CSS, Node's built-in test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-travel-community-design.md`

## Global Constraints

- Implement phase one only; following, notifications, private messages, personalized ranking, video, rich text, free tags, and AI moderation are excluded.
- Tasks execute in numeric order and are not implemented in parallel.
- 阶段一按顺序实现瀑布流、点赞、收藏、评论、举报和双账户验收；阶段二不在本计划实现且不得并行。
- Keep migration 010 and legacy `community_posts` data intact; the new UI must not call `/api/community/posts`.
- A travel note has 1–9 JPEG, PNG, or WebP images, a 1–60 character title, a 1–5000 character plain-text body, a 1–80 character location, and exactly one approved category.
- Categories are `摄影控`, `美食地图`, `独自旅行`, `城市漫步`, `自然风光`, and `亲子游`.
- Public feeds contain approved, non-deleted content only and sort by `published_at desc, id desc`.
- Travel notes and comments are invisible to other users until an administrator approves them.
- Public payloads and logs must not expose email, author UUID, private trip ID, original Storage path, access token, review notes, or private itinerary fields.
- User-generated text is rendered with safe DOM APIs, never untrusted `innerHTML`.
- Write tests first for every behavior and preserve all unrelated dirty-worktree files.

---

### Task 1: Define travel-note contracts and cursor semantics

**Files:**
- Create: `app/travel_notes/__init__.py`
- Create: `app/travel_notes/models.py`
- Create: `tests/unit/test_travel_note_models.py`

**Interfaces:**
- Consumes: `app.schemas.StrictSchema` and standard `UUID`/`datetime` types.
- Produces: `TravelNoteCategory`, `TravelNoteStatus`, `ReviewTargetType`, `TravelNoteImageInput`, `TravelNoteDraftInput`, `TravelNoteCard`, `TravelNoteDetail`, `TravelNoteOwnerView`, `TravelNotePage`, `TravelNoteComment`, `encode_travel_note_cursor()`, and `decode_travel_note_cursor()`.

- [ ] **Step 1: Write failing model tests**

```python
def test_draft_requires_one_category_and_one_to_nine_owned_images():
    valid = {
        "title": "大理四天三夜",
        "body": "苍山脚下散步，傍晚去洱海看日落。",
        "location_name": "云南·大理",
        "category": "城市漫步",
        "source_trip_id": None,
        "images": [{
            "storage_path": "user-a/note-a/cover.webp",
            "sort_order": 0,
            "width": 1440,
            "height": 1920,
        }],
    }
    assert TravelNoteDraftInput.model_validate(valid).category == "城市漫步"
    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate({**valid, "images": []})
    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate({**valid, "category": "随便逛逛"})


def test_public_card_forbids_private_fields():
    payload = public_card_payload()
    assert TravelNoteCard.model_validate(payload).title == "大理四天三夜"
    for field in ("author_id", "source_trip_id", "storage_path", "review_reason"):
        with pytest.raises(ValidationError):
            TravelNoteCard.model_validate({**payload, field: "private"})
```

- [ ] **Step 2: Run the tests and verify the red state**

Run: `python -m pytest tests/unit/test_travel_note_models.py -q`
Expected: FAIL because `app.travel_notes.models` does not exist.

- [ ] **Step 3: Implement strict contracts and opaque cursor helpers**

```python
TravelNoteCategory = Literal[
    "摄影控", "美食地图", "独自旅行", "城市漫步", "自然风光", "亲子游"
]
TravelNoteStatus = Literal["draft", "pending_review", "approved", "rejected"]


class TravelNoteImageInput(StrictSchema):
    storage_path: str = Field(min_length=5, max_length=500)
    sort_order: int = Field(ge=0, le=8)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)


class TravelNoteDraftInput(StrictSchema):
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=5000)
    location_name: str = Field(min_length=1, max_length=80)
    category: TravelNoteCategory
    source_trip_id: UUID | None = None
    images: list[TravelNoteImageInput] = Field(min_length=1, max_length=9)
```

Normalize surrounding whitespace, reject duplicate `sort_order` values, and encode cursors as URL-safe base64 of `published_at|id` using the same stable pattern as `app/community/models.py`.

- [ ] **Step 4: Run model tests**

Run: `python -m pytest tests/unit/test_travel_note_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the contract slice**

```bash
git add app/travel_notes/__init__.py app/travel_notes/models.py tests/unit/test_travel_note_models.py
git commit -m "feat: define travel note contracts"
```

---

### Task 2: Add migration 011, RLS, moderation roles, and Storage policies

**Files:**
- Create: `supabase/migrations/011_travel_note_community.sql`
- Create: `tests/integration/test_travel_note_sql_contract.py`
- Modify: `tests/integration/test_rls_contract.py`

**Interfaces:**
- Consumes: category/status names from Task 1 and existing `profiles`, `trips`, and `set_updated_at()` database objects.
- Produces: `travel_notes`, `travel_note_images`, `travel_note_likes`, `travel_note_bookmarks`, `travel_note_comments`, `travel_note_reports`, `moderation_decisions`, `user_roles`, `community_media_cleanup_jobs`, the private `community-media` bucket, and approved-content RPCs.

- [ ] **Step 1: Write SQL contract tests before the migration**

```python
def test_travel_notes_are_separate_from_legacy_snapshots():
    sql = migration_011()
    assert "create table public.travel_notes" in sql
    assert "alter table public.community_posts" not in sql
    assert "status text not null default 'draft'" in sql
    assert "published_at timestamptz" in sql
    assert "deleted_at timestamptz" in sql


def test_public_rpcs_only_return_approved_projection():
    sql = migration_011()
    listing = function_block(sql, "list_public_travel_notes_internal")
    assert "status = 'approved'" in listing
    assert "deleted_at is null" in listing
    for forbidden in ("author_id", "source_trip_id", "review_reason"):
        assert forbidden not in returns_table_columns(listing)
    assert "grant execute" in sql
    assert "list_public_travel_notes_internal" in sql
    assert "to service_role" in sql


def test_storage_objects_are_owner_scoped():
    sql = migration_011()
    assert "insert into storage.buckets" in sql
    assert "community-media" in sql
    assert "(storage.foldername(name))[1] = auth.uid()::text" in sql
```

- [ ] **Step 2: Run SQL tests and verify failure**

Run: `python -m pytest tests/integration/test_travel_note_sql_contract.py tests/integration/test_rls_contract.py -q`
Expected: FAIL because migration 011 and the new private-table declarations are absent.

- [ ] **Step 3: Create the migration with explicit constraints**

```sql
create table public.travel_notes (
  id uuid primary key default gen_random_uuid(),
  author_id uuid not null references auth.users(id) on delete cascade,
  source_trip_id uuid references public.trips(id) on delete set null,
  itinerary_snapshot jsonb,
  title text not null check (char_length(btrim(title)) between 1 and 60),
  body text not null check (char_length(btrim(body)) between 1 and 5000),
  location_name text not null check (char_length(btrim(location_name)) between 1 and 80),
  category text not null check (category in ('摄影控','美食地图','独自旅行','城市漫步','自然风光','亲子游')),
  status text not null default 'draft' check (status in ('draft','pending_review','approved','rejected')),
  review_reason text check (review_reason is null or char_length(review_reason) between 1 and 500),
  submitted_at timestamptz,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
```

Create all tables named in the interface block with foreign keys, unique interaction keys, timestamps, soft-delete fields where required, and indexes for feed order, owner/status listings, review queues, and comment order. `submit_travel_note` copies a validated, public-safe itinerary snapshot from an owned planned trip when `source_trip_id` is present; the public API never returns the private trip ID. Extend `profiles` with a unique non-null `creator_slug` generated from fresh random bytes rather than the user UUID, plus nullable `avatar_path`, preserving existing rows.

- [ ] **Step 4: Add least-privilege RLS and RPCs**

Create owner policies for drafts/images/bookmarks, append-only authenticated mutations for likes/comments/reports, service-controlled role and audit tables, and fixed-`search_path` RPCs:

```sql
public.list_public_travel_notes_internal(cursor_published_at timestamptz, cursor_id uuid, page_size integer, category_filter text, search_query text)
public.get_public_travel_note_internal(note_id uuid)
public.submit_travel_note(note_id uuid)
public.review_travel_note(note_id uuid, decision text, reason text)
public.review_travel_note_comment(comment_id uuid, decision text, reason text)
public.is_community_admin()
```

Revoke base-table access from `public` and `anon`. Grant owner mutation RPCs to `authenticated`, administrator RPCs to authenticated callers with an internal role check, and the two approved-content internal RPCs only to `service_role`. Anonymous browsers read through FastAPI; they never receive a direct Supabase RPC capable of returning Storage paths.

- [ ] **Step 5: Add the private Storage bucket and object policies**

```sql
insert into storage.buckets (id, name, public)
values ('community-media', 'community-media', false)
on conflict (id) do update set public = false;

create policy "users upload own community media"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'community-media'
  and (storage.foldername(name))[1] = auth.uid()::text
);
```

Add equivalent owner-scoped select/update/delete policies. Do not grant anonymous Storage reads.

- [ ] **Step 6: Extend the global RLS audit and run tests**

Add every new private table to the appropriate owner/service-role collection in `test_rls_contract.py`, and explicitly audit the `profiles` column additions.

Run: `python -m pytest tests/integration/test_travel_note_sql_contract.py tests/integration/test_rls_contract.py -q`
Expected: PASS.

- [ ] **Step 7: Commit the database slice**

```bash
git add supabase/migrations/011_travel_note_community.sql tests/integration/test_travel_note_sql_contract.py tests/integration/test_rls_contract.py
git commit -m "feat: add moderated travel note schema"
```

---

### Task 3: Implement the travel-note lifecycle service

**Files:**
- Create: `app/travel_notes/service.py`
- Create: `app/travel_notes/repositories.py`
- Create: `tests/unit/test_travel_note_service.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces: `TravelNoteRepository`, `PublicTravelNoteRepository`, `TravelNoteModule.create_draft()`, `replace_draft()`, `submit()`, `soft_delete()`, `list_mine()`, `list_public()`, and `get_public()`.

- [ ] **Step 1: Write lifecycle tests with an in-memory repository**

```python
def test_author_can_create_replace_and_submit_a_complete_draft():
    module = TravelNoteModule(InMemoryTravelNoteRepository())
    created = module.create_draft(USER_A, draft_input())
    replaced = module.replace_draft(USER_A, created.id, draft_input(title="新的标题"))
    submitted = module.submit(USER_A, created.id)
    assert replaced.title == "新的标题"
    assert submitted.status == "pending_review"


def test_cross_user_mutation_is_indistinguishable_from_missing():
    module = TravelNoteModule(InMemoryTravelNoteRepository())
    created = module.create_draft(USER_A, draft_input())
    with pytest.raises(AppError) as error:
        module.replace_draft(USER_B, created.id, draft_input())
    assert error.value.code == "TRAVEL_NOTE_NOT_FOUND"
```

Cover invalid image ownership prefixes, source-trip ownership, edits to pending content, resubmission of rejected content, soft deletion, cursor pagination, category filtering, and normalized search.

- [ ] **Step 2: Run service tests and verify failure**

Run: `python -m pytest tests/unit/test_travel_note_service.py -q`
Expected: FAIL because lifecycle classes do not exist.

- [ ] **Step 3: Implement a narrow module over repository protocols**

```python
class TravelNoteModule:
    def create_draft(self, user_id: UUID, value: TravelNoteDraftInput) -> TravelNoteOwnerView:
        self._validate_owner_paths(user_id, value.images)
        return self._repository.create_draft(user_id, value)

    def submit(self, user_id: UUID, note_id: UUID) -> TravelNoteOwnerView:
        note = self._repository.get_owned(user_id, note_id)
        if note is None:
            raise AppError("TRAVEL_NOTE_NOT_FOUND", "Travel note not found")
        if note.status not in {"draft", "rejected"}:
            raise AppError("TRAVEL_NOTE_INVALID_STATE", "Travel note state is invalid")
        return self._repository.submit(user_id, note_id)

    def soft_delete(self, user_id: UUID, note_id: UUID) -> None:
        if not self._repository.soft_delete(user_id, note_id):
            raise AppError("TRAVEL_NOTE_NOT_FOUND", "Travel note not found")
```

Implement `replace_draft`, `list_mine`, `list_public`, and `get_public` with the exact signatures in the Interfaces block. Normalize text in the model, keep authorization errors as stable `TRAVEL_NOTE_NOT_FOUND`, and keep owner/public result models separate.

- [ ] **Step 4: Run lifecycle tests**

Run: `python -m pytest tests/unit/test_travel_note_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the lifecycle slice**

```bash
git add app/travel_notes/service.py app/travel_notes/repositories.py tests/unit/test_travel_note_service.py
git commit -m "feat: add travel note lifecycle"
```

---

### Task 4: Add Supabase repositories and production wiring

**Files:**
- Modify: `app/travel_notes/repositories.py`
- Modify: `app/composition.py`
- Create: `tests/unit/test_travel_note_production_wiring.py`
- Create: `tests/unit/test_travel_note_repositories.py`

**Interfaces:**
- Consumes: Task 3 repository protocols and current `AuthenticatedUser.access_token`.
- Produces: `SupabaseTravelNoteRepository`, `SupabasePublicTravelNoteRepository`, `create_user_scoped_travel_note_repository(url, anon_key, access_token)`, `create_public_travel_note_repository(url, service_key)`, `get_travel_note_module()`, and `get_optional_travel_note_module()`.

- [ ] **Step 1: Write failing adapter and wiring tests**

```python
def test_user_scoped_repository_applies_the_bearer_token(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr("supabase.create_client", lambda url, key: fake)
    create_user_scoped_travel_note_repository("https://project.test", "anon", "token-a")
    assert fake.postgrest.tokens == ["token-a"]


def test_public_repository_uses_service_key_and_only_calls_internal_read_rpcs():
    repository = SupabasePublicTravelNoteRepository(FakeSupabaseClient())
    repository.list_notes(None, 21, None, None)
    assert repository._client.rpc_names == ["list_public_travel_notes_internal"]
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/unit/test_travel_note_repositories.py tests/unit/test_travel_note_production_wiring.py -q`
Expected: FAIL because concrete adapters and dependencies are absent.

- [ ] **Step 3: Implement adapters with stable row mapping**

Use authenticated table/RPC calls for owner actions and a server-only service-key repository for internal approved-content reads. The HTTP layer maps internal Storage paths to signed URLs before validating a public response model. Wrap operations in `database_operation()` with hashed subjects and map PostgreSQL errors to the stable codes `TRAVEL_NOTE_NOT_FOUND`, `TRAVEL_NOTE_INVALID_STATE`, `TRAVEL_NOTE_VALIDATION_FAILED`, and `TRAVEL_NOTE_UNAVAILABLE` without returning provider messages.

- [ ] **Step 4: Wire development and production composition**

```python
def get_travel_note_module(user: CurrentUser) -> TravelNoteModule:
    if not _uses_supabase():
        return get_development_travel_note_module()
    if not user.access_token:
        raise RuntimeError("A verified bearer token is required for travel note access")
    return TravelNoteModule(
        create_user_scoped_travel_note_repository(url, anon_key, user.access_token),
        get_public_travel_note_repository(service_key),
    )
```

Keep caches scoped like the existing community/profile modules and clear account-sensitive state on session changes.

- [ ] **Step 5: Run adapter and wiring tests**

Run: `python -m pytest tests/unit/test_travel_note_repositories.py tests/unit/test_travel_note_production_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Commit the adapter slice**

```bash
git add app/travel_notes/repositories.py app/composition.py tests/unit/test_travel_note_repositories.py tests/unit/test_travel_note_production_wiring.py
git commit -m "feat: wire travel note repositories"
```

---

### Task 5: Implement media URLs, avatar metadata, and cleanup jobs

**Files:**
- Create: `app/travel_notes/media.py`
- Create: `app/scripts/cleanup_community_media.py`
- Modify: `app/composition.py`
- Modify: `app/profile/models.py`
- Modify: `app/profile/repositories.py`
- Modify: `app/profile/service.py`
- Modify: `app/api/profile.py`
- Modify: `app/static/profile.html`
- Modify: `app/static/profile.js`
- Create: `tests/unit/test_community_media.py`
- Modify: `tests/integration/test_profile_api.py`
- Modify: `tests/frontend/profile.test.js`

**Interfaces:**
- Consumes: private `community-media` paths from Task 2 and service-key Supabase client creation from `app/composition.py`.
- Produces: `CommunityMediaGateway.sign_paths()`, `enqueue_cleanup()`, `run_cleanup_batch()`, optional `avatar_path` profile input, and public `avatar_url` output.

- [ ] **Step 1: Write media and avatar contract tests**

```python
def test_sign_paths_preserves_order_without_exposing_storage_paths():
    storage = FakeStorage({"a.webp": "https://signed/a", "b.webp": "https://signed/b"})
    gateway = CommunityMediaGateway(storage, bucket="community-media")
    assert gateway.sign_paths(["a.webp", "b.webp"], expires_in=3600) == [
        "https://signed/a", "https://signed/b"
    ]


def test_profile_response_returns_avatar_url_not_avatar_path(client):
    response = client.get("/api/profile", headers=auth_headers())
    assert "avatar_url" in response.json()
    assert "avatar_path" not in response.json()
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/unit/test_community_media.py tests/integration/test_profile_api.py -q`
Expected: FAIL because media signing and avatar fields are absent.

- [ ] **Step 3: Implement private URL signing and cleanup queue processing**

```python
class CommunityMediaGateway:
    def sign_paths(self, paths: list[str], *, expires_in: int = 3600) -> list[str]:
        return [
            self._storage.create_signed_url(path, expires_in)["signedURL"]
            for path in paths
        ]

    def enqueue_cleanup(self, owner_id: UUID, paths: list[str]) -> None:
        self._cleanup_repository.enqueue(owner_id, paths)

    def run_cleanup_batch(self, *, limit: int = 100) -> int:
        completed = 0
        for job in self._cleanup_repository.claim_pending(limit):
            self._storage.remove(job.paths)
            self._cleanup_repository.mark_completed(job.id)
            completed += 1
        return completed
```

The cleanup script loads production settings, processes pending rows from `community_media_cleanup_jobs`, removes objects with the service client, marks successes, and leaves failures pending with a bounded attempt counter and sanitized log entry.

- [ ] **Step 4: Add optional avatar metadata without exposing paths**

Extend profile input/storage with `avatar_path` validation restricted to `{user_id}/avatar/`, but return only a signed `avatar_url`. Add an avatar file input and preview to `profile.html`; `profile.js` compresses and uploads the image to the authenticated user's avatar directory before saving the returned path. When no avatar exists, frontend pages render initials from `display_name`.

- [ ] **Step 5: Run media and profile tests**

Run: `python -m pytest tests/unit/test_community_media.py tests/integration/test_profile_api.py -q`
Run: `node --test tests/frontend/profile.test.js`
Expected: PASS.

- [ ] **Step 6: Commit the media slice**

```bash
git add app/travel_notes/media.py app/scripts/cleanup_community_media.py app/composition.py app/profile app/api/profile.py app/static/profile.html app/static/profile.js tests/unit/test_community_media.py tests/integration/test_profile_api.py tests/frontend/profile.test.js
git commit -m "feat: add private community media handling"
```

---

### Task 6: Expose core travel-note and page routes

**Files:**
- Create: `app/api/travel_notes.py`
- Modify: `app/main.py`
- Create: `tests/integration/test_travel_note_api.py`
- Modify: `tests/integration/test_frontend_assets.py`

**Interfaces:**
- Consumes: Task 3 module and Task 4 dependencies.
- Produces: public/owner endpoints and static page routes for feed, detail, editor, mine, creator, and admin pages.

- [ ] **Step 1: Write failing HTTP contract tests**

```python
def test_anonymous_feed_uses_approved_projection(client):
    response = client.get("/api/community/notes?limit=20&category=城市漫步&q=大理")
    assert response.status_code == 200
    assert set(response.json()) == {"items", "next_cursor"}


def test_owner_draft_lifecycle_requires_authentication(client):
    assert client.post("/api/community/notes", json=draft_payload()).status_code == 401
    created = client.post("/api/community/notes", json=draft_payload(), headers=auth_headers())
    assert created.status_code == 201
    submitted = client.post(
        f"/api/community/notes/{created.json()['id']}/submit", headers=auth_headers()
    )
    assert submitted.json()["status"] == "pending_review"
```

Cover update, soft delete, mine-by-status, invalid cursor, unsupported category, query limits, private-field rejection, and stable 404/409/422/503 shapes.

- [ ] **Step 2: Run HTTP tests and verify failure**

Run: `python -m pytest tests/integration/test_travel_note_api.py tests/integration/test_frontend_assets.py -q`
Expected: FAIL because router and page routes are missing.

- [ ] **Step 3: Implement the core router**

```python
GET    /api/community/notes
GET    /api/community/notes/{note_id}
POST   /api/community/notes
PUT    /api/community/notes/{note_id}
POST   /api/community/notes/{note_id}/submit
DELETE /api/community/notes/{note_id}
GET    /api/me/travel-notes
```

Map all application errors to stable Chinese-safe response codes. Accept optional auth on public routes so the service can add `liked`, `bookmarked`, and owner capabilities without exposing ownership identifiers.

- [ ] **Step 4: Add static page routes without a catch-all SPA**

```python
@app.get("/community", include_in_schema=False)
def community_page(): return FileResponse(BASE / "static" / "community.html")

@app.get("/community/notes/new", include_in_schema=False)
def community_editor_page(): return FileResponse(BASE / "static" / "community-editor.html")

@app.get("/community/notes/{note_id}", include_in_schema=False)
def community_note_page(note_id: UUID): return FileResponse(BASE / "static" / "community-note.html")
```

Add routes for `/community/mine`, `/community/creators/{creator_slug}`, `/community/notes/{note_id}/edit`, and `/admin/community`. Include the new API router before page routes where ordering matters.

- [ ] **Step 5: Run API and asset tests**

Run: `python -m pytest tests/integration/test_travel_note_api.py tests/integration/test_frontend_assets.py -q`
Expected: PASS.

- [ ] **Step 6: Commit the API slice**

```bash
git add app/api/travel_notes.py app/main.py tests/integration/test_travel_note_api.py tests/integration/test_frontend_assets.py
git commit -m "feat: expose travel note routes"
```

---

### Task 7: Build the feed shell and responsive masonry discovery page

**Files:**
- Create: `app/static/community.html`
- Create: `app/static/community-client.js`
- Create: `app/static/community-feed.js`
- Create: `app/static/community.css`
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Create: `tests/frontend/community-feed.test.js`
- Modify: `tests/frontend/dom-harness.js`
- Modify: `tests/frontend/community.test.js`

**Interfaces:**
- Consumes: Task 6 public feed endpoint and existing runtime Supabase config.
- Produces: `window.VoyageCommunityClient`, safe card rendering, category/search state, load-more pagination, and `/community` navigation.

- [ ] **Step 1: Write failing feed tests**

```javascript
test("feed renders approved notes as safe masonry cards", async () => {
  const harness = createCommunityHarness({
    fetch: async () => jsonResponse(200, travelNotePage([travelNoteCard()])),
  });
  await settle();
  assert.match(harness.document.getElementById("community-grid").textContent, /大理四天三夜/);
  assert.equal(harness.document.querySelectorAll(".travel-note-card").length, 1);
  assert.equal(harness.unsafeInnerHtmlWrites.length, 0);
});


test("category search resets the cursor and encodes filters", async () => {
  const harness = createCommunityHarness({
    fetch: async () => jsonResponse(200, travelNotePage([])),
  });
  await harness.elements.get("category-city-walk").dispatch("click");
  harness.elements.get("community-search-input").value = "大理";
  await harness.elements.get("community-search-form").dispatch("submit");
  const requested = new URL(harness.fetchCalls.at(-1).url, "https://voyage.test");
  assert.equal(requested.searchParams.get("category"), "城市漫步");
  assert.equal(requested.searchParams.get("q"), "大理");
  assert.equal(requested.searchParams.get("cursor"), null);
});
```

Also test anonymous read, signed-out interaction redirect, stale-request cancellation, empty/error/retry states, and load-more append behavior.

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `node --test tests/frontend/community-feed.test.js`
Expected: FAIL because community page assets are absent.

- [ ] **Step 3: Add the standalone community shell**

Use semantic header/nav/main/footer markup, a search form, category buttons, `#community-grid`, `#community-load-more`, status region, and floating create link. Load runtime config, pinned Supabase JS, `community-client.js`, then `community-feed.js`.

- [ ] **Step 4: Implement card rendering and request generations**

```javascript
function renderTravelNoteCard(note) {
  const card = document.createElement("article");
  card.className = "travel-note-card";
  const image = document.createElement("img");
  image.src = note.cover_image_url;
  image.alt = note.title;
  image.loading = "lazy";
  card.append(image, buildCardCopy(note));
  return card;
}
```

Use `textContent`, URL validation, optional-auth headers, and a monotonically increasing request generation so stale search/account responses cannot overwrite current state.

- [ ] **Step 5: Implement responsive masonry styling**

Use CSS columns for the irregular-height feed, `break-inside: avoid`, four columns above 1200 px, two columns from 700–1199 px, and one column below 700 px. Preserve keyboard focus, reduced-motion preferences, and visible button labels.

- [ ] **Step 6: Retire the old embedded community UI**

Change the home navigation's Community action to `/community`, remove the old three-panel community section and its event/state code from `app.js`, and update legacy frontend tests to assert navigation instead of rendering `/api/community/posts`. Do not delete the legacy backend API or migration.

- [ ] **Step 7: Run frontend and syntax tests**

Run: `node --test tests/frontend/community-feed.test.js tests/frontend/community.test.js tests/frontend/app.test.js`
Run: `node --check app/static/community-client.js`
Run: `node --check app/static/community-feed.js`
Expected: all PASS.

- [ ] **Step 8: Commit the feed slice**

```bash
git add app/static/community.html app/static/community-client.js app/static/community-feed.js app/static/community.css app/static/index.html app/static/app.js tests/frontend
git commit -m "feat: add travel community masonry feed"
```

---

### Task 8: Build draft editor, client-side image preparation, and My Notes

**Files:**
- Create: `app/static/community-editor.html`
- Create: `app/static/community-editor.js`
- Create: `app/static/community-mine.html`
- Create: `app/static/community-mine.js`
- Modify: `app/static/community.css`
- Create: `tests/frontend/community-editor.test.js`
- Create: `tests/frontend/community-mine.test.js`
- Modify: `tests/frontend/dom-harness.js`

**Interfaces:**
- Consumes: Task 6 owner endpoints, private `community-media` bucket, and `VoyageCommunityClient` from Task 7.
- Produces: `prepareCommunityImage(file)`, ordered image manifests, draft create/update/submit flows, unsaved-change guard, and owner-status listings.

- [ ] **Step 1: Write failing editor tests**

```javascript
test("editor rejects unsupported or tenth image before upload", async () => {
  const harness = createEditorHarness({ session: SESSION });
  await harness.addFiles([pngFile(), ...eightWebpFiles(), jpegFile()]);
  assert.match(harness.elements.get("editor-errors").textContent, /最多上传 9 张/);
  assert.equal(harness.storageUploads.length, 0);
});


test("save creates a draft before uploading owner-scoped paths", async () => {
  const harness = createEditorHarness({ session: SESSION });
  await harness.fillValidDraft();
  await harness.saveDraft();
  assert.equal(harness.fetchCalls[0].url, "/api/community/notes");
  assert.equal(harness.fetchCalls[0].options.method, "POST");
  assert.match(harness.storageUploads[0].path, /^user-a\/[0-9a-f-]+\/[0-9a-f-]+[.]webp$/);
  assert.equal(harness.fetchCalls.at(-1).options.method, "PUT");
  const manifest = JSON.parse(harness.fetchCalls.at(-1).options.body).images;
  assert.deepEqual(manifest.map((image) => image.sort_order), [0]);
});
```

Cover image reorder, preview, draft save, submit, rejected-note resubmission, auth redirect, disabled duplicate submit, upload failure cleanup, and before-unload warning.

- [ ] **Step 2: Run editor tests and verify failure**

Run: `node --test tests/frontend/community-editor.test.js tests/frontend/community-mine.test.js`
Expected: FAIL because editor/mine assets are absent.

- [ ] **Step 3: Implement browser image preparation**

```javascript
async function prepareCommunityImage(file) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("UNSUPPORTED_IMAGE");
  if (file.size > 10 * 1024 * 1024) throw new Error("IMAGE_TOO_LARGE");
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
  return canvasToWebp(bitmap, scale, 0.82);
}
```

Create a 720 px-wide cover variant, preserve dimensions, and revoke every temporary object URL after use.

- [ ] **Step 4: Implement sequential draft and upload flow**

Create the draft first to obtain `note_id`, upload each image to `{user_id}/{note_id}/{image_id}.webp`, then update the draft with the ordered manifest. On partial failure, remove objects uploaded during that attempt and leave the draft editable.

- [ ] **Step 5: Implement My Notes status views**

Render tabs for draft, pending, approved, and rejected. Show review reason only to the owner, provide edit/delete actions allowed by status, and invalidate stale responses on account changes.

- [ ] **Step 6: Run editor, mine, and syntax tests**

Run: `node --test tests/frontend/community-editor.test.js tests/frontend/community-mine.test.js`
Run: `node --check app/static/community-editor.js`
Run: `node --check app/static/community-mine.js`
Expected: all PASS.

- [ ] **Step 7: Commit the authoring slice**

```bash
git add app/static/community-editor.html app/static/community-editor.js app/static/community-mine.html app/static/community-mine.js app/static/community.css tests/frontend
git commit -m "feat: add travel note authoring flow"
```

---

### Task 9: Add public detail and creator pages

**Files:**
- Create: `app/static/community-note.html`
- Create: `app/static/community-note.js`
- Create: `app/static/community-creator.html`
- Create: `app/static/community-creator.js`
- Modify: `app/api/travel_notes.py`
- Modify: `app/travel_notes/service.py`
- Modify: `app/travel_notes/repositories.py`
- Create: `tests/frontend/community-note.test.js`
- Create: `tests/frontend/community-creator.test.js`
- Modify: `tests/integration/test_travel_note_api.py`

**Interfaces:**
- Consumes: Task 6 note detail and Task 5 signed avatar/image URLs.
- Produces: `GET /api/community/creators/{creator_slug}`, accessible gallery/detail rendering, optional itinerary attachment projection, and read-only creator profile.

- [ ] **Step 1: Write failing detail and creator tests**

```javascript
test("detail renders gallery and plain text without private fields", async () => {
  const harness = createNoteHarness({ response: travelNoteDetail() });
  await settle();
  assert.equal(harness.elements.get("note-gallery").querySelectorAll("img").length, 3);
  assert.match(harness.elements.get("note-body").textContent, /苍山脚下/);
  assert.equal(harness.unsafeInnerHtmlWrites.length, 0);
});
```

Add API tests proving creator lookup returns only public profile fields and approved cards, and that optional itinerary output excludes owner/trip identifiers.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/integration/test_travel_note_api.py -q`
Run: `node --test tests/frontend/community-note.test.js tests/frontend/community-creator.test.js`
Expected: FAIL because creator endpoint and pages are absent.

- [ ] **Step 3: Implement creator projection and detail pages**

Return `creator_slug`, display name, bio, signed avatar URL, and approved note page. Render initials if avatar URL is null. The detail page uses buttons with accessible labels for gallery navigation and never exposes Storage paths in DOM data attributes.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/integration/test_travel_note_api.py -q`
Run: `node --test tests/frontend/community-note.test.js tests/frontend/community-creator.test.js`
Expected: PASS.

- [ ] **Step 5: Commit the reading slice**

```bash
git add app/static/community-note.html app/static/community-note.js app/static/community-creator.html app/static/community-creator.js app/api/travel_notes.py app/travel_notes tests/frontend tests/integration/test_travel_note_api.py
git commit -m "feat: add travel note detail and creator pages"
```

---

### Task 10: Implement likes, bookmarks, moderated comments, and reports

**Files:**
- Create: `app/travel_notes/interactions.py`
- Create: `app/api/community_interactions.py`
- Modify: `app/composition.py`
- Modify: `app/main.py`
- Create: `tests/unit/test_travel_note_interactions.py`
- Create: `tests/integration/test_community_interaction_api.py`

**Interfaces:**
- Consumes: interaction tables/RPCs from Task 2 and authenticated/public note IDs.
- Produces: `TravelNoteInteractionModule`, toggle-like, toggle-bookmark, submit-comment, list-approved-comments, and submit-report endpoints.

- [ ] **Step 1: Write failing interaction tests**

```python
def test_like_is_idempotent_and_bookmark_is_private():
    module = TravelNoteInteractionModule(InMemoryInteractionRepository())
    assert module.set_like(USER_A, NOTE_ID, True).like_count == 1
    assert module.set_like(USER_A, NOTE_ID, True).like_count == 1
    module.set_bookmark(USER_A, NOTE_ID, True)
    assert module.viewer_state(USER_A, NOTE_ID).bookmarked is True


def test_comment_is_pending_until_moderated():
    created = module.submit_comment(USER_A, NOTE_ID, "请问最佳拍摄时间？")
    assert created.status == "pending_review"
    assert module.list_public_comments(NOTE_ID, None, 20).items == []
```

Cover anonymous 401s, duplicate reports, deleted/unapproved notes, comment length 1–500, stable count updates, and private bookmark relations.

- [ ] **Step 2: Run interaction tests and verify failure**

Run: `python -m pytest tests/unit/test_travel_note_interactions.py tests/integration/test_community_interaction_api.py -q`
Expected: FAIL because module/router are absent.

- [ ] **Step 3: Implement interaction service and routes**

```text
PUT    /api/community/notes/{note_id}/like
DELETE /api/community/notes/{note_id}/like
PUT    /api/community/notes/{note_id}/bookmark
DELETE /api/community/notes/{note_id}/bookmark
GET    /api/community/notes/{note_id}/comments
POST   /api/community/notes/{note_id}/comments
POST   /api/community/notes/{note_id}/reports
```

Use database RPCs for idempotent like/bookmark mutations and count return values. Comments return pending state only to their author until approved. Reports never reveal another reporter or administrator disposition.

- [ ] **Step 4: Wire the router and run tests**

Run: `python -m pytest tests/unit/test_travel_note_interactions.py tests/integration/test_community_interaction_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the interaction backend**

```bash
git add app/travel_notes/interactions.py app/api/community_interactions.py app/composition.py app/main.py tests/unit/test_travel_note_interactions.py tests/integration/test_community_interaction_api.py
git commit -m "feat: add community interactions"
```

---

### Task 11: Add interaction controls to feed and detail pages

**Files:**
- Modify: `app/static/community-client.js`
- Modify: `app/static/community-feed.js`
- Modify: `app/static/community-note.js`
- Modify: `app/static/community.css`
- Create: `tests/frontend/community-interactions.test.js`

**Interfaces:**
- Consumes: Task 10 endpoints and viewer-state fields on cards/details.
- Produces: optimistic-but-reversible like/bookmark controls, moderated comment UI, and report dialog.

- [ ] **Step 1: Write failing frontend interaction tests**

```javascript
test("failed optimistic like restores the previous count", async () => {
  const harness = createInteractionHarness({ likeResponse: jsonResponse(503, errorBody()) });
  await harness.clickLike();
  assert.equal(harness.elements.get("note-like-count").textContent, "12");
  assert.match(harness.elements.get("note-feedback").textContent, /暂不可用/);
});


test("new comment is visible only as pending to its author", async () => {
  const harness = createInteractionHarness({ commentResponse: pendingComment() });
  await harness.submitComment("请问最佳拍摄时间？");
  assert.match(harness.elements.get("comment-list").textContent, /审核中/);
});
```

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `node --test tests/frontend/community-interactions.test.js`
Expected: FAIL because controls are not wired.

- [ ] **Step 3: Implement authenticated controls with stale-session guards**

Disable each control while its mutation is active, revert optimistic state on failure, redirect anonymous users with a same-origin `return_to`, and invalidate pending mutations when account identity changes.

- [ ] **Step 4: Implement comment and report forms**

Render approved comments plus the current user's pending comments, enforce 1–500 characters, show “审核中”, require a report reason, and prevent duplicate submits.

- [ ] **Step 5: Run frontend and syntax tests**

Run: `node --test tests/frontend/community-interactions.test.js tests/frontend/community-feed.test.js tests/frontend/community-note.test.js`
Run: `node --check app/static/community-feed.js`
Run: `node --check app/static/community-note.js`
Expected: all PASS.

- [ ] **Step 6: Commit the interaction UI**

```bash
git add app/static/community-client.js app/static/community-feed.js app/static/community-note.js app/static/community.css tests/frontend/community-interactions.test.js
git commit -m "feat: add community interaction controls"
```

---

### Task 12: Implement administrator authorization and review APIs

**Files:**
- Create: `app/travel_notes/moderation.py`
- Create: `app/api/community_admin.py`
- Create: `app/scripts/grant_community_admin.py`
- Modify: `app/composition.py`
- Modify: `app/main.py`
- Create: `tests/unit/test_community_moderation.py`
- Create: `tests/integration/test_community_admin_api.py`

**Interfaces:**
- Consumes: `user_roles`, `moderation_decisions`, and review RPCs from Task 2.
- Produces: `CommunityModerationModule`, `require_community_admin`, queue listing, approve/reject endpoints for notes and comments, report resolution, and a controlled administrator bootstrap script.

- [ ] **Step 1: Write failing moderation tests**

```python
def test_non_admin_receives_403_without_learning_queue_contents(client):
    response = client.get("/api/admin/community/review-queue", headers=user_headers())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "COMMUNITY_ADMIN_REQUIRED"


def test_rejection_requires_reason_and_records_actor(client):
    response = client.post(
        f"/api/admin/community/reviews/note/{NOTE_ID}/reject",
        headers=admin_headers(),
        json={"reason": "图片中包含无法确认授权的个人信息"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
```

Cover invalid target type, empty reason, repeated decision conflicts, report dismissal/content hiding, audit persistence, administrator bootstrap authorization, and provider outage mapping.

- [ ] **Step 2: Run moderation tests and verify failure**

Run: `python -m pytest tests/unit/test_community_moderation.py tests/integration/test_community_admin_api.py -q`
Expected: FAIL because moderation module/router are absent.

- [ ] **Step 3: Implement defense-in-depth administrator checks**

The API dependency validates the user token and calls `is_community_admin()` using the same JWT. Every approve/reject RPC repeats the role check in PostgreSQL before changing status or writing `moderation_decisions`.

- [ ] **Step 4: Implement queue and decision routes**

```text
GET  /api/admin/community/review-queue?target_type=note&cursor=<opaque_cursor>
POST /api/admin/community/reviews/{target_type}/{target_id}/approve
POST /api/admin/community/reviews/{target_type}/{target_id}/reject
POST /api/admin/community/reports/{report_id}/resolve
```

The queue returns private review data only after authorization. Reject requires a trimmed 1–500 character reason; approve clears author-facing rejection text and sets publication timestamps atomically. Report resolution accepts only `dismiss` or `hide_content`; hiding soft-deletes the reported approved item and writes the audit decision in the same transaction.

- [ ] **Step 5: Add a controlled administrator bootstrap command**

`grant_community_admin.py` accepts a required UUID argument, creates a service-key client, upserts `user_roles(user_id, role='admin')`, and logs only a hashed subject. Unit tests replace the client and assert that invalid UUIDs fail before any network call.

Run: `python -m app.scripts.grant_community_admin --user-id 00000000-0000-0000-0000-000000000001`
Expected in a configured staging environment: one administrator role row is created or preserved idempotently.

- [ ] **Step 6: Run moderation tests**

Run: `python -m pytest tests/unit/test_community_moderation.py tests/integration/test_community_admin_api.py -q`
Expected: PASS.

- [ ] **Step 7: Commit moderation backend**

```bash
git add app/travel_notes/moderation.py app/api/community_admin.py app/scripts/grant_community_admin.py app/composition.py app/main.py tests/unit/test_community_moderation.py tests/integration/test_community_admin_api.py
git commit -m "feat: add community moderation API"
```

---

### Task 13: Build the administrator review page

**Files:**
- Create: `app/static/admin-community.html`
- Create: `app/static/admin-community.js`
- Modify: `app/static/community.css`
- Create: `tests/frontend/admin-community.test.js`

**Interfaces:**
- Consumes: Task 12 review queue and decision endpoints.
- Produces: `/admin/community` authenticated queue UI for notes, comments, and reports.

- [ ] **Step 1: Write failing admin-page tests**

```javascript
test("non-admin page response redirects to home without rendering queue", async () => {
  const harness = createAdminHarness({ queueResponse: jsonResponse(403, adminRequired()) });
  await settle();
  assert.equal(harness.window.location.pathname, "/");
  assert.equal(harness.elements.get("review-queue").children.length, 0);
});


test("reject requires a reason and removes the decided item", async () => {
  const harness = createAdminHarness({ items: [pendingNote()] });
  await harness.reject("");
  assert.match(harness.elements.get("review-errors").textContent, /填写驳回原因/);
  await harness.reject("图片与正文主题不一致");
  assert.equal(harness.elements.get("review-queue").children.length, 0);
});


test("administrator resolves a report without exposing the reporter", async () => {
  const harness = createAdminHarness({ reports: [pendingReport()] });
  await harness.resolveReport("dismiss", "未发现违规内容");
  assert.equal(harness.fetchCalls.at(-1).url.includes("/reports/"), true);
  assert.doesNotMatch(harness.elements.get("review-queue").textContent, /reporter_id|user-a/);
});
```

- [ ] **Step 2: Run admin frontend tests and verify failure**

Run: `node --test tests/frontend/admin-community.test.js`
Expected: FAIL because admin page assets are absent.

- [ ] **Step 3: Implement the review queue UI**

Render target-type tabs for notes, comments, and reports; complete media previews; plain-text content; approve/reject or report-resolution controls; reason dialog; cursor pagination; empty/error/retry states; and request-generation guards. Do not put review reasons in URLs or logs.

- [ ] **Step 4: Run admin tests and syntax check**

Run: `node --test tests/frontend/admin-community.test.js`
Run: `node --check app/static/admin-community.js`
Expected: PASS.

- [ ] **Step 5: Commit the admin UI**

```bash
git add app/static/admin-community.html app/static/admin-community.js app/static/community.css tests/frontend/admin-community.test.js
git commit -m "feat: add community review console"
```

---

### Task 14: Complete cross-layer security, responsive, and deployment acceptance

**Files:**
- Modify: `tests/integration/test_user_journey.py`
- Modify: `tests/integration/test_rls_contract.py`
- Modify: `tests/integration/test_frontend_assets.py`
- Create: `tests/integration/test_travel_note_privacy_contract.py`
- Create: `docs/handoff-2026-08-21-travel-community-phase-1.md`
- Modify: `docs/work-log-2026-08-21-community-profile.md`

**Interfaces:**
- Consumes: every phase-one task.
- Produces: final two-account/anonymous acceptance coverage, privacy regression checks, deployment order, rollback procedure, and handoff record.

- [ ] **Step 1: Write the final failing user-journey tests**

```python
def test_travel_note_is_private_until_admin_approval(client):
    note = create_and_submit_note(client, user="user-a")
    assert anonymous_get(client, note.id).status_code == 404
    approve_note(client, note.id, admin="admin-a")
    assert anonymous_get(client, note.id).status_code == 200


def test_user_b_cannot_read_or_mutate_user_a_draft(client):
    note = create_draft(client, user="user-a")
    assert owner_get(client, note.id, user="user-b").status_code == 404
    assert owner_delete(client, note.id, user="user-b").status_code == 404
```

Add anonymous feed, two-account media path isolation, private bookmark, pending comment, administrator-only review, legacy snapshot exclusion, public payload allowlist, and sanitized-log tests.

- [ ] **Step 2: Run focused acceptance tests before final fixes**

Run: `python -m pytest tests/integration/test_user_journey.py tests/integration/test_travel_note_privacy_contract.py tests/integration/test_rls_contract.py -q`
Expected: any uncovered cross-layer gap fails with a precise assertion.

- [ ] **Step 3: Fix only acceptance gaps in owning modules**

Keep fixes in the file responsible for the failing contract. Do not add unrelated refactors or phase-two features.

- [ ] **Step 4: Run complete verification**

Run: `python -m pytest -q`
Expected: all backend tests PASS.

Run: `node --test tests/frontend/*.test.js`
Expected: all frontend tests PASS.

Run: `node --check app/static/community-client.js`
Run: `node --check app/static/community-feed.js`
Run: `node --check app/static/community-editor.js`
Run: `node --check app/static/community-mine.js`
Run: `node --check app/static/community-note.js`
Run: `node --check app/static/community-creator.js`
Run: `node --check app/static/admin-community.js`
Run: `git diff --check`
Expected: every command exits 0; only documented pre-existing warnings are allowed.

- [ ] **Step 5: Apply migration 011 in staging and perform real-runtime smoke tests**

Use Supabase SQL Editor to apply `011_travel_note_community.sql`, create one `admin` role row through the controlled bootstrap procedure, and verify:

1. anonymous cannot read drafts or Storage objects;
2. user A cannot read or mutate user B drafts/images/bookmarks;
3. administrator can approve and reject, ordinary users receive 403;
4. approved note appears in feed and signed image URLs load;
5. pending comment is visible only to its author and administrators;
6. old `community_posts` remains queryable by legacy code but absent from `/api/community/notes`;
7. logs contain no email, access token, raw UUID, Storage path, or full user content.

- [ ] **Step 6: Write the handoff and deployment order**

Record migration status, environment variables, Storage bucket, admin bootstrap, test results, rollback strategy, known warnings, and manual smoke-test evidence. Deployment order is migration → backend → static assets → smoke tests. Roll back application code without dropping new tables or media.

- [ ] **Step 7: Commit the acceptance slice**

```bash
git add tests docs/handoff-2026-08-21-travel-community-phase-1.md docs/work-log-2026-08-21-community-profile.md
git commit -m "test: verify travel community phase one"
```

## Execution Boundary

Phase two must not start until every Task 14 automated and staging acceptance item passes. Following and notification work receives its own design review and implementation plan; private messaging remains phase three.
