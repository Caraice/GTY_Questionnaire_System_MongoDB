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
    unit: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator('unit')
    @classmethod
    def nonblank_unit(cls, value):
        if value is not None and not value.strip():
            raise ValueError('计量单位不能全部为空白')
        return value.strip() if value is not None else None


Question = Annotated[ChoiceQuestion | TextQuestion | NumberQuestion, Field(discriminator="type")]


class JumpRule(Model):
    """Ordered rules. First match wins; target END means finish."""
    model_config = ConfigDict(allow_inf_nan=False)
    source_question_id: str
    operator: Literal["eq", "contains", "contains_any", "contains_all", "gt", "gte", "lt", "lte", "between"]
    value: JsonValue
    target_question_id: str


class BankCreate(Model):
    name: str = Field(min_length=1, max_length=200)

    @field_validator('name')
    @classmethod
    def bank_name(cls, value):
        if not value.strip():
            raise ValueError('题库名称不能全部为空白')
        return value.strip()


class LibraryQuestionCreate(Model):
    question: Question


class QuestionImport(Model):
    expected_version: int = Field(strict=True, ge=1)
    template_id: str = Field(min_length=1, max_length=64)
    template_version_id: str = Field(min_length=1, max_length=64)


class LibraryVersionCreate(LibraryQuestionCreate):
    expected_version: int = Field(strict=True, ge=1)
    base_version_id: str = Field(min_length=1, max_length=64)


class LibraryFork(LibraryQuestionCreate):
    source_version_id: str = Field(min_length=1, max_length=64)


class LibraryRestore(Model):
    expected_version: int = Field(strict=True, ge=1)
    source_version_id: str = Field(min_length=1, max_length=64)


class ShareCreate(Model):
    username: str = Field(pattern=r'^[A-Za-z0-9_]{3,40}$')


class InstanceVersionUpdate(Model):
    expected_version: int = Field(strict=True, ge=1)
    template_version_id: str = Field(min_length=1, max_length=64)


class LibraryStatisticsMerge(Model):
    version_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=2, max_length=32)
    base_version_id: str = Field(min_length=1, max_length=64)
    option_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    unit_confirmations: dict[str, Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def coherent_scope(self):
        ids = set(self.version_ids)
        if len(ids) != len(self.version_ids) or self.base_version_id not in ids:
            raise ValueError('版本不能重复，基准版本必须在所选版本中')
        if not set(self.option_mappings).issubset(ids) or not set(self.unit_confirmations).issubset(ids):
            raise ValueError('映射和单位确认只能针对所选版本')
        if any(not unit.strip() for unit in self.unit_confirmations.values()):
            raise ValueError('单位确认不能全部为空白')
        self.unit_confirmations = {key: unit.strip() for key, unit in self.unit_confirmations.items()}
        return self


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

