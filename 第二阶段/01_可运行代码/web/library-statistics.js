import {mergePayload,metricLabels,versionLabel} from './library-statistics-state.mjs';
const $ = id => document.getElementById(id), templateId = location.pathname.split('/')[2];
const base = `/api/library/questions/${encodeURIComponent(templateId)}/statistics`;
let data, busy = false, selection = new Set(), mappings = {}, units = {};
const el = (tag,text = '') => {const e = document.createElement(tag);e.textContent=text;return e;};
async function request(path,options = {}) {
  const token = sessionStorage.getItem('survey_token');
  const r = await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token ? {Authorization:`Bearer ${token}`} : {})}});
  const result = await r.json();
  if (!r.ok) {
    if ([401,404].includes(r.status)) {
      $('version-statistics').replaceChildren(); $('merge-result').hidden = true; $('merge-panel').hidden = true;
    }
    if (r.status === 401) { $('stats-login').hidden = false; $('stats-login').href='/?return='+encodeURIComponent(location.pathname); }
    throw new Error(Array.isArray(result.detail) ? result.detail.map(e => e.msg.replace(/^Value error, /,'')).join('；') : result.detail || '请求失败');
  }
  return result;
}
async function run(action) {
  if (busy) return; busy=true; $('stats-message').textContent='';
  $('stats-refresh').disabled=true; $('stats-versions').disabled=true; $('merge-fields').disabled=true;
  try {await action();} catch(error) {$('stats-message').textContent=error.message;}
  finally {busy=false;$('stats-refresh').disabled=false;$('stats-versions').disabled=!data;$('merge-fields').disabled=false;}
}
function instances(rows, excluded = false) {
  const details=el('details'); details.append(el('summary',`${excluded ? '排除' : '参与'}的题目实例（${rows.length}）`));
  if (!rows.length) details.append(el('p','无'));
  for (const row of rows) {
    const p=el('p'), link=el('a',`${row.title} · 第 ${row.question_number ?? '已移除'} 题 · v${row.version_number ?? '未知'}`);
    link.href=row.status==='draft' ? `/edit/${row.survey_id}` : `/statistics/${row.survey_id}`;
    const state={draft:'草稿',open:'收集中',expired:'已截止',closed:'已关闭'}[row.availability] || row.status;
    p.append(link,document.createTextNode(` · ${state}${excluded ? '：'+row.reasons.join('；') : ''}`));details.append(p);
  }
  return details;
}
function metrics(box, m, unit, fetchPage, expanded=false) {
  const counts=el('div');counts.className='constraint-grid';
  for (const label of metricLabels(m)) counts.append(el('strong',label)); box.append(counts);
  for (const excluded of m.excluded_answer_counts || []) box.append(el('p',`${excluded.reason}：${excluded.count} 次实例作答未计入回答统计`));
  if (m.type==='number') box.append(el('p',`平均值：${m.average == null ? '暂无有效回答' : m.average} · 单位：${unit || '未声明'}`));
  if (m.options) {
    const table=el('table'); const head=el('tr');for(const text of ['选项（基准标签）','稳定选项ID','回答次数']) head.append(el('th',text));table.append(head);
    for(const option of m.options) {const row=el('tr');row.append(el('td',option.label),el('td',option.id),el('td',String(option.count)));table.append(row);}box.append(table);
    if(m.type==='multiple') box.append(el('p',`选项选择总次数：${m.selection_count}`));
  }
  if(m.values) {
    const details=el('details');details.open=expanded;details.append(el('summary',`查看填写内容（显示 ${m.values.length} / ${m.answer_count} 次）`));
    const list=el('ol');list.start=((m.values_page || 1)-1)*m.values_limit+1;for(const value of m.values) list.append(el('li',String(value)));details.append(list);
    details.append(el('p',`第 ${m.values_page || 1} 页，每页 ${m.values_limit} 条；计数和平均值使用全部有效回答。`));
    if(fetchPage) for(const [label,page,enabled] of [['上一页内容',(m.values_page || 1)-1,(m.values_page || 1)>1],['下一页内容',(m.values_page || 1)+1,m.values_has_more]]) {if(!enabled) continue;const b=el('button',label);b.type='button';b.addEventListener('click',()=>run(async()=>{const next=await fetchPage(page);box.replaceChildren();metrics(box,next,unit,fetchPage,true);}));details.append(b);}
    box.append(details);
  }
}
function renderVersions() {
  $('version-statistics').replaceChildren();
  for (const v of data.versions) {
    const card=el('article');card.className='question-card';card.id='version-'+v.id;
    const label=el('label'), check=el('input');check.type='checkbox';check.checked=selection.has(v.id);
    check.setAttribute('aria-label',`选择版本 ${v.number}`);label.className='check-label';label.append(check,document.createTextNode(`选择 v${v.number} 参与合并`));
    check.addEventListener('change',()=>{check.checked ? selection.add(v.id) : selection.delete(v.id);renderConfiguration();$('merge-result').hidden=true;});
    card.append(el('h3',versionLabel(v)),label);
    if(v.metrics) {const content=el('div');card.append(content);metrics(content,v.metrics,v.content.unit,async page=>(await request(base+'?value_page='+page)).versions.find(x=>x.id===v.id).metrics);} else card.append(el('p',(v.definition_errors || ['版本定义无效']).join('；')));
    card.append(instances(v.included_instances),instances(v.excluded_instances,true));$('version-statistics').append(card);
  }
}
function renderConfiguration() {
  const selected=data.versions.filter(v=>selection.has(v.id)), prior=$('merge-base').value;
  $('merge-panel').hidden=selected.length<2;
  $('merge-base').replaceChildren();
  for(const v of selected) {const option=el('option',versionLabel(v));option.value=v.id;$('merge-base').append(option);}
  if(selected.some(v=>v.id===prior)) $('merge-base').value=prior;
  renderMappingFields();
}
function renderMappingFields() {
  $('merge-configuration').replaceChildren();
  const baseVersion=data.versions.find(v=>v.id===$('merge-base').value);
  if(!baseVersion) return;
  for(const v of data.versions.filter(v=>selection.has(v.id))) {
    const group=el('section');group.append(el('h3',`v${v.number} 合并配置`));
    if(v.content.type==='number') {
      if(v.content.unit) group.append(el('p',`版本声明单位：${v.content.unit}（不执行单位换算）`));
      else {
        const label=el('label',`v${v.number} 本次确认单位（不修改旧版本）`), input=el('input');input.maxLength=64;input.value=units[v.id] || '';
        input.placeholder='例如 kg、次/周；无单位请填“无单位”';input.addEventListener('input',()=>units[v.id]=input.value);label.append(input);group.append(label);
      }
    }
    if(v.id!==baseVersion.id && ['single','multiple'].includes(v.content.type) && v.content.type===baseVersion.content.type) {
      const sourceIds=v.content.options.map(o=>o.id), targetIds=baseVersion.content.options.map(o=>o.id);
      const same=sourceIds.length===targetIds.length && sourceIds.every(id=>targetIds.includes(id));
      mappings[v.id] ||= {};
      for(const source of v.content.options) {
        const label=el('label',`v${v.number}：${source.label} [${source.id}] 对应基准选项`), select=el('select');
        const placeholder=el('option','请选择对应关系');placeholder.value='';select.append(placeholder);
        for(const target of baseVersion.content.options) {const option=el('option',`${target.label} [${target.id}]`);option.value=target.id;select.append(option);}
        if(mappings[v.id][source.id]===undefined && same) mappings[v.id][source.id]=source.id;
        select.value=mappings[v.id][source.id] || '';
        select.addEventListener('change',()=>mappings[v.id][source.id]=select.value);label.append(select);group.append(label);
      }
    }
    $('merge-configuration').append(group);
  }
}
async function load() {
  const result=await request(base);data=result;selection=new Set([...selection].filter(id=>data.versions.some(v=>v.id===id)));
  $('stats-title').textContent=(data.versions[0]?.content.title || '题库题目')+' · 跨问卷统计';
  $('stats-time').textContent=`本次统计时间：${new Date(data.generated_at).toLocaleString()}`;
  $('stats-history').href=`/library/${templateId}`;$('stats-history').hidden=!data.source_history_available;
  $('stats-policy').replaceChildren(...Object.values(data.policy).map(text=>el('p',text)));
  $('stats-login').hidden=true;$('merge-result').hidden=true;renderVersions();renderConfiguration();
  const known=new Set(data.versions.map(v=>v.id)), unknown=data.excluded_instances.filter(row=>!known.has(row.version_id));
  $('unknown-exclusions').hidden=!unknown.length;$('unknown-exclusions').replaceChildren(instances(unknown,true));
}
$('stats-refresh').addEventListener('click',()=>run(load));
$('merge-base').addEventListener('change',()=>{mappings={};renderMappingFields();$('merge-result').hidden=true;});
$('merge-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{
  const payload=mergePayload(data.versions,selection,$('merge-base').value,mappings,units);
  const result=await request(base+'/merge',{method:'POST',body:JSON.stringify(payload)});
  const box=$('merge-result');box.replaceChildren(el('h2','本次合并检查结果'),el('p',result.message));
  box.append(el('p','通过兼容检查的版本：'+(result.included_versions.map(v=>`v${v.number}`).join('、') || '无')));
  for(const v of result.included_versions) {
    for(const warning of v.warnings) box.append(el('p',`v${v.number}：${warning}`));
    if(v.unit) box.append(el('p',`v${v.number} 单位：${v.unit}（${v.unit_source}）`));
  }
  if(result.excluded_versions.length) {
    box.append(el('h3','排除的版本'));
    for(const v of result.excluded_versions) box.append(el('p',`v${v.number}：${v.reasons.join('；')}`));
  }
  if(result.metrics) {const content=el('div');box.append(content);metrics(content,result.metrics,result.unit,async page=>(await request(base+'/merge?value_page='+page,{method:'POST',body:JSON.stringify(payload)})).metrics);}
  box.append(instances(result.included_instances),instances(result.excluded_instances,true));box.hidden=false;box.scrollIntoView({block:'start'});
});});
run(async()=>{await load();const id=new URLSearchParams(location.hash.slice(1)).get('version');if(id) document.getElementById('version-'+id)?.scrollIntoView({block:'start'});});
