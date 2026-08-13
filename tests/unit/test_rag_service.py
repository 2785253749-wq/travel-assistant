import pytest

from app.rag.embedding import RagUnavailable
from app.rag.models import RetrievedChunk
from app.rag.service import KnowledgeAnswerService


FIXED_REFUSAL = "资料库没有足够依据，无法可靠回答。"


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.0] * 1024 for _ in texts]


class FailingEmbedder:
    def embed(self, _texts: list[str]) -> list[list[float]]:
        raise RagUnavailable("secret upstream detail")


class FakeRepository:
    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = scores if scores is not None else [0.94]
        self.calls: list[tuple[list[float], str | None, int]] = []

    def search(self, vector, region, limit):
        self.calls.append((list(vector), region, limit))
        names = ["high-score", "low-score", "third", "fourth", "fifth"]
        return [
            RetrievedChunk(
                chunk_id=names[index],
                content=f"第 {index + 1} 条资料",
                source_label="厦门市文化和旅游局",
                score=score,
            )
            for index, score in enumerate(self.scores)
        ]


class FailingRepository:
    def search(self, _vector, _region, limit):
        del limit
        raise RagUnavailable("private database detail")


class BrokenEmbedder:
    def embed(self, _texts: list[str]) -> list[list[float]]:
        raise TypeError("programming defect")


class BrokenRepository:
    def search(self, _vector, _region, limit):
        del limit
        raise AttributeError("repository programming defect")


def test_lowest_ranked_chunk_below_threshold_is_not_returned() -> None:
    repository = FakeRepository(scores=[0.94, 0.62])
    embedder = FakeEmbedder()

    answer = KnowledgeAnswerService(repository, embedder).answer(
        "厦门怎么去鼓浪屿", region="厦门"
    )

    assert answer.status == "grounded"
    assert [chunk.chunk_id for chunk in answer.chunks] == ["high-score"]
    assert answer.reply == "第 1 条资料\n【来源：厦门市文化和旅游局】"
    assert embedder.calls == [["厦门怎么去鼓浪屿"]]
    assert repository.calls == [([0.0] * 1024, "厦门", 4)]


def test_embedding_failure_returns_fixed_chinese_refusal_without_repository_call() -> None:
    repository = FakeRepository()

    answer = KnowledgeAnswerService(repository, FailingEmbedder()).answer(
        "云南雨季", region=None
    )

    assert answer.status == "refused"
    assert answer.reply == FIXED_REFUSAL
    assert answer.chunks == ()
    assert repository.calls == []


def test_repository_failure_returns_fixed_chinese_refusal() -> None:
    answer = KnowledgeAnswerService(FailingRepository(), FakeEmbedder()).answer(
        "福建交通", region="福建"
    )

    assert answer.status == "refused"
    assert answer.reply == FIXED_REFUSAL
    assert answer.chunks == ()


def test_programming_error_is_not_disguised_as_a_knowledge_refusal() -> None:
    with pytest.raises(TypeError, match="programming defect"):
        KnowledgeAnswerService(FakeRepository(), BrokenEmbedder()).answer(
            "福建交通", region="福建"
        )

    with pytest.raises(AttributeError, match="repository programming defect"):
        KnowledgeAnswerService(BrokenRepository(), FakeEmbedder()).answer(
            "福建交通", region="福建"
        )


def test_no_chunk_at_or_above_threshold_returns_fixed_refusal() -> None:
    answer = KnowledgeAnswerService(
        FakeRepository(scores=[0.69, 0.2]), FakeEmbedder(), threshold=0.7
    ).answer("低相关问题", region=None)

    assert answer.status == "refused"
    assert answer.reply == FIXED_REFUSAL
    assert answer.chunks == ()


def test_grounded_answer_hard_limits_chunks_and_labels_every_source_in_chinese() -> None:
    answer = KnowledgeAnswerService(
        FakeRepository(scores=[0.99, 0.98, 0.97, 0.96, 0.95]), FakeEmbedder()
    ).answer("厦门资料", region="厦门")

    assert answer.status == "grounded"
    assert len(answer.chunks) == 4
    assert answer.reply.count("【来源：厦门市文化和旅游局】") == 4
    assert "第 5 条资料" not in answer.reply


def test_blank_question_is_refused_without_embedding_or_search() -> None:
    repository = FakeRepository()
    embedder = FakeEmbedder()

    answer = KnowledgeAnswerService(repository, embedder).answer("  ", region="厦门")

    assert answer.status == "refused"
    assert answer.reply == FIXED_REFUSAL
    assert embedder.calls == []
    assert repository.calls == []


@pytest.mark.parametrize("region", [None, "", "北京", "上海"])
def test_non_pilot_region_is_refused_before_embedding_or_search(region) -> None:
    """A missing/unsupported region must never search the three-region pilot corpus."""
    repository = FakeRepository()
    embedder = FakeEmbedder()

    answer = KnowledgeAnswerService(repository, embedder).answer(
        "有哪些值得去的景点？", region=region
    )

    assert answer.status == "refused"
    assert answer.reply == FIXED_REFUSAL
    assert embedder.calls == []
    assert repository.calls == []


@pytest.mark.parametrize(
    "question",
    [
        "请保证云南下周绝对不会下雨。",
        "帮我实时预订厦门轮渡票。",
        "给出厦门所有餐厅的实时空位。",
    ],
)
def test_dynamic_or_transactional_claims_are_refused_before_retrieval(question) -> None:
    """Static curated evidence cannot ground guarantees, booking, or live inventory."""
    repository = FakeRepository()
    embedder = FakeEmbedder()

    answer = KnowledgeAnswerService(repository, embedder).answer(question, region="厦门")

    assert answer.status == "refused"
    assert embedder.calls == []
    assert repository.calls == []
