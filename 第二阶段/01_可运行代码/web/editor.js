import {QUESTION_TYPES, isChoice, createQuestion, createOption, changeType, moveItem, questionPayload} from './editor-state.mjs';
import {previewText, sourceText, fillSelect} from './library-state.mjs';

const $ = id => document.getElementById(id);
const surveyId = decodeURIComponent(location.pathname.split('/').pop());
const editingTemplate = /^\/library\/[A-Za-z0-9_-]+\/edit$/.test(location.pathname) ? location.pathname.split('/')[2] : null;
const libraryMode = location.pathname === '/library/new' || Boolean(editingTemplate);
let libraryDetail = null, baseVersion = null, updatingInstance = null;
let pickerQuestions = [], pickerVersions = [];
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
  $('save').disabled = busy || !editable || conflicted || Boolean(editingTemplate && libraryDetail && !libraryDetail.can_edit);
  $('editor-state').textContent = libraryMode ? '新建内容版本 · 尚未保存' : survey ? `版本 ${survey.version} · ${dirty ? '有未保存修改' : '已保存'}` : '尚未加载';
  $('editor-count').textContent = `${questions.length} / ${libraryMode ? 1 : 200} 道题`;
  $('picker-fields').disabled = busy || !editable || conflicted;
  $('picker-import').disabled = !pickerVersions.some(v => v.id === $('picker-version').value);
  $('picker-bank').disabled = Boolean(updatingInstance); $('picker-question').disabled = Boolean(updatingInstance);
  $('picker-bank').parentElement.hidden = Boolean(updatingInstance);
  if ($('fork-question')) $('fork-question').disabled = busy;
  $('conflict').hidden = !conflicted;
  for (const button of document.querySelectorAll('[data-add]')) button.disabled = questions.length >= (libraryMode ? 1 : 200);
  if ($('from-bank')) $('from-bank').disabled = busy || !editable || conflicted;
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
  if (libraryMode) {
    if (editingTemplate) {
      const loaded = await request(`/api/library/questions/${editingTemplate}`);
      const selected = new URLSearchParams(location.search).get('version') || loaded.latest_version_id;
      const version = loaded.versions.find(v => v.id === selected);
      if (!version) throw new Error('此版本不存在或已失去共享权限。');
      libraryDetail = loaded; baseVersion = version;
    }
    questions = []; dirty = false; conflicted = false;
    survey = {status:'draft', version:1, question_sources:[]};
    $('survey-title').textContent = '新建题库题目';
    document.querySelector('.eyebrow').textContent = '题库管理 · 新建题目';
    document.title = '新建题库题目';
    $('configure-rules').textContent = '返回题库'; $('configure-rules').href = '/library';
    $('save').textContent = '保存为题目版本 1';
    document.querySelector('.lead').textContent = '选择一种题型，填写题干、选项与校验限制。保存后可加入一个或多个题库。';
    document.querySelector('main > p.muted').textContent = '新题目保存在“全部题目”中，可在题库管理中设置归属。';
    if (editingTemplate) {
      questions = [{...structuredClone(baseVersion.content), id:crypto.randomUUID()}];
      survey.version = libraryDetail.edit_version;
      $('survey-title').textContent = `编辑题目 · 以 v${baseVersion.number} 为基础`;
      document.title = '编辑题库题目'; document.querySelector('.eyebrow').textContent = '题库管理 · 编辑题目';
      $('save').textContent = '保存为新版本';
      $('configure-rules').href = `/library/${editingTemplate}`; $('configure-rules').textContent = '版本历史与引用';
      $('conflict').textContent = '其他窗口已产生新版本，本地修改仍保留。请先复制需要保留的内容，再重新加载；不会覆盖旧版本。';
      document.querySelector('.lead').textContent = libraryDetail.can_edit ? '保存为新版本会追加到当前题目；另存为新题会建立独立题目。已有问卷继续使用原版本。' : '这是共享版本，可以改好后另存为自己的题目。你不能修改对方的原题。';
    }
    render(); return;
  }
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
    const unit = document.createElement('input'); unit.value = question.unit || ''; unit.maxLength = 64;
    unit.placeholder = '例如 kg、次/周；无单位请填“无单位”';
    unit.addEventListener('input', () => {question.unit = unit.value || undefined; changed();});
    grid.append(field('计量单位（可选）', unit));
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
    const source = survey?.question_sources?.find(s => s.instance_id === question.id);
    if (source) {
      const origin = document.createElement('p'); origin.className = 'muted'; origin.textContent = sourceText(source);
      article.append(origin);
      const detail = document.createElement('a'); detail.href = `/library/${source.template_id}`; detail.textContent = '查看来源版本与引用'; detail.target = '_blank'; detail.rel = 'noopener'; article.append(detail);
      if (source.available_update) {
        const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = `已有可用新版 v${source.available_update.number}，当前仍使用 v${source.version_number}。`;
        article.append(hint,button('预览并主动更新此题', () => openUpdate(source)));
      }
    }
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
      if (libraryMode) {
        if (questions.length !== 1) throw new Error('请先添加一道题目。');
        const path = editingTemplate ? `/api/library/questions/${editingTemplate}/versions` : '/api/library/questions';
        await request(path, {method:'POST', body:JSON.stringify({question:questionPayload(questions[0]),
          ...(editingTemplate ? {expected_version:libraryDetail.edit_version,base_version_id:baseVersion.id} : {}),
        })});
        dirty = false; location.assign(editingTemplate ? `/library/${editingTemplate}` : '/library'); return;
      }
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
if (!libraryMode) {
  const open = button('从题库添加', () => {
    if (dirty) { message('请先保存当前题目，再从题库添加。'); return; }
    withBusy(async () => {
      updatingInstance = null; $('picker-import').textContent = '添加所选版本到问卷';
      document.querySelector('#bank-picker h2').textContent = '从题库添加';
      document.querySelector('#bank-picker > p').textContent = '先保存问卷中的修改，再选择具体版本。每次添加都会立即保存为一道独立题目。';
      const banks = await request('/api/question-banks');
      fillSelect($('picker-bank'), banks, b => b.id, b => b.name, '全部题目（含未归类）');
      const shared = document.createElement('option'); shared.value = '__shared__'; shared.textContent = '共享给我的题目'; $('picker-bank').append(shared);
      await loadPickerQuestions(); $('bank-picker').hidden = false; $('bank-picker').scrollIntoView({block:'start'});
    });
  });
  open.id = 'from-bank'; document.querySelector('.add-types').append(open);
}
if (editingTemplate) {
  const fork = button('另存为新题', () => withBusy(async () => {
    if (!$('editor-form').reportValidity() || questions.length !== 1) return;
    const result = await request(`/api/library/questions/${editingTemplate}/fork`,{method:'POST',body:JSON.stringify({
      source_version_id:baseVersion.id, question:questionPayload(questions[0]),
    })});
    dirty = false; location.assign(`/library/${result.id}`);
  }));
  fork.id = 'fork-question'; document.querySelector('.editor-toolbar .actions').append(fork);
}
function openUpdate(source) {
  if (dirty) { message('请先保存当前修改，再预览新版。更新会替换此实例的内容。'); return; }
  withBusy(async () => {
    updatingInstance = source.instance_id;
    document.querySelector('#bank-picker h2').textContent = '更新题目引用';
    document.querySelector('#bank-picker > p').textContent = '确认后替换当前实例的题干、选项与限制，并保留实例 ID、排序和其他题目。更新前会重新检查跳转引用。';
    pickerVersions = await request(`/api/library/questions/${source.template_id}/versions`);
    fillSelect($('picker-question'), [{id:source.template_id,title:source.title}], q => q.id, q => q.title || '来源题目');
    $('picker-question').value = source.template_id;
    fillSelect($('picker-version'), pickerVersions, v => v.id, v => `版本 ${v.number}`);
    $('picker-version').value = source.available_update.id;
    $('picker-preview').textContent = previewText(pickerVersions.find(v => v.id === source.available_update.id));
    $('picker-import').textContent = '用所选版本更新此题（替换本地内容）';
    $('bank-picker').hidden = false; $('bank-picker').scrollIntoView({block:'start'});
  });
}
async function loadPickerQuestions() {
  const bank = $('picker-bank').value;
  pickerQuestions = await request(bank === '__shared__' ? '/api/library/shared' : '/api/library/questions' + (bank ? '?bank_id=' + encodeURIComponent(bank) : ''));
  fillSelect($('picker-question'), pickerQuestions, q => q.id, q => q.latest?.content.title || q.id);
  pickerVersions = []; fillSelect($('picker-version'), [], v => v.id, v => v.number);
  $('picker-preview').textContent = pickerQuestions.length ? '请选择题目和版本。' : '此题库暂无题目，请先在题库管理中添加。';
}
$('picker-bank').addEventListener('change', () => withBusy(loadPickerQuestions));
$('picker-question').addEventListener('change', () => withBusy(async () => {
  const id = $('picker-question').value;
  pickerVersions = id ? await request(`/api/library/questions/${id}/versions`) : [];
  fillSelect($('picker-version'), pickerVersions, v => v.id, v => `版本 ${v.number}`);
  $('picker-version').value = pickerVersions[0]?.id || '';
  $('picker-preview').textContent = previewText(pickerVersions[0]);
}));
$('picker-version').addEventListener('change', () => {
  $('picker-preview').textContent = previewText(pickerVersions.find(v => v.id === $('picker-version').value)); updateState();
});
$('picker-close').addEventListener('click', () => $('bank-picker').hidden = true);
$('picker-import').addEventListener('click', () => withBusy(async () => {
  if (dirty) throw new Error('请先保存当前修改，再添加题库题目。');
  try {
    const path = updatingInstance ? `/api/surveys/${surveyId}/instances/${updatingInstance}/version` : `/api/surveys/${surveyId}/imports`;
    const data = await request(path, {method:'POST', body:JSON.stringify({
      expected_version:survey.version, ...(!updatingInstance ? {template_id:$('picker-question').value} : {}), template_version_id:$('picker-version').value,
    })});
    acceptSurvey(data); message(updatingInstance ? '已更新当前实例，实例 ID 与排序保留，跳转引用已重新检查。' : '已添加并保存一道独立题目，可继续添加同一版本。');
    if (updatingInstance) $('bank-picker').hidden = true;
  } catch (error) {
    if (error.status === 409) conflicted = true;
    throw error;
  }
}));
withBusy(load);
