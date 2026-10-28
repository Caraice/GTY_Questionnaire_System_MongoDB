import {parseNumber,trimText} from './fill-state.mjs?v=0.8.0';

export const OPERATORS = {
  single:[['eq','选择了']],
  multiple:[['contains_any','包含以下任一选项'],['contains_all','包含以下全部选项']],
  text:[['eq','等于'],['contains','包含文本']],
  number:[['eq','等于'],['gt','大于'],['gte','大于或等于'],['lt','小于'],['lte','小于或等于'],['between','介于两数之间（含边界）']],
};
export function resetRule(questions, sourceId) {
  const index = questions.findIndex(q => q.id === sourceId), question = questions[index];
  if (!question) throw new Error('请先添加并保存题目');
  return {source_question_id:sourceId,operator:OPERATORS[question.type][0][0],
    value:question.type === 'single' ? question.options[0]?.id || '' : question.type === 'multiple' ? [] : '',
    target_question_id:questions[index+1]?.id || 'END'};
}
export function targets(questions, sourceId) {
  const index = questions.findIndex(q => q.id === sourceId);
  if (index < 0) return [];
  return questions.slice(index+1);
}
export function rulePayload(questions, rules) {
  return rules.map((rule,index) => {
    const fail = message => {throw new Error(`第 ${index+1} 条规则：${message}`);};
    const question = questions.find(q => q.id === rule.source_question_id);
    if (!question) fail('来源题目不存在，请重新加载');
    if (!(OPERATORS[question.type] || []).some(([op]) => op === rule.operator)) fail('条件与题型不匹配');
    if (rule.target_question_id !== 'END' && !targets(questions,question.id).some(q => q.id === rule.target_question_id)) fail('只能跳到后面的题目或结束填写');
    let value = structuredClone(rule.value);
    const allowed = new Set((question.options || []).map(o => o.id));
    if (question.type === 'single') {
      if (typeof value !== 'string' || !allowed.has(value)) fail('请选择有效选项');
    } else if (question.type === 'multiple') {
      if (!Array.isArray(value) || !value.length || value.some(id => !allowed.has(id)) || new Set(value).size !== value.length) fail('至少选择一个有效且不重复的选项');
    } else if (question.type === 'text') {
      if (typeof value !== 'string' || !trimText(value)) fail('请填写用于判断的文本');
      value = trimText(value);
    } else {
      const numeric = raw => {
        if (!['string','number'].includes(typeof raw)) fail('请输入有效数字');
        const number = typeof raw === 'number' ? raw : parseNumber(raw);
        if (number == null || !Number.isFinite(number)) fail('请输入有限数字，0可以使用');
        if (Number.isInteger(number) && !Number.isSafeInteger(number)) fail('数字超出浏览器安全整数范围');
        return number;
      };
      if (rule.operator === 'between') {
        if (!Array.isArray(value) || value.length !== 2) fail('请填写范围下限和上限');
        value = value.map(numeric);
        if (value[0] > value[1]) fail('范围下限不能大于上限');
      } else value = numeric(value);
    }
    return {source_question_id:question.id,operator:rule.operator,value,target_question_id:rule.target_question_id};
  });
}
