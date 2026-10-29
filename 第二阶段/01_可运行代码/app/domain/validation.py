"""Strict, side-effect-free validation against an already validated question definition.

Call this only for questions on the server-computed answer path (M3). Missing
answer keys are passed as None. Invalid answers raise ValueError, never coerce
strings/bools into numbers, and never mutate the question or submitted answer.
"""
from math import isfinite


def validate_answer(question: dict, value):
    """Return None for an optional omission, or a valid normalized answer.

    Blank single/text strings and empty multiple-choice lists are omissions.
    Other wrong types are errors even on optional questions. Text is stripped
    before measuring length. Numeric types and choice ID spelling stay intact;
    integer_only accepts finite integral floats such as 1.0 without converting.
    """
    kind = question.get("type")
    if kind not in {"single", "multiple", "text", "number"}:
        raise ValueError("不支持的题型")

    missing = (
        value is None
        or (kind in {"single", "text"} and type(value) is str and not value.strip())
        or (kind == "multiple" and type(value) is list and len(value) == 0)
    )
    if missing:
        if question.get("required", False):
            raise ValueError("此题为必答题")
        return None

    rules = question.get("validation", {})
    if kind == "single":
        if type(value) is not str:
            raise ValueError("单选答案必须是一个选项 ID 字符串")
        allowed = {option["id"] for option in question.get("options", [])}
        if value not in allowed:
            raise ValueError("单选答案包含不存在的选项")
        return value

    if kind == "multiple":
        if type(value) is not list or any(type(item) is not str for item in value):
            raise ValueError("多选答案必须是选项 ID 字符串数组")
        if len(set(value)) != len(value):
            raise ValueError("多选答案不能包含重复选项")
        allowed = {option["id"] for option in question.get("options", [])}
        if not set(value).issubset(allowed):
            raise ValueError("多选答案包含不存在的选项")
        count = len(value)
        exact = rules.get("exact_choices")
        minimum, maximum = rules.get("min_choices"), rules.get("max_choices")
        if exact is not None and count != exact:
            raise ValueError(f"必须恰好选择 {exact} 个选项")
        if minimum is not None and count < minimum:
            raise ValueError(f"至少选择 {minimum} 个选项")
        if maximum is not None and count > maximum:
            raise ValueError(f"至多选择 {maximum} 个选项")
        return list(value)

    if kind == "text":
        if type(value) is not str:
            raise ValueError("文本答案必须是字符串")
        normalized = value.strip()
        minimum, maximum = rules.get("min_length", 0), rules.get("max_length")
        if len(normalized) < minimum:
            raise ValueError(f"至少输入 {minimum} 个字符")
        if maximum is not None and len(normalized) > maximum:
            raise ValueError(f"至多输入 {maximum} 个字符")
        return normalized

    # bool subclasses int: using isinstance(value, int) would accept True/False.
    if type(value) not in (int, float):
        raise ValueError("数字答案必须是数字，不能是布尔值或字符串")
    # Integers are finite. Converting a large int to float here could overflow
    # or lose precision, so inspect only floats and compare original values.
    if type(value) is float and not isfinite(value):
        raise ValueError("数字答案必须是有限数值，不能是 NaN 或 Infinity")
    if rules.get("integer_only", False) and type(value) is float and not value.is_integer():
        raise ValueError("数字答案必须为整数")
    minimum, maximum = rules.get("minimum"), rules.get("maximum")
    if minimum is not None and value < minimum:
        raise ValueError(f"数字不能小于 {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"数字不能大于 {maximum}")
    return value

