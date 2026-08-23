from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.travel_notes.moderation import (
    InMemoryModerationRepository,
    ModerationComment,
    ModerationImage,
    ModerationNote,
    ModerationReport,
    TravelNoteModerationModule,
)


ADMIN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NOTE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
COMMENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
REPORT_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
NOW = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)


def _module() -> TravelNoteModerationModule:
    repository = InMemoryModerationRepository(admin_user_ids={ADMIN_ID})
    repository.add_note(
        ModerationNote(
            id=NOTE_ID,
            title="待审核游记",
            body="公开内容",
            location_name="厦门",
            category="城市漫步",
            status="pending_review",
            review_reason=None,
            submitted_at=NOW,
            author_display_name="Voyage 旅行者",
            images=[
                ModerationImage(
                    id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                    image_url="https://signed.example.test/short-lived",
                    sort_order=0,
                    width=1200,
                    height=800,
                )
            ],
        )
    )
    repository.add_comment(
        ModerationComment(
            id=COMMENT_ID,
            note_id=NOTE_ID,
            author_display_name="Voyage 旅行者",
            body="待审核评论",
            status="pending_review",
            review_reason=None,
            created_at=NOW,
        )
    )
    repository.add_report(
        ModerationReport(
            id=REPORT_ID,
            target_type="comment",
            target_id=COMMENT_ID,
            reason="疑似广告",
            status="pending",
            resolution_note=None,
            created_at=NOW,
        )
    )
    return TravelNoteModerationModule(repository)


def test_non_admin_cannot_read_or_mutate_any_moderation_queue():
    module = _module()

    with pytest.raises(AppError) as error:
        module.list_notes(USER_ID, limit=20)
    assert error.value.code == "COMMUNITY_ADMIN_REQUIRED"

    with pytest.raises(AppError) as error:
        module.review_note(USER_ID, NOTE_ID, decision="approved", reason=None)
    assert error.value.code == "COMMUNITY_ADMIN_REQUIRED"


def test_admin_can_review_note_and_rejection_requires_reason():
    module = _module()

    with pytest.raises(AppError) as error:
        module.review_note(ADMIN_ID, NOTE_ID, decision="rejected", reason=" ")
    assert error.value.code == "COMMUNITY_MODERATION_VALIDATION_FAILED"

    reviewed = module.review_note(
        ADMIN_ID, NOTE_ID, decision="rejected", reason="缺少必要的行程说明"
    )

    assert reviewed.status == "rejected"
    assert reviewed.review_reason == "缺少必要的行程说明"
    assert reviewed.images[0].image_url.startswith("https://")
    assert not hasattr(reviewed, "storage_path")
    assert not hasattr(reviewed, "author_id")


def test_admin_can_review_comment_and_action_report_without_changing_target():
    module = _module()

    comment = module.review_comment(
        ADMIN_ID, COMMENT_ID, decision="approved", reason=None
    )
    report = module.resolve_report(
        ADMIN_ID,
        REPORT_ID,
        decision="actioned",
        resolution_note="已记录并关闭举报",
    )

    assert comment.status == "approved"
    assert report.status == "actioned"
    assert report.target_id == COMMENT_ID

def test_all_in_memory_moderation_queues_use_stable_time_and_id_cursors():
    module = _module()
    repository = module._repository
    note_template = repository._notes[NOTE_ID]
    comment_template = repository._comments[COMMENT_ID]
    report_template = repository._reports[REPORT_ID]
    repository._notes.clear()
    repository._comments.clear()
    repository._reports.clear()
    note_ids = [UUID(f"00000000-0000-0000-0000-00000000000{i}") for i in range(1, 4)]
    comment_ids = [UUID(f"10000000-0000-0000-0000-00000000000{i}") for i in range(1, 4)]
    report_ids = [UUID(f"20000000-0000-0000-0000-00000000000{i}") for i in range(1, 4)]
    for item_id in note_ids:
        repository.add_note(note_template.model_copy(update={"id": item_id}))
    for item_id in comment_ids:
        repository.add_comment(comment_template.model_copy(update={"id": item_id}))
    for item_id in report_ids:
        repository.add_report(report_template.model_copy(update={"id": item_id}))

    for list_method in (repository.list_notes, repository.list_comments, repository.list_reports):
        first = list_method(None, 2)
        second = list_method(first.next_cursor, 2)
        assert first.next_cursor
        assert len(first.items) == 2
        assert set(item.id for item in first.items).isdisjoint({item.id for item in second.items})
        assert len(second.items) == 1


def test_hide_content_is_an_explicit_admin_operation():
    module = _module()
    result = module.hide_content(ADMIN_ID, "note", NOTE_ID)
    assert result.target_type == "note"
    assert result.target_id == NOTE_ID
    assert result.hidden is True
    assert module._repository.is_hidden("note", NOTE_ID)
    assert module._repository.decisions[-1]["decision"] == "hide_content"
