import {parseNumber, normalizeAnswer, nextQuestion, answerPath, saveAnswer} from './fill-state.mjs?v=0.8.0';

const $ = id => document.getElementById(id);
const shareId = location.pathname.split('/').pop(), endpoint = `/api/fill/${encodeURIComponent(shareId)}`;
const token = sessionStorage.getItem('survey_token');
let loaded = null, observedAt = 0, available = false, checking = false, sending = false;
let answers = {}, drafts = {}, currentId = null, reviewing = false, receipt = null, pending = null, dirty = false;
async function setupNavigation() {
  const link = $('fill-manage'); link.href = '/?return='+encodeURIComponent(location.pathname);
  if (!token) return;
  try {
    const response = await fetch('/api/surveys',{headers:{Authorization:`Bearer ${token}`}});
    if (!response.ok) return;
    const surveys = await response.json(), owned = surveys.find(survey => survey.share_id === shareId);
    link.textContent = owned ? '查看实时统计' : '我的填写记录';
    link.href = owned ? `/statistics/${encodeURIComponent(owned.id)}` : '/?view=history';
  } catch { link.textContent = '返回工作台'; link.href = '/?view=surveys'; }
}
function show(text) { $('fill-message').textContent = text; }
function errorText(data) {
  return typeof data.detail === 'string' ? data.detail : data.detail?.message || '请求内容不合法，请检查填写内容';
}
function updateControls() {
  $('fill-refresh').disabled = checking || sending;
  $('fill-fields').disabled = !available || sending;
  $('fill-prev').disabled = !available || !currentId || answerPath(loaded?.questions || [],loaded?.rules || [],answers).path.indexOf(currentId) <= 0;
  $('review-back').disabled = !available || sending || checking || Boolean(pending);
  $('fill-submit').disabled = sending || checking || (!available && !pending);
  $('fill-submit').textContent = sending ? '正在确认提交…' : pending ? '重试确认这次提交' : '确认提交';
  $('fill-anonymous').disabled = sending || Boolean(pending);
}
async function check() {
  if (checking || sending || receipt) return available;
  if (!token) { $('fill-title').textContent = '请先登录'; show('填写者需要先登录，请返回工作台登录后再打开问卷链接。'); return false; }
  checking = true; updateControls();
  try {
    const response = await fetch(endpoint,{headers:{Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(10000)});
    const data = await response.json();
    if (!response.ok) { available = false; if (!loaded) $('fill-title').textContent = '问卷暂不可填写'; show(errorText(data)); return false; }
    if (loaded && loaded.survey_version !== data.survey_version) {
      available = false; show('问卷内容版本已变化，请复制需要保留的内容后重新加载'); return false;
    }
    const initial = !loaded;
    loaded = data; observedAt = performance.now(); available = true;
    $('fill-title').textContent = data.title;
    $('fill-description').textContent = data.description || '请按题目要求填写，提交前可以返回修改。';
    $('fill-deadline').textContent = data.deadline ? `截止时间：${new Date(data.deadline).toLocaleString()}` : '截止时间：不限时';
    $('anonymous-label').hidden = !data.allow_anonymous;
    if (initial) { currentId = data.questions[0]?.id; renderQuestion(false); }
    if (!pending) show('');
    return true;
  } catch { available = false; show('状态检查失败，填写内容仍保留在本页，请重新检查'); return false; }
  finally { checking = false; updateControls(); }
}
function constraintHint(question) {
  const v = question.validation || {}, lines = [question.required ? '必答题' : '可选题，可留空'];
  if (question.type === 'multiple') {
    if (v.exact_choices != null) lines.push(`恰好选择 ${v.exact_choices} 项`);
    if (v.min_choices != null) lines.push(`至少 ${v.min_choices} 项`);
    if (v.max_choices != null) lines.push(`至多 ${v.max_choices} 项`);
  } else if (question.type === 'text') {
    if (v.min_length) lines.push(`至少 ${v.min_length} 个字符`);
    if (v.max_length != null) lines.push(`至多 ${v.max_length} 个字符`);
  } else if (question.type === 'number') {
    if (v.minimum != null) lines.push(`最小 ${v.minimum}`);
    if (v.maximum != null) lines.push(`最大 ${v.maximum}`);
    if (v.integer_only) lines.push('必须为整数');
    if (question.unit) lines.push(`单位 ${question.unit}`);
  }
  return lines.join(' · ');
}
function renderQuestion(focus = true) {
  reviewing = false;
  $('fill-review').hidden = true; $('fill-form').hidden = false;
  const question = loaded.questions.find(q => q.id === currentId);
  if (!question) { show('问卷没有可填写的题目'); available = false; updateControls(); return; }
  $('question-title').textContent = question.title;
  $('fill-progress').textContent = `当前为问卷第 ${loaded.questions.indexOf(question)+1} 题，共 ${loaded.questions.length} 题（按规则可能跳过部分题目）`;
  $('question-hint').textContent = constraintHint(question);
  $('question-error').textContent = '';
  const box = $('question-inputs'); box.replaceChildren();
  const value = Object.hasOwn(drafts,currentId) ? drafts[currentId] : answers[currentId];
  if (['single','multiple'].includes(question.type)) {
    const group = document.createElement('fieldset'); group.className = 'fill-options';
    const legend = document.createElement('legend'); legend.textContent = question.type === 'single' ? '选择一项' : '选择符合要求的选项';
    group.append(legend);
    for (const option of question.options) {
      const label = document.createElement('label'); label.className = 'fill-choice';
      const input = document.createElement('input'); input.type = question.type === 'single' ? 'radio' : 'checkbox';
      input.name = 'answer'; input.value = option.id;
      input.checked = question.type === 'single' ? value === option.id : Array.isArray(value) && value.includes(option.id);
      const text = document.createElement('span'); text.textContent = option.label;
      label.append(input,text); group.append(label);
    }
    if (question.type === 'single' && !question.required) {
      const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'secondary'; clear.textContent = '清空选择';
      clear.addEventListener('click',() => { for (const input of group.querySelectorAll('input')) input.checked = false; dirty = true; });
      group.append(clear);
    }
    box.append(group);
  } else {
    const label = document.createElement('label'); label.textContent = question.type === 'text' ? '填写内容' : '填写数字';
    const input = document.createElement(question.type === 'text' ? 'textarea' : 'input');
    input.id = 'answer-value'; input.setAttribute('aria-describedby','question-hint question-error');
    if (question.type === 'text') input.rows = 4;
    else { input.type = 'number'; input.step = 'any'; }
    input.value = value == null ? '' : String(value); label.append(input); box.append(label);
  }
  box.oninput = () => { dirty = true; $('question-error').textContent = ''; };
  updateControls();
  if (focus) $('question-title').focus();
}
function rawValue() {
  const question = loaded.questions.find(q => q.id === currentId);
  if (question.type === 'single') return $('question-inputs').querySelector('input:checked')?.value || null;
  if (question.type === 'multiple') return [...$('question-inputs').querySelectorAll('input:checked')].map(input => input.value);
  return $('answer-value').value;
}
function displayAnswer(question,value) {
  if (value == null) return '未填写';
  if (question.type === 'single') return question.options.find(option => option.id === value)?.label || value;
  if (question.type === 'multiple') return value.map(id => question.options.find(option => option.id === id)?.label || id).join('、');
  return String(value);
}
function renderReview() {
  reviewing = true;
  $('fill-form').hidden = true; $('fill-review').hidden = false;
  const list = $('review-answers'); list.replaceChildren();
  for (const id of answerPath(loaded.questions,loaded.rules,answers).path) {
    const question = loaded.questions.find(q => q.id === id);
    const term = document.createElement('dt'); term.textContent = question.title;
    const value = document.createElement('dd'); value.textContent = displayAnswer(question,answers[id]);
    list.append(term,value);
  }
  updateControls(); $('fill-review').scrollIntoView({block:'start'});
}
$('fill-form').addEventListener('submit',async event => {
  event.preventDefault();
  if (!await check() || pending) return;
  try {
    const question = loaded.questions.find(q => q.id === currentId), raw = rawValue();
    drafts[currentId] = raw;
    if (question.type === 'number' && $('answer-value').validity.badInput) throw new Error('请输入有效数字');
    const value = normalizeAnswer(question,question.type === 'number' ? parseNumber(raw) : raw);
    const result = saveAnswer(loaded.questions,loaded.rules,answers,currentId,value);
    answers = result.answers;
    drafts = Object.fromEntries(Object.entries(drafts).filter(([id]) => result.path.includes(id)));
    const next = nextQuestion(loaded.questions,loaded.rules,currentId,value);
    if (next === 'END') renderReview();
    else { currentId = next; renderQuestion(); }
  } catch (error) { $('question-error').textContent = error.message; }
});
$('fill-prev').addEventListener('click',() => {
  if (!available || pending) return;
  drafts[currentId] = rawValue();
  const path = answerPath(loaded.questions,loaded.rules,answers).path, index = path.indexOf(currentId);
  if (index > 0) { currentId = path[index-1]; renderQuestion(); }
});
$('review-back').addEventListener('click',() => {
  if (pending || !available) return;
  const path = answerPath(loaded.questions,loaded.rules,answers).path;
  currentId = path.at(-1); renderQuestion();
});
$('fill-submit').addEventListener('click',async () => {
  if (sending || receipt || checking) return;
  if (!pending) {
    if (!await check()) return;
    if (!answerPath(loaded.questions,loaded.rules,answers).complete) { show('还有题目没有确认，请返回填写'); return; }
    pending = {survey_version:loaded.survey_version, submission_key:crypto.randomUUID(), anonymous:$('fill-anonymous').checked,
      answers:structuredClone(answers)};
  }
  sending = true; updateControls(); show('正在向服务器确认保存，请稍候…');
  try {
    const response = await fetch(endpoint+'/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify(pending),signal:AbortSignal.timeout(15000)});
    const data = await response.json();
    if (!response.ok) {
      if (response.status >= 500) throw new Error('暂时无法确认保存结果');
      pending = null; show(errorText(data));
      if ([401,410].includes(response.status)) available = false;
      const id = data.detail?.question_id;
      if (id && answerPath(loaded.questions,loaded.rules,answers).path.includes(id)) {
        currentId = id; renderQuestion(); $('question-error').textContent = data.detail.message;
      }
      return;
    }
    if (!data.id || !Array.isArray(data.path)) throw new Error('未收到有效回执');
    receipt = data; pending = null; dirty = false;
    $('fill-form').hidden = true; $('fill-review').hidden = true; $('fill-success').hidden = false;
    $('receipt-message').textContent = `答卷已保存，提交编号：${data.id}${data.replayed ? '（已确认原提交，没有重复保存）' : ''}`;
    $('receipt-time').textContent = `保存时间：${new Date(data.submitted_at).toLocaleString()}`;
    $('fill-again').hidden = !loaded.allow_multiple; show('');
    $('fill-success').scrollIntoView({block:'start'});
  } catch {
    show('暂时未能确认保存结果，填写内容仍在本页。请点击“重试确认这次提交”，会复用相同提交键，不会重复保存；确认前不要刷新或改写答案。');
  } finally { sending = false; updateControls(); }
});
$('fill-again').addEventListener('click',async () => {
  receipt = null;
  if (!await check()) { $('fill-success').hidden = false; return; }
  answers = {}; drafts = {}; pending = null; currentId = loaded.questions[0].id; dirty = false;
  $('fill-anonymous').checked = false; $('fill-success').hidden = true; renderQuestion();
});
$('fill-refresh').addEventListener('click',check);
window.addEventListener('focus',check);
window.addEventListener('beforeunload',event => { if (!receipt && (dirty || pending)) {event.preventDefault(); event.returnValue = '';} });
setInterval(check,15000);
setInterval(() => {
  if (!receipt && available && loaded?.deadline && Date.parse(loaded.deadline) <= Date.parse(loaded.server_time)+performance.now()-observedAt) {
    available = false; show('问卷已截止，新提交不可保存。若之前提交结果不明，仍可重试确认原回执。'); updateControls();
  }
},1000);
check();
setupNavigation();
