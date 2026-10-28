import {QUESTION_TYPES, isChoice, createQuestion, createOption, changeType, moveItem, questionPayload} from './editor-state.mjs';

const $ = id => document.getElementById(id);
const surveyId = decodeURIComponent(location.pathname.split('/').pop());
let survey = null;
let questions = [];
let dirty = false;
let busy = false;
let conflicted = false;

function message(text) {
  $('editor-message').textContent = text;
  if (text) $('editor-message').scrollIntoView({block:'nearest'});
}
function describeError(detail) {
  if (!Array.isArray(detail)) return typeof detail === 'string' ? detail : '请求失败';
  return detail.map(error => {
    const path = error.loc || [];
    const qi = path.indexOf('questions'), oi = path.indexOf('options');
    const prefix = qi >= 0 ? `第 ${path[qi + 1] + 1} 题${oi >= 0 ? `的第 ${path[oi + 1] + 1} 个选项` : ''}：` : '';
    return prefix + String(error.msg).replace(/^Value error, /, '');
  }).join('\n');
}
async function request(path, options = {}) {
  const token = sessionStorage.getItem('survey_token');
  if (!token) throw new Error('请先返回工作台登录，再进入题目编辑。');
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`}});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(describeError(data.detail)); error.status = response.status; throw error;
  }
  return data;
}
function updateState() {
  const editable = survey && survey.status === 'draft';
  $('editor-fields').disabled = busy || !editable;
  $('save').disabled = busy || !editable || conflicted;
  $('editor-state').textContent = survey ? `版本 ${survey.version} · ${dirty ? '有未保存修改' : '已保存'}` : '尚未加载';
  $('editor-count').textContent = `${questions.length} / 200 道题`;
  $('conflict').hidden = !conflicted;
  for (const button of document.querySelectorAll('[data-add]')) button.disabled = questions.length >= 200;
}
function changed() { dirty = true; message(''); updateState(); }
async function withBusy(action) {
  if (busy) return;
  busy = true; updateState();
  try { await action(); } catch (error) { message(error.message); }
  finally { busy = false; updateState(); }
}
function acceptSurvey(data) {
  survey = data; questions = structuredClone(data.questions); dirty = false; conflicted = false;
  $('survey-title').textContent = data.title;
  document.title = `${data.title} · 题目编辑`;
  $('configure-rules').href = `/rules/${encodeURIComponent(surveyId)}`;
  $('configure-rules').textContent = data.status === 'draft' ? '配置跳转规则' : '查看跳转规则';
  render();
  if (data.status !== 'draft') message('仅草稿问卷可以编辑题目，请返回工作台。');
}
async function load() {
  acceptSurvey(await request(`/api/surveys/${encodeURIComponent(surveyId)}`));
}
function button(text, action, options = {}) {
  const element = document.createElement('button'); element.type = 'button'; element.textContent = text;
  element.className = options.danger ? 'secondary danger' : 'secondary';
  element.disabled = Boolean(options.disabled);
  if (options.label) element.setAttribute('aria-label', options.label);
  element.addEventListener('click', action); return element;
}
function field(text, input) {
  const label = document.createElement('label'); label.append(document.createTextNode(text), input); return label;
}
function textInput(value, maxLength, onInput, hint) {
  const input = document.createElement('input'); input.value = value; input.required = true; input.maxLength = maxLength;
  input.placeholder = hint;
  const validate = () => input.setCustomValidity(input.value.trim() ? '' : '不能全部为空白');
  input.addEventListener('input', () => {validate(); onInput(input.value); changed();});
  validate(); return input;
}
function numberInput(value, {min, integer = false, defaultValue = null} = {}, onInput) {
  const input = document.createElement('input'); input.type = 'number'; input.step = integer ? '1' : 'any';
  if (min !== undefined) input.min = String(min);
  input.value = value ?? '';
  input.placeholder = '不限制';
  input.addEventListener('input', () => {onInput(input.value === '' ? defaultValue : input.valueAsNumber); changed();});
  return input;
}
function checkbox(checked, onChange) {
  const input = document.createElement('input'); input.type = 'checkbox'; input.checked = checked;
  input.addEventListener('change', () => {onChange(input.checked); changed();}); return input;
}
function renderOptions(question, article, questionIndex) {
  const group = document.createElement('fieldset'); group.className = 'options-group';
  const legend = document.createElement('legend'); legend.textContent = '选项'; group.append(legend);
  question.options.forEach((option, index) => {
    const row = document.createElement('div'); row.className = 'option-row'; row.dataset.optionId = option.id;
    row.append(field(`选项 ${index + 1}`, textInput(option.label, 500, value => option.label = value, '输入选项内容')));
    const actions = document.createElement('div'); actions.className = 'actions';
    actions.append(
      button('↑', () => {question.options = moveItem(question.options, option.id, -1); changed(); render();},
        {disabled:index === 0, label:`第 ${questionIndex + 1} 题选项 ${index + 1} 上移`}),
      button('↓', () => {question.options = moveItem(question.options, option.id, 1); changed(); render();},
        {disabled:index === question.options.length - 1, label:`第 ${questionIndex + 1} 题选项 ${index + 1} 下移`}),
      button('删除选项', () => {question.options = question.options.filter(item => item.id !== option.id); changed(); render();},
        {disabled:question.options.length <= 2, danger:true, label:`第 ${questionIndex + 1} 题删除选项 ${index + 1}`}),
    );
    row.append(actions); group.append(row);
  });
  group.append(button('添加选项', () => {question.options.push(createOption()); changed(); render();}, {disabled:question.options.length >= 100}));
  const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = '每题 2–100 个选项。更改文字、调整顺序不会改变已有选项 ID。'; group.append(hint);
  article.append(group);
}
function renderValidation(question, article) {
  const grid = document.createElement('div'); grid.className = 'constraint-grid';
  const validation = question.validation;
  const add = (label, key, options) => grid.append(field(label, numberInput(validation[key], options, value => validation[key] = value)));
  if (question.type === 'multiple') {
    add('至少选择（可选）', 'min_choices', {min:0, integer:true});
    add('至多选择（可选）', 'max_choices', {min:0, integer:true});
    add('恰好选择（可选）', 'exact_choices', {min:0, integer:true});
    const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = '“恰好选择”与“至少／至多”不能同时设置。'; article.append(hint);
  } else if (question.type === 'text') {
    add('最少字符数', 'min_length', {min:0, integer:true, defaultValue:0});
    add('最多字符数（可选）', 'max_length', {min:0, integer:true});
  } else if (question.type === 'number') {
    add('最小值（可选）', 'minimum', {}); add('最大值（可选）', 'maximum', {});
    const integer = field('必须为整数', checkbox(validation.integer_only, value => validation.integer_only = value));
    integer.className = 'check-label'; grid.append(integer);
  }
  article.append(grid);
}
function render() {
  $('question-list').replaceChildren();
  if (!questions.length) {
    const empty = document.createElement('p'); empty.className = 'empty-state'; empty.textContent = '还没有题目。点击上方按钮添加第一道题。'; $('question-list').append(empty);
  }
  questions.forEach((question, index) => {
    const article = document.createElement('article'); article.className = 'question-card'; article.dataset.questionId = question.id;
    const head = document.createElement('div'); head.className = 'section-head';
    const heading = document.createElement('h2'); heading.textContent = `第 ${index + 1} 题 · ${QUESTION_TYPES[question.type]}`;
    const actions = document.createElement('div'); actions.className = 'actions';
    actions.append(
      button('上移', () => {questions = moveItem(questions, question.id, -1); changed(); render();}, {disabled:index === 0, label:`第 ${index + 1} 题上移`}),
      button('下移', () => {questions = moveItem(questions, question.id, 1); changed(); render();}, {disabled:index === questions.length - 1, label:`第 ${index + 1} 题下移`}),
      button('删除题目', () => {questions = questions.filter(item => item.id !== question.id); changed(); render();}, {danger:true, label:`删除第 ${index + 1} 题`}),
    );
    head.append(heading, actions); article.append(head);
    const type = document.createElement('select');
    for (const [value, label] of Object.entries(QUESTION_TYPES)) {
      const option = document.createElement('option'); option.value = value; option.textContent = label; type.append(option);
    }
    type.value = question.type;
    type.addEventListener('change', () => {questions[index] = changeType(question, type.value); changed(); render();});
    article.append(field('题型', type), field('题干', textInput(question.title, 1000, value => question.title = value, '输入题干，不能全部为空白')));
    const typeHint = document.createElement('p'); typeHint.className = 'muted';
    typeHint.textContent = '切换题型会重置校验限制。改为填空并保存后不再保留原选项。'; article.append(typeHint);
    const required = field('必答题', checkbox(question.required, value => question.required = value)); required.className = 'check-label'; article.append(required);
    if (isChoice(question.type)) renderOptions(question, article, index);
    renderValidation(question, article);
    $('question-list').append(article);
  });
  updateState();
}
for (const control of document.querySelectorAll('[data-add]')) {
  control.addEventListener('click', () => {
    questions.push(createQuestion(control.dataset.add)); changed(); render();
    const last = $('question-list').lastElementChild;
    last.scrollIntoView({block:'start'}); last.querySelector('input:not([type=checkbox])').focus();
  });
}
$('editor-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!$('editor-form').reportValidity() || conflicted) return;
  withBusy(async () => {
    message('正在保存…');
    try {
      const data = await request(`/api/surveys/${encodeURIComponent(surveyId)}/questions`, {
        method:'PUT', body:JSON.stringify({expected_version:survey.version, questions:questions.map(questionPayload)}),
      });
      acceptSurvey(data); message('题目已保存到 MongoDB。');
    } catch (error) {
      if (error.status === 409) conflicted = true;
      message(error.message);
    }
  });
});
$('reload').addEventListener('click', () => {
  if (dirty && !confirm('重新加载会放弃当前未保存的修改。请先复制需要保留的内容，确定重新加载吗？')) return;
  withBusy(async () => {await load(); message('已加载最新版本。');});
});
for (const id of ['back', 'return-home']) $(id).addEventListener('click', event => {
  if (dirty && !confirm('当前有未保存的修改，确定返回工作台吗？')) event.preventDefault();
  else dirty = false;
});
window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
withBusy(load);
