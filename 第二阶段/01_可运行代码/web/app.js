import {surveyState} from './survey-state.mjs';
import {deadlineInput, parseDeadline, attachDatePicker} from './deadline-input.mjs';

const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('survey_token') || '';
let busy = false;
let rowUpdates = [];
let refreshSequence = 0;
let historySequence = 0, historyOffset = 0;
let currentView = new URLSearchParams(location.search).get('view') || 'surveys';
function showMessage(text) { $('message').textContent = text; }
async function api(path, options = {}) {
  const headers = {'Content-Type':'application/json', ...options.headers};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, {...options, headers});
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) clearLogin();
    const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    error.status = response.status; throw error;
  }
  return data;
}
function clearLogin() {
  token = ''; sessionStorage.removeItem('survey_token'); rowUpdates = []; ++refreshSequence;
  $('auth').hidden = false; $('workspace').hidden = true;
  $('intro').hidden = false; ++historySequence; $('history-list').replaceChildren();
}
function credentials() { return {username:$('username').value, password:$('password').value}; }
function action(text, handler, style = 'secondary') {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.className = style;
  button.addEventListener('click', () => run(handler)); return button;
}
function renderSurvey(survey) {
  const observedAt = performance.now(), serverAt = Date.parse(survey.server_time);
  const card = document.createElement('article'); card.className = 'survey';
  const heading = document.createElement('h3'); heading.textContent = survey.title;
  const badge = document.createElement('span'); badge.className = 'tag'; heading.append(badge);
  const description = document.createElement('p'); description.textContent = survey.description || '暂无说明';
  const meta = document.createElement('p'); meta.textContent = `${survey.questions.length} 道题 · 创建于 ${new Date(survey.created_at).toLocaleString()}`;
  const deadline = document.createElement('p'); deadline.textContent = survey.deadline ? `截止时间：${new Date(survey.deadline).toLocaleString()}` : '截止时间：不限时';
  const link = document.createElement('code'); link.textContent = new URL(survey.url,location.origin).href;
  const hint = document.createElement('p'); hint.className = 'muted';
  const actions = document.createElement('div'); actions.className = 'actions survey-actions';
  const edit = document.createElement('a'); edit.href = `/edit/${encodeURIComponent(survey.id)}`; edit.textContent = '编辑题目';
  const fill = document.createElement('a'); fill.textContent = '打开问卷';
  const base = `/api/surveys/${encodeURIComponent(survey.id)}`;
  const publish = action('发布问卷', async () => {
    await api(base+'/publish', {method:'POST',body:JSON.stringify({expected_version:survey.version})});
    await refresh(); showMessage('问卷已发布，题目与规则已冻结。');
  }, '');
  const close = action('关闭问卷', async () => {
    await api(base+'/close', {method:'POST',body:JSON.stringify({expected_version:survey.version})});
    await refresh(); showMessage('问卷已关闭，填写和提交入口均已停止。');
  }, 'secondary danger');
  const statistics = document.createElement('a'); statistics.textContent = '查看实时统计'; statistics.href = `/statistics/${encodeURIComponent(survey.id)}`; statistics.className = 'button-link';
  const rules = document.createElement('a'); rules.href = `/rules/${encodeURIComponent(survey.id)}`; rules.className = 'button-link secondary';
  actions.append(edit,rules,publish,statistics,close,fill);
  card.append(heading,description,meta,deadline,link,hint,actions);

  const form = document.createElement('form'); form.className = 'deadline-form'; form.dataset.dirty = 'false';
  const label = document.createElement('label'); label.textContent = '修改截止时间（留空为不限时，按本机时区输入）';
  const input = document.createElement('input'); input.type = 'text'; input.placeholder = 'yyyy/mm/dd hh:mm'; input.autocomplete = 'off'; input.value = deadlineInput(survey.deadline);
  input.addEventListener('input', () => form.dataset.dirty = 'true'); label.append(input); attachDatePicker(input);
  const saveDeadline = document.createElement('button'); saveDeadline.type = 'submit'; saveDeadline.textContent = '保存截止时间'; saveDeadline.className = 'secondary';
  form.append(label,saveDeadline);
  const deadlinePanel = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = '修改截止时间';
  deadlinePanel.append(summary,form); card.append(deadlinePanel);
  form.addEventListener('submit', event => {event.preventDefault(); run(async () => {
    const updated = parseDeadline(input.value);
    await api(base+'/settings', {method:'PATCH',body:JSON.stringify({expected_version:survey.version,deadline:updated})});
    await refresh(); showMessage('截止时间已更新。填写和提交将使用最新期限。');
  });});

  rowUpdates.push(() => {
    const state = surveyState(survey, serverAt + performance.now() - observedAt);
    badge.textContent = state.label; badge.className = `tag tag-${state.state}`;
    edit.hidden = !state.canEdit; publish.hidden = !state.canPublish;
    rules.textContent = state.canEdit ? '配置跳转规则' : '查看跳转规则';
    publish.disabled = busy || survey.questions.length === 0;
    close.hidden = !state.canClose; close.disabled = busy;
    deadlinePanel.hidden = !state.canSetDeadline; input.disabled = busy; saveDeadline.disabled = busy;
    fill.hidden = !state.canFill;
    if (state.canFill) fill.href = survey.url; else fill.removeAttribute('href');
    hint.textContent = state.state === 'draft' ? '发布前请检查题目和规则；发布后定义将被冻结。'
      : state.state === 'closed' ? '题目与规则已冻结。手动关闭后不能重新开放。'
      : state.state === 'expired' ? '题目与规则已冻结。当前不可填写或提交，可延长期限或关闭问卷。'
      : '题目与规则已冻结。可修改截止时间，或关闭问卷停止收集。';
  });
  return card;
}
function editingDeadline() { return Boolean(document.querySelector('.deadline-form[data-dirty="true"], .survey details[open]')); }
async function refresh(background = false) {
  const sequence = ++refreshSequence;
  const surveys = await api('/api/surveys');
  if (sequence !== refreshSequence || !token) return;
  if (background && editingDeadline()) return;
  rowUpdates = []; $('surveys').replaceChildren();
  if (!surveys.length) $('surveys').textContent = '还没有问卷，先创建第一份草稿。';
  for (const survey of surveys) $('surveys').append(renderSurvey(survey));
  rowUpdates.forEach(update => update());
}
async function enterWorkspace() {
  const user = await api('/api/auth/me');
  $('welcome').textContent = `${user.username} 的工作台`;
  $('auth').hidden = true; $('workspace').hidden = false;
  $('intro').hidden = true;
  const target = new URLSearchParams(location.search).get('return');
  if (target && (target === '/library' || /^\/(statistics|response|survey|rules|library)\/[A-Za-z0-9_-]+$/.test(target) || /^\/library\/[A-Za-z0-9_-]+\/statistics$/.test(target))) { location.replace(target); return; }
  await selectView(currentView);
}
async function selectView(view) {
  currentView = ['surveys','history','create'].includes(view) ? view : 'surveys';
  for (const name of ['surveys','history','create']) {
    $(name+'-panel').hidden = name !== currentView;
    $('tab-'+name).setAttribute('aria-pressed',String(name === currentView));
  }
  history.replaceState(null,'',`/?view=${currentView}`);
  if (currentView === 'surveys') await refresh();
  if (currentView === 'history') await refreshHistory();
}
async function refreshHistory(append = false) {
  const sequence = ++historySequence;
  const offset = append ? historyOffset : 0;
  const data = await api(`/api/me/responses?limit=20&offset=${offset}`);
  if (sequence !== historySequence || !token) return;
  if (!append) $('history-list').replaceChildren();
  if (!data.total) $('history-list').textContent = '还没有已提交的问卷。完成填写并提交后，会在这里显示。';
  for (const item of data.items) {
    const card = document.createElement('article'); card.className = 'survey';
    const heading = document.createElement('h3'); heading.textContent = item.title;
    const meta = document.createElement('p'); meta.textContent = `${new Date(item.submitted_at).toLocaleString()} · ${item.anonymous ? '匿名提交' : '实名提交'}`;
    const detail = document.createElement('a'); detail.href = `/response/${encodeURIComponent(item.id)}`; detail.textContent = '查看我填写的内容';
    card.append(heading,meta,detail); $('history-list').append(card);
  }
  historyOffset = offset + data.items.length; $('history-more').hidden = historyOffset >= data.total;
}
async function run(handler) {
  if (busy) return;
  busy = true; showMessage('');
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  rowUpdates.forEach(update => update());
  try { await handler(); } catch (error) { showMessage(error.message); }
  finally {
    busy = false; document.querySelectorAll('button').forEach(button => button.disabled = false);
    rowUpdates.forEach(update => update());
  }
}
$('auth-form').addEventListener('submit', event => {event.preventDefault(); run(async () => {
  const data = await api('/api/auth/login', {method:'POST',body:JSON.stringify(credentials())});
  token = data.access_token; sessionStorage.setItem('survey_token',token); $('password').value = ''; await enterWorkspace();
});});
$('register').addEventListener('click', () => {
  if (!$('auth-form').reportValidity()) return;
  run(async () => {await api('/api/auth/register', {method:'POST',body:JSON.stringify(credentials())}); showMessage('注册成功，请点击登录。');});
});
$('logout').addEventListener('click', () => run(async () => {await api('/api/auth/logout', {method:'POST'}); clearLogin();}));
$('refresh').addEventListener('click', () => run(refresh));
for (const view of ['surveys','history','create']) $('tab-'+view).addEventListener('click',() => run(() => selectView(view)));
$('history-refresh').addEventListener('click',() => run(() => refreshHistory()));
$('history-more').addEventListener('click',() => run(() => refreshHistory(true)));
attachDatePicker($('deadline'));
$('survey-form').addEventListener('submit', event => {event.preventDefault(); run(async () => {
  await api('/api/surveys', {method:'POST',body:JSON.stringify({title:$('title').value,description:$('description').value,
    allow_anonymous:$('anonymous').checked,allow_multiple:$('multiple').checked,
    deadline:parseDeadline($('deadline').value)})});
  $('survey-form').reset(); await selectView('surveys'); showMessage('问卷草稿已保存到 MongoDB。');
});});
setInterval(() => rowUpdates.forEach(update => update()),1000);
function refreshWhenIdle() {
  if (token && currentView === 'surveys' && !busy && !editingDeadline()) {
    refresh(true).catch(error => showMessage(error.message));
  }
}
setInterval(refreshWhenIdle,30000);
window.addEventListener('focus',refreshWhenIdle);
if (token) run(enterWorkspace);
