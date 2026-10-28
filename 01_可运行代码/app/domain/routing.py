"""Data-driven forward routing and authoritative server-side answer paths."""
from math import isfinite

from pydantic import TypeAdapter, ValidationError

from app.models import JumpRule
from app.domain.validation import validate_answer


def validate_rules(questions: list[dict], rules: list[dict]) -> None:
    by_id = {question["id"]: question for question in questions}
    position = {question["id"]: index for index, question in enumerate(questions)}
    if "END" in by_id or len(by_id) != len(questions):
        raise ValueError("题目 ID 必须唯一，且不能使用保留 ID END")
    try:
        definitions = TypeAdapter(list[JumpRule]).validate_python(rules)
    except ValidationError:
        raise ValueError("跳转规则结构或运算符无效，数值必须有限") from None

    def numeric(value):
        return type(value) is int or (type(value) is float and isfinite(value))

    for index, rule in enumerate(definitions, 1):
        def invalid(message):
            raise ValueError(f"第 {index} 条规则：{message}")

        source, target = rule.source_question_id, rule.target_question_id
        if source not in by_id or (target != "END" and target not in by_id):
            invalid("来源题或目标题不存在")
        if target != "END" and position[target] <= position[source]:
            invalid("只允许向后跳转或结束，不能回跳或跳转到自身")
        question = by_id[source]
        kind, operator, value = question["type"], rule.operator, rule.value
        options = {option["id"] for option in question.get("options", [])}
        if kind == "single":
            if operator != "eq" or type(value) is not str or value not in options:
                invalid("单选规则必须使用 eq 和有效选项 ID")
        elif kind == "multiple":
            if operator not in {"contains_any", "contains_all"}:
                invalid("多选规则只支持 contains_any 或 contains_all")
            if type(value) is not list or not value or any(type(item) is not str for item in value):
                invalid("多选条件必须是非空选项 ID 数组")
            if len(set(value)) != len(value) or not set(value).issubset(options):
                invalid("多选条件不能包含重复或未知选项")
        elif kind == "number":
            if operator == "between":
                if type(value) is not list or len(value) != 2 or not all(numeric(item) for item in value):
                    invalid("between 必须提供两个有限数字")
                if value[0] > value[1]:
                    invalid("between 下限不能大于上限")
            elif operator not in {"eq", "gt", "gte", "lt", "lte"} or not numeric(value):
                invalid("数字规则必须使用数字比较和有限数字，不能使用布尔值或字符串")
        elif kind == "text":
            if operator not in {"eq", "contains"} or type(value) is not str or not value.strip():
                invalid("文本规则必须使用 eq 或 contains，并提供非空文本")
        else:
            invalid("未知题型不支持条件跳转")


class PathValidationError(ValueError):
    def __init__(self, message, question_id=None):
        super().__init__(message)
        self.question_id = question_id


def _matches(operator, answer, value):
    if answer is None:
        return False
    if operator == "eq":
        return answer == (value.strip() if type(value) is str else value)
    if operator == "contains":
        return value.strip() in answer
    if operator == "contains_any":
        return any(item in answer for item in value)
    if operator == "contains_all":
        return all(item in answer for item in value)
    if operator == "between":
        return value[0] <= answer <= value[1]
    if operator == "gt":
        return answer > value
    if operator == "gte":
        return answer >= value
    if operator == "lt":
        return answer < value
    if operator == "lte":
        return answer <= value
    raise ValueError("未知跳转运算符")


def _next(questions, rules, current_id, answer):
    for rule in rules:
        if rule["source_question_id"] == current_id and _matches(rule["operator"], answer, rule["value"]):
            return rule["target_question_id"]
    position = next(index for index, question in enumerate(questions) if question["id"] == current_id)
    return questions[position + 1]["id"] if position + 1 < len(questions) else "END"


def next_question(questions: list[dict], rules: list[dict], current_id: str, answer):
    validate_rules(questions, rules)
    current = next((question for question in questions if question["id"] == current_id), None)
    if current is None:
        raise PathValidationError("当前题目不存在", current_id)
    return _next(questions, rules, current_id, validate_answer(current, answer))


def validate_path(questions: list[dict], rules: list[dict], answers: dict):
    """Return (visited IDs, normalized nonempty answers), without trusting client paths."""
    validate_rules(questions, rules)
    if not questions:
        raise PathValidationError("问卷至少需要一道题目")
    by_id = {question["id"]: question for question in questions}
    unknown = set(answers) - set(by_id)
    if unknown:
        raise PathValidationError("答案包含未知题号", sorted(unknown)[0])
    path, normalized = [], {}
    current_id = questions[0]["id"]
    while current_id != "END":
        if current_id not in by_id or current_id in path:
            raise PathValidationError("跳转路径无效或存在循环", current_id)
        path.append(current_id)
        try:
            value = validate_answer(by_id[current_id], answers.get(current_id))
        except ValueError as error:
            raise PathValidationError(str(error), current_id) from None
        if value is not None:
            normalized[current_id] = value
        current_id = _next(questions, rules, current_id, value)
    outside = set(answers) - set(path)
    if outside:
        raise PathValidationError("不能提交跳过题或路径外的答案", sorted(outside)[0])
    return path, normalized

