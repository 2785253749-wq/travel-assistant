# Community and Profile MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved community publishing/browsing MVP and the dedicated private profile page without weakening the existing trip ownership, authentication, or public/private data boundaries.

**Architecture:** Keep private trips and profiles behind the authenticated Supabase repository path. Add `community_posts` as a denormalized public snapshot written only through a server-side `SECURITY DEFINER` publish function. Expose anonymous list/detail through allow-listed public RPCs, and derive the authenticated viewer's `can_delete` flag privately in the API layer. Add a dedicated `/profile` page and replace the community placeholder with list/detail/publish/withdraw states.

**Tech Stack:** FastAPI, Pydantic, Supabase/PostgreSQL SQL migrations and RLS/RPC, vanilla JavaScript, the existing fake-DOM frontend harness, pytest, and Node's built-in test runner.

**Spec:** `docs/superpowers/specs/2026-08-20-community-profile-design.md`

## Global Constraints

- Preserve unrelated untracked handoff/work-log/planning documents and do not add feature files to PR #23.
- Use the existing repository/composition patterns and `AuthenticatedUser`/`CurrentUser` authentication path.
- Follow TDD for every behavior: add a focused failing test, run it to observe the expected failure, implement the smallest change, run the focused test to green, then refactor only if the green suite remains green.
- Never expose `user_id`, `source_trip_id`, email, or conversation data through anonymous community list/detail responses.
- Render user-controlled text with `textContent` or form values; do not introduce `innerHTML` interpolation.
- Do not commit or push implementation changes without explicit user authorization; keep the plan and implementation reviewable in the working tree.

---

## Task 1: Establish domain contracts and validation

**Files:** `app/schemas.py`, `app/profile/models.py`, `app/community/models.py`, `tests/unit/test_profile_models.py`, `tests/unit/test_community_models.py`

- [ ] Inspect existing schema conventions and add the smallest profile input/output types needed for `display_name`, `bio`, `home_city`, and `travel_styles`.
- [ ] Add failing profile model tests for trimming, required display name, maximum lengths, valid travel-style arrays, and rejection of unknown profile fields.
- [ ] Run `& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/unit/test_profile_models.py -q`; confirm the new tests fail because the contracts do not exist.
- [ ] Implement profile validation and serializable output models without changing trip schemas.
- [ ] Re-run the focused profile tests and confirm green.
- [ ] Add failing community model tests for title/destination/summary limits, snapshot object shape, stable cursor encoding/decoding, and public response exclusion fields.
- [ ] Run `& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/unit/test_community_models.py -q`; confirm failure before implementation.
- [ ] Implement `CommunityPost`, `CommunityPage`, cursor helpers, and publish input/error contracts.
- [ ] Re-run both focused model suites and then `git diff --check`.

## Task 2: Add the database schema, RLS, and RPC contract

**Files:** `supabase/migrations/010_community_profile.sql`, `tests/integration/test_rls_contract.py`, `tests/integration/test_community_sql_contract.py`

- [ ] Extend the RLS contract tests first so `community_posts` is treated as an owner-scoped table and the migration must contain private-table revocation, authenticated own-row delete, and no direct client insert/update policies.
- [ ] Add failing SQL contract tests for the `profiles.preferences` JSONB fields, `community_posts` constraints/indexes, `publish_community_post`, `list_community_posts`, `get_community_post`, fixed `search_path`, authenticated execution grants, and anonymous response allow-list.
- [ ] Run the focused integration contract tests and confirm they fail because migration `010_community_profile.sql` is absent.
- [ ] Implement the migration: profile JSONB defaults/constraints as compatible with existing data; `community_posts`; updated-at trigger; RLS; owner-only authenticated delete; and the three security-definer functions.
- [ ] Ensure the publish RPC validates `auth.uid()`, source-trip ownership, `planned` status, profile display name, and duplicate publication before creating a deep JSON snapshot.
- [ ] Ensure public RPCs paginate by `(created_at, id)`, return only public fields, and never read through a client-exposed private table query.
- [ ] Re-run the SQL/RLS contract tests and inspect the migration diff for accidental grants or public policies.

## Task 3: Implement private profile repository, module, and API

**Files:** `app/profile/repositories.py`, `app/profile/service.py`, `app/api/profile.py`, `app/main.py`, `app/composition.py`, `tests/unit/test_profile_service.py`, `tests/integration/test_profile_api.py`, `tests/unit/test_profile_production_wiring.py`

- [ ] Add failing service tests for profile retrieval defaults, full replacement, trimming, validation errors, and preservation of unrelated profile preference keys.
- [ ] Run the focused service tests and confirm failure because the profile module is missing.
- [ ] Implement `ProfileModule` plus in-memory and Supabase repository adapters. The Supabase adapter must use the authenticated JWT client and the existing `database_operation` logging convention.
- [ ] Re-run the focused service tests and confirm green.
- [ ] Add failing API tests for `GET /api/profile`, authenticated `PUT /api/profile`, missing bearer token, invalid payload/extra fields, and stable response shape.
- [ ] Run the focused API tests and confirm failure before route wiring.
- [ ] Implement `app/api/profile.py`, add `/profile` static serving, include the router, and wire the module through the composition root.
- [ ] Re-run profile API and production-wiring tests; confirm no email or trip fields are accepted as writable profile data.

## Task 4: Implement community repositories, module, and API

**Files:** `app/community/repositories.py`, `app/community/service.py`, `app/api/community.py`, `app/main.py`, `app/composition.py`, `tests/unit/test_community_service.py`, `tests/integration/test_community_api.py`, `tests/unit/test_community_production_wiring.py`

- [ ] Add failing service tests for publishing an owned planned trip, rejecting an unowned trip, rejecting a non-planned trip, deep-copying the itinerary snapshot, duplicate publication, listing/detail pagination, and cross-user withdrawal.
- [ ] Run the focused service tests and confirm failure before the community module exists.
- [ ] Implement `CommunityModule` and in-memory repositories with explicit `publish`, `withdraw`, `list_posts`, `get_post`, and current-user post-ID operations.
- [ ] Re-run the focused service tests and confirm green.
- [ ] Add failing repository/wiring tests proving authenticated calls carry the JWT, public calls use the anonymous key, publication goes through the RPC allow-list, and no public adapter directly queries `community_posts`.
- [ ] Run the focused repository/wiring tests and confirm failure before Supabase adapters are wired.
- [ ] Implement `SupabaseCommunityRepository` and `SupabasePublicCommunityRepository` using the existing Supabase client abstractions and normalized database error mapping.
- [ ] Add failing API tests for anonymous list/detail, opaque cursor/limit validation, authenticated publish, duplicate/non-publishable errors, own-only delete, stable 404s, and private `can_delete` derivation.
- [ ] Run the focused API tests and confirm failure before route wiring.
- [ ] Implement `/api/community/posts` list/detail/publish/delete routes and map domain errors to the documented status codes and error codes.
- [ ] Re-run all community service, repository, and API tests; verify anonymous payloads contain no author UUID, source trip ID, email, or conversations.

## Task 5: Wire and regression-test the backend composition

**Files:** `app/composition.py`, `app/main.py`, `tests/unit/test_trip_production_wiring.py`, `tests/integration/test_user_journey.py`

- [ ] Add failing wiring tests for in-memory mode, Supabase mode, optional-auth community reads, and profile/community route registration.
- [ ] Run the focused wiring tests and confirm failure for each missing composition branch.
- [ ] Add cached factories for profile, authenticated community, and public community services following existing environment/configuration behavior.
- [ ] Re-run wiring tests and the existing authenticated trip journey tests.
- [ ] Run the complete backend suite with `& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q`; investigate and fix only regressions caused by this feature.

## Task 6: Build the private profile page and account navigation

**Files:** `app/static/profile.html`, `app/static/profile.js`, `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`, `tests/frontend/profile.test.js`, `tests/frontend/app.test.js`, `tests/frontend/dom-harness.js`

- [ ] Extend the frontend harness and add failing profile tests for signed-out same-origin redirect, loading/error states, initial value population, full replacement save, validation error display, and return-to navigation.
- [ ] Run `node --test tests/frontend/profile.test.js`; confirm failure because the page/script do not exist.
- [ ] Implement the accessible profile page and script using `fetch('/api/profile')`, `fetch('/api/profile', {method: 'PUT'})`, form values, `textContent`, and a same-origin `return_to` whitelist.
- [ ] Re-run profile tests and confirm green.
- [ ] Add the “个人信息” account-menu link only for signed-in users, preserve logout behavior, and cover the signed-in/signed-out menu states in `app.test.js`.
- [ ] Run `node --test tests/frontend/app.test.js tests/frontend/profile.test.js` and `node --check app/static/profile.js`.

## Task 7: Replace the community placeholder with the approved UI flow

**Files:** `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`, `tests/frontend/community.test.js`, `tests/frontend/app.test.js`, `tests/frontend/dom-harness.js`

- [ ] Add failing community frontend tests for anonymous list loading, empty/error/retry states, detail navigation, pagination, signed-in planned-trip publish form, validation feedback, own-post withdrawal, and stale account-switch protection.
- [ ] Run `node --test tests/frontend/community.test.js`; confirm failure while the page is still the placeholder.
- [ ] Implement semantic community list/detail/publish/withdraw sections and state transitions without `innerHTML` interpolation. Keep anonymous browsing available and make publishing/withdrawal auth-gated.
- [ ] Re-run community tests and confirm green.
- [ ] Add responsive styles for the new sections while preserving the existing Voyage visual language and avoiding layout regressions on the screenshots’ viewport sizes.
- [ ] Run the full frontend suite `node --test tests/frontend/*.test.js`, plus `node --check app/static/app.js` and `node --check app/static/profile.js`.

## Task 8: Final verification, documentation, and handoff

**Files:** `docs/superpowers/specs/2026-08-20-community-profile-design.md` (only if implementation decisions changed), `docs/handoff-2026-08-20-auth-route.md` (only if the user explicitly asks to update it)

- [ ] Review the diff for scope, accidental changes to PR #23 files, secret leakage, direct `innerHTML`, public RPC overexposure, and missing error-code mappings.
- [ ] Run `node --test tests/frontend/*.test.js`.
- [ ] Run `& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q`.
- [ ] Run `node --check app/static/app.js`, `node --check app/static/profile.js`, and `git diff --check`.
- [ ] Record the exact test results and any migration/deployment prerequisite in the final handoff; do not claim deployment or PR creation unless separately authorized and verified.
