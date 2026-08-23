from __future__ import annotations

from uuid import UUID

import pytest

from app.core.errors import AppError
from app.travel_notes.interactions import (
    InMemoryInteractionRepository,
    TravelNoteInteractionModule,
)


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
NOTE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_NOTE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
COMMENT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def create_module() -> TravelNoteInteractionModule:
    return TravelNoteInteractionModule(
        InMemoryInteractionRepository(
            approved_note_ids={NOTE_ID},
            approved_comment_ids={COMMENT_ID},
            comment_note_ids={COMMENT_ID: NOTE_ID},
        )
    )


def error_code(error: pytest.ExceptionInfo[AppError]) -> str:
    return error.value.code


def test_like_and_bookmark_are_idempotent_and_viewer_scoped():
    module = create_module()

    first_like = module.set_like(USER_A, NOTE_ID, True)
    second_like = module.set_like(USER_A, NOTE_ID, True)
    module.set_like(USER_B, NOTE_ID, True)
    module.set_bookmark(USER_A, NOTE_ID, True)

    assert first_like.like_count == 1
    assert second_like.like_count == 1
    assert module.viewer_state(USER_A, NOTE_ID).like_count == 2
    assert module.viewer_state(USER_A, NOTE_ID).liked is True
    assert module.viewer_state(USER_A, NOTE_ID).bookmarked is True
    assert module.viewer_state(USER_B, NOTE_ID).bookmarked is False


def test_unlike_and_unbookmark_are_idempotent():
    module = create_module()

    module.set_like(USER_A, NOTE_ID, False)
    module.set_bookmark(USER_A, NOTE_ID, False)
    result = module.viewer_state(USER_A, NOTE_ID)

    assert result.like_count == 0
    assert result.liked is False
    assert result.bookmarked is False


def test_comments_start_pending_and_public_listing_hides_them():
    module = create_module()

    created = module.submit_comment(USER_A, NOTE_ID, "请问最佳拍摄时间？")
    page = module.list_public_comments(NOTE_ID, cursor=None, limit=20)

    assert created.status == "pending_review"
    assert created.author_display_name == "Voyage 旅行者"
    assert page.items == []


def test_report_is_idempotent_and_comment_must_belong_to_note():
    module = create_module()

    first = module.submit_report(
        USER_A,
        NOTE_ID,
        target_type="comment",
        target_id=COMMENT_ID,
        reason="内容不实",
    )
    second = module.submit_report(
        USER_A,
        NOTE_ID,
        target_type="comment",
        target_id=COMMENT_ID,
        reason="内容不实",
    )

    assert first.id == second.id
    with pytest.raises(AppError) as error:
        module.submit_report(
            USER_A,
            OTHER_NOTE_ID,
            target_type="comment",
            target_id=COMMENT_ID,
            reason="跨游记评论举报",
        )
    assert error_code(error) == "TRAVEL_NOTE_NOT_FOUND"


def test_comment_author_can_see_own_pending_comment_but_other_viewers_cannot():
    module = create_module()
    created = module.submit_comment(USER_A, NOTE_ID, "pending author view")
    own_page = module.list_public_comments(NOTE_ID, cursor=None, limit=20, viewer_id=USER_A)
    other_page = module.list_public_comments(NOTE_ID, cursor=None, limit=20, viewer_id=UUID("22222222-2222-2222-2222-222222222222"))
    assert [item.id for item in own_page.items] == [created.id]
    assert own_page.items[0].status == "pending_review"
    assert other_page.items == []
