// Browser feedback mirrors domain rules; the server independently rebuilds everything.
export const trimText = value => value.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, '');
export function parseNumber(value) {
  if (trimText(value) === '') return null;
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) throw new Error('请输入有效数字');
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error('数字必须有限');
  return number;
}
export function normalizeAnswer(question, value) {
  const kind = question.type, rules = question.validation || {};
  if (!['single','multiple','text','number'].includes(kind)) throw new Error('不支持的题型');
  const missing = value == null || (['single','text'].includes(kind) && typeof value === 'string' && !trimText(value)) ||
    (kind === 'multiple' && Array.isArray(value) && value.length === 0);
  if (missing) { if (question.required) throw new Error('此题为必答题'); return null; }
  const allowed = new Set((question.options || []).map(option => option.id));
  if (kind === 'single') {
    if (typeof value !== 'string' || !allowed.has(value)) throw new Error('请选择一个有效选项');
  } else if (kind === 'multiple') {
    if (!Array.isArray(value) || value.some(id => typeof id !== 'string' || !allowed.has(id)) || new Set(value).size !== value.length)
      throw new Error('多选答案包含未知或重复选项');
    if (rules.exact_choices != null && value.length !== rules.exact_choices) throw new Error(`必须恰好选择 ${rules.exact_choices} 项`);
    if (rules.min_choices != null && value.length < rules.min_choices) throw new Error(`至少选择 ${rules.min_choices} 项`);
    if (rules.max_choices != null && value.length > rules.max_choices) throw new Error(`至多选择 ${rules.max_choices} 项`);
    return [...value];
  } else if (kind === 'text') {
    if (typeof value !== 'string') throw new Error('文本答案必须为字符串');
    value = trimText(value);
    const length = Array.from(value).length;
    if (length < (rules.min_length || 0)) throw new Error(`至少输入 ${rules.min_length} 个字符`);
    if (rules.max_length != null && length > rules.max_length) throw new Error(`至多输入 ${rules.max_length} 个字符`);
  } else {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('请输入有限数字');
    if (rules.integer_only && !Number.isInteger(value)) throw new Error('必须填写整数');
    if (rules.integer_only && !Number.isSafeInteger(value)) throw new Error('超出浏览器安全整数范围，请联系创建者使用文本题保存');
    if (rules.minimum != null && value < rules.minimum) throw new Error(`不能小于 ${rules.minimum}`);
    if (rules.maximum != null && value > rules.maximum) throw new Error(`不能大于 ${rules.maximum}`);
  }
  return value;
}
export function nextQuestion(questions, rules, currentId, answer) {
  const index = questions.findIndex(question => question.id === currentId);
  if (index < 0) throw new Error('当前题目不存在');
  for (const rule of rules) {
    if (rule.source_question_id !== currentId || answer == null) continue;
    const value = rule.value;
    let matches;
    switch (rule.operator) {
      case 'eq': matches = answer === (typeof value === 'string' ? trimText(value) : value); break;
      case 'contains': matches = answer.includes(trimText(value)); break;
      case 'contains_any': matches = value.some(item => answer.includes(item)); break;
      case 'contains_all': matches = value.every(item => answer.includes(item)); break;
      case 'gt': matches = answer > value; break;
      case 'gte': matches = answer >= value; break;
      case 'lt': matches = answer < value; break;
      case 'lte': matches = answer <= value; break;
      case 'between': matches = answer >= value[0] && answer <= value[1]; break;
      default: throw new Error('未知跳转运算符');
    }
    if (matches) {
      if (rule.target_question_id !== 'END' && questions.findIndex(q => q.id === rule.target_question_id) <= index)
        throw new Error('跳转目标必须向后或结束');
      return rule.target_question_id;
    }
  }
  return questions[index + 1]?.id || 'END';
}
export function answerPath(questions, rules, answers) {
  const path = [];
  let id = questions[0]?.id || 'END';
  while (id !== 'END') {
    if (path.includes(id) || !questions.some(q => q.id === id)) throw new Error('问卷路径无效');
    path.push(id);
    if (!Object.hasOwn(answers, id)) return {path, complete:false};
    id = nextQuestion(questions, rules, id, answers[id]);
  }
  return {path, complete:true};
}
export function saveAnswer(questions, rules, answers, id, raw) {
  const question = questions.find(question => question.id === id);
  if (!question) throw new Error('题目不存在');
  const updated = {...answers, [id]:normalizeAnswer(question,raw)};
  const route = answerPath(questions,rules,updated);
  const reachable = new Set(route.path);
  return {answers:Object.fromEntries(Object.entries(updated).filter(([key]) => reachable.has(key))), ...route};
}
