import {OPERATORS,resetRule,targets,rulePayload} from './rules-state.mjs';
import {element} from './view-utils.mjs';

const $ = id => document.getElementById(id), id = location.pathname.split('/').pop();
const base = '/api/surveys/'+encodeURIComponent(id);
let survey = null, rules = [], busy = false, dirty = false, conflicted = false;
function message(text) { $('rules-message').textContent = text; }
async function request(path,options={}) {
  const token = sessionStorage.getItem('survey_token');
  const response = await fetch(path,{...options,cache:'no-store',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json',...(token ? {Authorization:`Bearer ${token}`} : {})}});
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    const error = new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(e => e.msg).join('\n') : '保存失败，请检查规则');
    error.status = response.status; throw error;
  }
  return data;
}
function update() {
  const editable = survey?.status === 'draft';
  $('rules-fields').disabled = busy || !editable || conflicted;
  $('rules-save').disabled = busy || !editable || conflicted;
  $('rules-reload').disabled = busy;
  $('rules-add').disabled = rules.length >= 1000 || !survey?.questions.length;
  $('rules-conflict').hidden = !conflicted;
  $('rules-state').textContent = survey ? `${editable ? '草稿' : '已发布，规则只读'} · ${rules.length} 条规则 · ${dirty ? '有未保存修改' : '已保存'}` : '尚未加载';
}
function changed() { dirty = true; message(''); update(); }
function accept(data) {
  survey = data; rules = structuredClone(data.rules); dirty = false; conflicted = false;
  $('rules-title').textContent = data.title+' · 跳转规则';
  $('rules-edit-link').href = '/edit/'+encodeURIComponent(id);
  render(); update();
}
async function run(action) {
  if (busy) return;
  busy = true; update();
  try { await action(); }
  catch (error) {
    if (error.status === 409) conflicted = true;
    $('rules-login').hidden = error.status !== 401;
    $('rules-login').href = '/?return='+encodeURIComponent(location.pathname);
    message(error.message || '连接失败，本地规则仍保留，请稍后重试');
  } finally {busy = false; update();}
}
function field(label,control) { const node = element('label',label); node.append(control); return node; }
function select(options,value,change) {
  const node = element('select');
  for (const [key,label] of options) node.add(new Option(label,key));
  node.value = value;
  node.addEventListener('change',() => {change(node.value); changed();});
  return node;
}
function questionLabel(question) { return `第 ${survey.questions.indexOf(question)+1} 题：${question.title}`; }
function action(label,handler,disabled=false) {
  const node = element('button',label,'secondary'); node.type = 'button'; node.disabled = disabled;
  node.addEventListener('click',() => {handler(); changed(); render();}); return node;
}
function renderValue(rule,question,box) {
  if (question.type === 'single') {
    box.append(field('回答选项',select(question.options.map(o => [o.id,o.label]),rule.value,value => {rule.value=value;})));
  } else if (question.type === 'multiple') {
    const group = element('fieldset','','fill-options'); group.append(element('legend','用于判断的选项'));
    for (const option of question.options) {
      const input = element('input'); input.type = 'checkbox'; input.checked = Array.isArray(rule.value) && rule.value.includes(option.id);
      input.addEventListener('change',() => {rule.value = input.checked ? [...rule.value,option.id] : rule.value.filter(id => id !== option.id); changed();});
      const label = element('label','','fill-choice'); label.append(input,element('span',option.label)); group.append(label);
    }
    box.append(group);
  } else {
    const add = (label,value,onInput) => {
      const input = element('input'); input.type = 'text'; input.value = value ?? '';
      if (question.type === 'number') input.inputMode = 'decimal';
      else input.maxLength = 10000;
      input.addEventListener('input',() => {onInput(input.value); changed();}); box.append(field(label,input));
    };
    if (rule.operator === 'between') {
      if (!Array.isArray(rule.value)) rule.value=['',''];
      add('范围下限',rule.value[0],v => {rule.value[0]=v;});
      add('范围上限',rule.value[1],v => {rule.value[1]=v;});
    } else add(question.type === 'text' ? '匹配文本' : '比较数值',rule.value,v => {rule.value=v;});
  }
}
function render() {
  const list = $('rules-list'); list.replaceChildren();
  if (!rules.length) list.append(element('p',survey.questions.length ? '尚未设置跳转，填写时将按题目顺序继续。点击下方按钮添加。' : '请先返回编辑题目，添加并保存题目后再配置规则。','empty-state'));
  rules.forEach((rule,index) => {
    const question = survey.questions.find(q => q.id === rule.source_question_id);
    const card = element('article','','question-card'), heading = element('div','','section-head');
    const actions = element('div','','actions');
    actions.append(action('上移',() => {[rules[index-1],rules[index]]=[rules[index],rules[index-1]];},index === 0),
      action('下移',() => {[rules[index],rules[index+1]]=[rules[index+1],rules[index]];},index === rules.length-1),
      action('删除规则',() => {rules.splice(index,1);}));
    heading.append(element('h2',`规则 ${index+1}`),actions); card.append(heading);
    card.append(field('来源题目',select(survey.questions.map(q => [q.id,questionLabel(q)]),rule.source_question_id,value => {rules[index]=resetRule(survey.questions,value); render();})));
    if (!question) {card.append(element('p','来源题目已不存在，请删除或重新选择。')); list.append(card); return;}
    card.append(field('当回答满足',select(OPERATORS[question.type],rule.operator,value => {rule.operator=value; rule.value=value === 'between' ? ['',''] : question.type === 'multiple' ? rule.value : ''; render();})));
    const box = element('div','','constraint-grid'); renderValue(rule,question,box); card.append(box);
    const destinations = targets(survey.questions,question.id).map(q => [q.id,questionLabel(q)]);
    destinations.push(['END','结束填写']);
    card.append(field('跳转到',select(destinations,rule.target_question_id,value => {rule.target_question_id=value;})));
    list.append(card);
  });
  update();
}
$('rules-add').addEventListener('click',() => {rules.push(resetRule(survey.questions,survey.questions[0].id)); changed(); render();});
$('rules-save').addEventListener('click',() => run(async () => {
  const payload = rulePayload(survey.questions,rules);
  accept(await request(base+'/rules',{method:'PUT',body:JSON.stringify({expected_version:survey.version,rules:payload})}));
  message('跳转规则已保存。回到问卷列表发布后，填写时会自动执行。');
}));
$('rules-reload').addEventListener('click',() => {
  if (dirty && !confirm('重新加载会放弃未保存规则。请先复制需要保留的内容，确定重新加载吗？')) return;
  run(async () => {accept(await request(base)); message('已加载最新版本。');});
});
window.addEventListener('beforeunload',event => {if (dirty) {event.preventDefault(); event.returnValue='';}});
run(async () => accept(await request(base)));
