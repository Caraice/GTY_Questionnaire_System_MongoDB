"""Version-one API contracts. Stable IDs survive question reordering."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Model):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)


class SurveyCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    allow_anonymous: bool = False
    allow_multiple: bool = False
    deadline: datetime | None = None

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("标题不能全部为空白")
        return value.strip()

    @field_validator("deadline")
    @classmethod
    def aware_deadline(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("截止时间必须带时区，例如 +08:00")
        return value


class Option(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=500)

    @field_validator("label")
    @classmethod
    def nonblank_label(cls, value):
        if not value.strip():
            raise ValueError("选项内容不能全部为空白")
        return value.strip()


class ChoiceValidation(Model):
    min_choices: int | None = Field(default=None, ge=0)
    max_choices: int | None = Field(default=None, ge=0)
    exact_choices: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent(self):
        if self.exact_choices is not None and (self.min_choices is not None or self.max_choices is not None):
            raise ValueError("精确选择数量和上下限不能同时设置")
        if self.min_choices is not None and self.max_choices is not None and self.min_choices > self.max_choices:
            raise ValueError("最少选择数不能大于最多选择数")
        return self


class TextValidation(Model):
    min_length: int = Field(default=0, ge=0)
    max_length: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent(self):
        if self.max_length is not None and self.min_length > self.max_length:
            raise ValueError("最短长度不能大于最长长度")
        return self


class NumberValidation(Model):
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    integer_only: bool = False

    @model_validator(mode="after")
    def coherent(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("最小值不能大于最大值")
        return self


class QuestionBase(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=1000)
    required: bool = False

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("题干不能全部为空白")
        return value.strip()


class ChoiceQuestion(QuestionBase):
    type: Literal["single", "multiple"]
    options: list[Option] = Field(min_length=2, max_length=100)
    validation: ChoiceValidation = Field(default_factory=ChoiceValidation)

    @model_validator(mode="after")
    def valid_options(self):
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError("选项 ID 必须唯一")
        constraints = self.validation.model_dump()
        if self.type == "single" and any(v is not None for v in constraints.values()):
            raise ValueError("单选题不接受多选数量限制")
        if any(v is not None and v > len(self.options) for v in constraints.values()):
            raise ValueError("选择数量不能超过选项数")
        return self


class TextQuestion(QuestionBase):
    type: Literal["text"]
    validation: TextValidation = Field(default_factory=TextValidation)


class NumberQuestion(QuestionBase):
    type: Literal["number"]
    validation: NumberValidation = Field(default_factory=NumberValidation)


Question = Annotated[ChoiceQuestion | TextQuestion | NumberQuestion, Field(discriminator="type")]


class JumpRule(Model):
    """Ordered rules. First match wins; target END means finish."""
    model_config = ConfigDict(allow_inf_nan=False)
    source_question_id: str
    operator: Literal["eq", "contains", "contains_any", "contains_all", "gt", "gte", "lt", "lte", "between"]
    value: JsonValue
    target_question_id: str


class QuestionSet(Model):
    expected_version: int = Field(ge=1, strict=True)
    questions: list[Question] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_questions(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("题目 ID 必须唯一")
        return self


class RuleSet(Model):
    expected_version: int = Field(ge=1, strict=True)
    rules: list[JumpRule] = Field(max_length=1000)


class VersionedAction(Model):
    expected_version: int = Field(ge=1, strict=True)


class DeadlineUpdate(VersionedAction):
    deadline: datetime | None  # Required: explicit null removes the deadline.

    @field_validator("deadline")
    @classmethod
    def aware_deadline(cls, value):
        return SurveyCreate.aware_deadline(value)


class Submission(Model):
    # JsonValue preserves JSON types (notably bool), including inside arrays.
    # Question-specific type checks belong to domain.validation, not a coercing union.
    model_config = ConfigDict(allow_inf_nan=False)

    survey_version: int = Field(ge=1, strict=True)
    submission_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,100}$")
    anonymous: bool = False
    answers: dict[str, JsonValue]

