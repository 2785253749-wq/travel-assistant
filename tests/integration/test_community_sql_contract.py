from pathlib import Path
import re


MIGRATION = (
    Path(__file__).parents[2]
    / "supabase"
    / "migrations"
    / "010_community_profile.sql"
)
ALLOWED_TRAVEL_STYLES = ("美食", "人文", "自然", "亲子", "户外", "休闲")
PUBLIC_COMMUNITY_COLUMNS = {
    "id",
    "author_display_name",
    "title",
    "destination",
    "summary",
    "itinerary_snapshot",
    "created_at",
    "updated_at",
}
FORBIDDEN_PUBLIC_COLUMNS = {
    "user_id",
    "source_trip_id",
    "email",
    "conversation_messages",
    "token_hash",
    "profile",
    "preferences",
}


def _migration() -> str:
    assert MIGRATION.exists(), f"missing migration: {MIGRATION.name}"
    return MIGRATION.read_text(encoding="utf-8").lower()


def _function_block(migration: str, function_name: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{function_name}\s*"
        rf"\((?P<args>[\s\S]*?)\)\s*"
        rf"returns\s+(?P<returns>[\s\S]*?)\s+language\s+(?P<language>\w+)\s+"
        rf"security\s+definer\s+set\s+search_path\s*=\s*(?P<search_path>[\w\s,]+)\s+"
        rf"as\s+\$\$(?P<body>[\s\S]*?)\$\$",
        migration,
    )
    assert match is not None, f"missing function block for {function_name}"
    return match.group(0)


def _routine_block(migration: str, function_name: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{function_name}\s*"
        rf"\([\s\S]*?\)\s*returns\s+[\w\[\]]+[\s\S]*?"
        rf"as\s+\$\$(?P<body>[\s\S]*?)\$\$\s*;",
        migration,
    )
    assert match is not None, f"missing function block for {function_name}"
    return match.group(0)


def _returns_table_columns(function_block: str) -> set[str]:
    match = re.search(
        r"returns\s+table\s*\((?P<columns>[\s\S]*?)\)\s+language",
        function_block,
    )
    assert match is not None, "expected returns table (...) contract"
    columns: set[str] = set()
    for raw_line in match.group("columns").splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        columns.add(line.split()[0])
    return columns


def test_profiles_preferences_contract_keeps_a_structured_json_object():
    migration = _migration()

    assert "alter table public.profiles" in migration
    assert "alter column preferences set default '{}'::jsonb" in migration
    assert "jsonb_typeof(preferences) = 'object'" in migration
    assert "preferences ->> 'bio'" in migration
    assert "preferences ->> 'home_city'" in migration
    assert "preferences -> 'travel_styles'" in migration
    assert "jsonb_array_length(preferences -> 'travel_styles') <= 5" in migration
    for style in ALLOWED_TRAVEL_STYLES:
        assert f"'{style}'" in migration
    assert "create trigger profiles_set_updated_at" in migration


def test_profiles_preferences_migration_preserves_unmanaged_object_keys():
    migration = _migration()
    update = re.search(
        r"update\s+public\.profiles\s+as\s+profile_row\s+"
        r"set\s+preferences\s*=\s*(?P<expression>[\s\S]*?)\s*;",
        migration,
    )

    assert update is not None
    expression = re.sub(r"\s+", " ", update.group("expression")).strip()
    assert (
        "case when jsonb_typeof(profile_row.preferences) = 'object' "
        "then profile_row.preferences else '{}'::jsonb end"
    ) in expression
    assert re.search(
        r"end\s*-\s*'bio'\s*-\s*'home_city'\s*-\s*'travel_styles'\s*"
        r"\)\s*\|\|\s*jsonb_strip_nulls",
        expression,
    )


def test_community_posts_table_has_private_snapshot_constraints_and_indexes():
    migration = _migration()

    assert "create table public.community_posts" in migration
    assert (
        "user_id uuid not null references auth.users(id) on delete cascade" in migration
    )
    assert (
        "source_trip_id uuid references public.trips(id) on delete set null"
        in migration
    )
    assert (
        "author_display_name text not null check (char_length(author_display_name) between 1 and 40)"
        in migration
    )
    assert "title text not null check (char_length(title) between 1 and 100)" in migration
    assert (
        "destination text not null check (char_length(destination) between 1 and 80)"
        in migration
    )
    assert "summary text not null check (char_length(summary) between 1 and 300)" in migration
    assert "itinerary_snapshot jsonb not null" in migration
    assert "jsonb_typeof(itinerary_snapshot) = 'object'" in migration
    assert "create unique index" in migration
    assert "on public.community_posts (user_id, source_trip_id)" in migration
    assert "where source_trip_id is not null" in migration
    assert "create index" in migration
    assert "on public.community_posts (created_at desc, id desc)" in migration
    assert "create trigger community_posts_set_updated_at" in migration


def test_publish_rpc_uses_authenticated_trip_ownership_and_duplicate_guards():
    migration = _migration()
    function_block = _function_block(migration, "publish_community_post")

    assert "security definer" in function_block
    assert "set search_path = pg_catalog, public" in function_block
    assert "auth.uid()" in function_block
    assert "insert into public.community_posts" in function_block
    assert "join public.profiles" in function_block or "from public.profiles" in function_block
    assert "from public.trips" in function_block
    assert "status = 'planned'" in function_block
    assert "user_id = auth.uid()" in function_block or "user_id = v_user_id" in function_block
    assert "source_trip_id" in function_block
    assert "summary" in function_block
    assert "voyage 旅行者" in function_block
    assert "duplicate" in function_block or "unique_violation" in function_block
    assert "itinerary_snapshot" in function_block
    assert "revoke all on function public.publish_community_post" in migration
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.publish_community_post\([^)]*\)\s+to\s+authenticated",
        migration,
    )
    assert not re.search(
        r"grant\s+execute\s+on\s+function\s+public\.publish_community_post\([^)]*\)\s+to\s+anon",
        migration,
    )


def test_publish_rpc_uses_non_conflicting_inputs_and_qualified_relations():
    migration = _migration()
    function_block = _function_block(migration, "publish_community_post")
    signature = re.search(
        r"function\s+public\.publish_community_post\s*\((?P<args>[^)]*)\)",
        function_block,
    )

    assert signature is not None
    assert re.sub(r"\s+", " ", signature.group("args")).strip() == (
        "p_source_trip_id uuid, p_summary text"
    )
    assert "v_summary text := btrim(p_summary)" in function_block
    assert "from public.trips as trip_row" in function_block
    assert "where trip_row.id = p_source_trip_id" in function_block
    assert "and trip_row.user_id = v_user_id" in function_block
    assert "from public.profiles as profile_row" in function_block
    assert "where profile_row.user_id = v_user_id" in function_block
    assert "from public.community_posts as post_row" in function_block
    assert "where post_row.user_id = v_user_id" in function_block
    assert "and post_row.source_trip_id = p_source_trip_id" in function_block
    assert "insert into public.community_posts as inserted_post" in function_block
    assert "returning inserted_post.* into v_post" in function_block


def test_publish_rpc_validates_effective_display_name_before_insert():
    migration = _migration()
    function_block = _function_block(migration, "publish_community_post")
    insert_index = function_block.index("insert into public.community_posts")

    assert "voyage 旅行者" in function_block
    assert "char_length(v_author_display_name) not between 1 and 40" in function_block
    validation_index = function_block.index(
        "char_length(v_author_display_name) not between 1 and 40"
    )
    assert validation_index < insert_index
    assert re.search(
        r"if\s+char_length\(v_author_display_name\)\s+not\s+between\s+1\s+and\s+40\s+then[\s\S]*?"
        r"raise\s+exception\s+'author display name must be between 1 and 40 characters'\s+using\s+errcode\s*=\s*'p0001'",
        function_block,
    )


def test_trusted_publish_path_enforces_the_recursive_public_itinerary_contract():
    migration = _migration()
    object_guard = _routine_block(
        migration, "community_jsonb_object_has_only_keys"
    )
    validator = _routine_block(migration, "community_public_itinerary_is_valid")
    citations = _routine_block(
        migration, "community_public_citations_are_valid"
    )
    facts = _routine_block(migration, "community_public_facts_are_valid")
    publish = _function_block(migration, "publish_community_post")
    normalized_contract = re.sub(
        r"\s+", " ", "\n".join((validator, citations, facts))
    )

    assert "jsonb_object_keys(p_value)" in object_guard
    assert "object_key.key <> all(p_allowed_keys)" in object_guard
    for allowlist in (
        "array['title', 'start_date', 'end_date', 'days', 'budget', 'notes', 'assumptions', 'citations', 'booking_links']",
        "array['date', 'morning', 'afternoon', 'evening', 'weather']",
        "array['title', 'start_time', 'end_time', 'notes', 'facts', 'citations']",
        "array['text', 'evidence_id']",
        "array['evidence_id', 'source_url', 'source_type', 'fetched_at', 'freshness', 'fact', 'source_label']",
        "array['date', 'city', 'status', 'summary', 'report_time']",
        "array['transport', 'hotel', 'food', 'tickets', 'reserve', 'other', 'total', 'currency', 'traveler_basis', 'traveler_count', 'trip_total', 'estimate']",
        "array['low', 'point', 'high', 'currency', 'basis', 'assumption_id']",
        "array['assumption_id', 'category', 'description']",
        "array['train', 'hotel', 'flight', 'disclaimer']",
    ):
        assert allowlist in normalized_contract

    assert "public.community_public_facts_are_valid" in validator
    assert "public.community_public_citations_are_valid" in validator
    assert "if not public.community_public_itinerary_is_valid(v_trip.itinerary)" in publish
    assert "v_itinerary_snapshot := v_trip.itinerary" in publish
    assert re.search(
        r"values\s*\([\s\S]*?v_itinerary_snapshot[\s\S]*?\)\s*returning",
        publish,
    )
    assert re.search(
        r"itinerary_snapshot\s+jsonb\s+not\s+null\s+check\s*\(\s*"
        r"jsonb_typeof\(itinerary_snapshot\)\s*=\s*'object'\s+and\s+"
        r"public\.community_public_itinerary_is_valid\(itinerary_snapshot\)\s*\)",
        migration,
    )


def test_trusted_itinerary_validator_rejects_json_null_required_enum_values():
    migration = _migration()
    citations = re.sub(
        r"\s+",
        " ",
        _routine_block(migration, "community_public_citations_are_valid"),
    )
    validator = re.sub(
        r"\s+",
        " ",
        _routine_block(migration, "community_public_itinerary_is_valid"),
    )
    required_guards = {
        "citation source_type": (
            citations,
            "jsonb_typeof(citation_item.value -> 'source_type') = 'string' "
            "and citation_item.value ->> 'source_type' in (",
        ),
        "weather status": (
            validator,
            "jsonb_typeof(v_weather -> 'status') <> 'string' "
            "or v_weather ->> 'status' not in (",
        ),
        "budget currency": (
            validator,
            "jsonb_typeof(v_budget -> 'currency') <> 'string' "
            "or v_budget ->> 'currency' <> 'cny'",
        ),
        "budget traveler_basis": (
            validator,
            "jsonb_typeof(v_budget -> 'traveler_basis') <> 'string' "
            "or v_budget ->> 'traveler_basis' not in (",
        ),
        "estimate currency": (
            validator,
            "jsonb_typeof(v_estimate -> 'currency') <> 'string' "
            "or v_estimate ->> 'currency' <> 'cny'",
        ),
        "estimate basis": (
            validator,
            "jsonb_typeof(v_estimate -> 'basis') <> 'string' "
            "or v_estimate ->> 'basis' not in (",
        ),
        "assumption category": (
            validator,
            "jsonb_typeof(v_assumption -> 'category') <> 'string' "
            "or v_assumption ->> 'category' not in (",
        ),
    }

    missing_guards = [
        field_name
        for field_name, (function_block, guard) in required_guards.items()
        if guard not in function_block
    ]

    assert not missing_guards, (
        "required itinerary enum fields need explicit JSON string guards before "
        f"their value checks: {', '.join(missing_guards)}"
    )


def test_public_community_rpcs_use_allowlisted_columns_and_cursor_pagination():
    migration = _migration()

    list_block = _function_block(migration, "list_community_posts")
    get_block = _function_block(migration, "get_community_post")

    for function_block in (list_block, get_block):
        assert "security definer" in function_block
        assert "set search_path = pg_catalog, public" in function_block
        returned_columns = _returns_table_columns(function_block)
        assert returned_columns == PUBLIC_COMMUNITY_COLUMNS
        assert not (returned_columns & FORBIDDEN_PUBLIC_COLUMNS)

    assert "from public.community_posts as post_row" in list_block
    assert "order by post_row.created_at desc, post_row.id desc" in list_block
    assert "cursor_created_at" in list_block
    assert "cursor_id" in list_block
    assert "page_size" in list_block
    assert "limit least(greatest" in list_block or "greatest(1" in list_block
    assert "post_row.created_at < cursor_created_at" in list_block
    assert "post_row.created_at = cursor_created_at" in list_block
    assert "from public.community_posts as post_row" in get_block
    assert "where post_row.id = post_id" in get_block
    assert "grant execute on function public.list_community_posts" in migration
    assert "grant execute on function public.get_community_post" in migration
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.list_community_posts\([^)]*\)\s+to\s+anon,\s*authenticated",
        migration,
    )
    assert re.search(
        r"grant\s+execute\s+on\s+function\s+public\.get_community_post\([^)]*\)\s+to\s+anon,\s*authenticated",
        migration,
    )


def test_public_list_rpc_allows_one_internal_lookahead_row():
    migration = _migration()
    list_block = _function_block(migration, "list_community_posts")

    assert re.search(
        r"limit\s+least\s*\(\s*greatest\s*\(\s*coalesce\s*\(\s*page_size\s*,\s*20\s*\)\s*,\s*1\s*\)\s*,\s*51\s*\)",
        list_block,
    )
