from pathlib import Path


MIGRATION_PATH = Path("supabase/migrations/011_travel_note_community.sql")


def test_migration_rejects_snapshot_on_direct_client_writes():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    client_write_rules = sql[
        sql.index("create or replace function public.enforce_travel_note_client_write_rules") :
        sql.index("create or replace function public.enforce_travel_note_image_write_rules")
    ]

    assert "or new.itinerary_snapshot is not null" in client_write_rules
    assert "or new.itinerary_snapshot is distinct from old.itinerary_snapshot" in client_write_rules


def test_submit_rpc_is_the_snapshot_persistence_boundary():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    submit_rpc = sql[
        sql.index("create or replace function public.submit_travel_note") :
        sql.index("create or replace function public.review_travel_note")
    ]

    assert "itinerary_snapshot = v_snapshot" in submit_rpc
    assert "v_snapshot := v_trip.itinerary" in submit_rpc
