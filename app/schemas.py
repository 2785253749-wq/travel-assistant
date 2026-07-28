from typing import Literal
from pydantic import BaseModel, Field

class TravelProfile(BaseModel):
    origin: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = None
    budget_cny: int | None = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

class ExtractionResult(BaseModel):
    profile: TravelProfile


class ProfileIssue(BaseModel):
    code: str
    field: str
    message: str

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(min_length=1, max_length=100)

class ChatResponse(BaseModel):
    reply: str
    stage: Literal["collecting", "planned"]
    profile: TravelProfile
