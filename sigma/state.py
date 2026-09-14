from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)]
JobId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,100}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobInput(Model):
    brief: Text
    slide_count: int = Field(default=3, ge=1, le=10)


class NewJob(Model):
    job_id: JobId
    brand_id: JobId
    task_type: Literal["carousel"] = "carousel"
    input: JobInput


class Slide(Model):
    number: int = Field(ge=1, le=10)
    title: Text
    body: Text
    image_prompt: Text


class Package(Model):
    caption: Text
    slides: list[Slide] = Field(min_length=1, max_length=10)


class Asset(Model):
    number: int = Field(ge=1, le=10)
    url: HttpUrl


class Assets(Model):
    slides: list[Asset] = Field(min_length=1, max_length=10)


class Failure(Model):
    message: Text
    retryable: bool = False


class Success(Model):
    action_id: Text
    outcome: Literal["success"]
    data: Package | Assets


class Failed(Model):
    action_id: Text
    outcome: Literal["failed"]
    error: Failure


Result = Annotated[Success | Failed, Field(discriminator="outcome")]


class Approval(Model):
    action_id: Text
    decision: Literal["approve", "reject"]


class SigmaState(TypedDict):
    schema_version: int
    job: dict
    step: str
    status: str
    artifacts: dict
    approval: str | None
    attempts: dict[str, int]
    error: dict | None
    receipts: dict[str, str]
