import {QUESTION_TYPES} from './editor-state.mjs';

export function previewText(version) {
  if (!version) return '请选择题目和版本';
  const q = version.content;
  const limits = q.validation || {}, rules = [];
  const add = (key, label) => { if (limits[key] !== null && limits[key] !== undefined) rules.push(`${label} ${limits[key]}`); };
  if (q.type === 'multiple') {
    add('min_choices', '至少选择'); add('max_choices', '至多选择'); add('exact_choices', '恰好选择');
  } else if (q.type === 'text') {
    add('min_length', '最少字符'); add('max_length', '最多字符');
  } else if (q.type === 'number') {
    rules.push(`单位：${q.unit || '未声明'}`);
    add('minimum', '最小值'); add('maximum', '最大值'); if (limits.integer_only) rules.push('必须为整数');
  }
  return [`版本 ${version.number} · ${QUESTION_TYPES[q.type]} · ${q.required ? '必答' : '可选'}题`, q.title,
    ...(q.options || []).map((o, i) => `${i + 1}. ${o.label}`),
    `填写限制：${rules.length ? rules.join('；') : '无额外限制'}`].join('\n');
}

export function sourceText(source) {
  return `来源：${source.title || '题库题目'} · 版本 ${source.version_number} · ${source.modified ? '已在本问卷修改' : '与来源版本一致'}`;
}

export function fillSelect(select, items, valueOf, labelOf, placeholder = '请选择') {
  const first = document.createElement('option'); first.value = ''; first.textContent = placeholder;
  select.replaceChildren(first);
  for (const item of items) {
    const option = document.createElement('option'); option.value = valueOf(item); option.textContent = labelOf(item);
    select.append(option);
  }
}
