import {previewText} from './library-state.mjs';
const $ = id => document.getElementById(id), templateId = location.pathname.split('/')[2];
const base = `/api/library/questions/${encodeURIComponent(templateId)}`;
let busy = false, detail, before = null, cursors = [];
const element = (tag, text = '') => { const e = document.createElement(tag); e.textContent = text; return e; };
async function request(path, options = {}) {
  const token = sessionStorage.getItem('survey_token');
  const r = await fetch(path, {...options, headers:{'Content-Type':'application/json', ...(token ? {Authorization:`Bearer ${token}`} : {})}});
  const data = await r.json();
  if (!r.ok) {
    if (r.status === 401) { $('history-login').hidden = false; $('history-login-link').href = '/?return=' + encodeURIComponent(location.pathname); }
    throw new Error(typeof data.detail === 'string' ? data.detail : '输入不合法，请检查后重试。');
  }
  return data;
}
async function run(action) {
  if (busy) return; busy = true; $('history-message').textContent = '';
  for (const b of document.querySelectorAll('button')) b.disabled = true;
  try { await action(); } catch (error) { $('history-message').textContent = error.message; }
  finally { busy = false; for (const b of document.querySelectorAll('button')) b.disabled = false; }
}
function button(text, action) {
  const b = element('button', text); b.type = 'button'; b.className = 'secondary'; b.addEventListener('click', () => run(action)); return b;
}
const names = {title:'题干', type:'题型', required:'必答', unit:'计量单位', order:'选项顺序', min_choices:'至少选择',max_choices:'至多选择',exact_choices:'恰好选择',min_length:'最少字符',max_length:'最多字符',minimum:'最小值',maximum:'最大值',integer_only:'必须为整数'};
function value(v) { if (v == null) return '无'; if (typeof v === 'boolean') return v ? '是' : '否'; return Array.isArray(v) ? v.join(' → ') : String(v); }
function changes(version) {
  const box = element('div');
  if (!version.changes) { box.append(element('p',version.comparison_note)); return box; }
  for (const [key, label] of Object.entries({title:'题干变化',options:'选项变化',validation:'限制变化',other:'题型与必答变化'})) {
    const changes = version.changes[key]; box.append(element('h4', label));
    if (!changes.length) box.append(element('p','无变化'));
    for (const change of changes) {
      // Option IDs only appear when necessary to distinguish additions/deletions.
      const field = names[change.field] || '选项';
      if (change.field === 'order') {
        const oldOptions = detail.versions.find(v => v.id === version.parent_version_id)?.content.options || [];
        const labels = (ids, options) => (ids || []).map(id => options.find(o => o.id === id)?.label || id).join('、') || '无';
        box.append(element('p',`${field}：${labels(change.before,oldOptions)} → ${labels(change.after,version.content.options || [])}`));
      } else box.append(element('p',`${field}：${value(change.before)} → ${value(change.after)}`));
    }
  }
  return box;
}
function originText(v, versions) {
  const label = id => { const found = versions.find(x => x.id === id); return found ? `v${found.number}` : `来源版本 ${id}`; };
  if (v.restored_from_version_id) return `由 ${label(v.restored_from_version_id)} 恢复为 v${v.number}；前一版本 ${label(v.parent_version_id)}`;
  if (v.origin.kind === 'fork' && v.origin.title) return `从题目“${v.origin.title}”的 v${v.origin.version_number} 另存为独立题目`;
  if (v.derived_from_version_id) return `从 ${label(v.derived_from_version_id)} ${v.origin.kind === 'fork' ? '另存为独立题目' : '编辑产生'}${v.parent_version_id ? '；前一版本 ' + label(v.parent_version_id) : ''}`;
  if (v.parent_version_id) return `编辑保存，前一版本 ${label(v.parent_version_id)}`;
  return v.origin.migration ? '由第一阶段问卷快照迁移产生' : '新建题目';
}
async function load() {
  detail = await request(base+'/history-page?page_size=10'+(before ? '&before='+before : '')); $('version-history').replaceChildren(); $('usage-scope').textContent = detail.usage_scope;
  $('history-title').textContent = detail.versions[0]?.content.title || '题目版本历史';
  for (const version of detail.versions) {
    const card = element('article'); card.className = 'question-card'; card.dataset.versionId = version.id;
    card.append(element('h2',`版本 ${version.number}`), element('p',originText(version,detail.versions)));
    const preview = element('pre', previewText(version)); preview.className = 'library-preview'; card.append(preview);
    const diff = element('details'); diff.append(element('summary','查看题干、选项和限制的变化'), changes(version)); card.append(diff);
    const actions = element('div'); actions.className = 'actions';
    const statistics = element('a','查看本版本跨问卷统计'); statistics.href = `/library/${templateId}/statistics#version=${version.id}`; actions.append(statistics);
    const edit = element('a',detail.can_edit ? '编辑此版本／另存为新题' : '另存到我的题库后修改');
    edit.href = `/library/${templateId}/edit?version=${encodeURIComponent(version.id)}`; actions.append(edit);
    if (detail.can_edit) actions.append(button(`恢复 v${version.number} 为新版本`, async () => {
      const r = await request(base+'/restore',{method:'POST',body:JSON.stringify({expected_version:detail.edit_version,source_version_id:version.id})});
      await load(); $('history-message').textContent = `已由 v${version.number} 恢复为 v${r.latest.number}，其余版本均保留。`;
    }));
    card.append(actions, element('h3','引用此版本的问卷'));
    const usageBox=element('div');card.append(usageBox);
    async function loadUsages(page=1) {
      const r=await request(`${base}/versions/${version.id}/usages?page=${page}&page_size=20`);
      usageBox.replaceChildren(element('p',`引用第 ${page} 页`));
      if(!r.items.length) usageBox.append(element('p','暂无你有权查看的问卷引用。'));
      for(const usage of r.items) {
        const p=element('p'),a=element('a',`${usage.title} · 第 ${usage.question_number} 题`);
        a.href=usage.status==='draft' ? `/edit/${usage.survey_id}` : `/statistics/${usage.survey_id}`;
        p.append(a,document.createTextNode(` · ${{draft:'草稿',open:'收集中',expired:'已截止',closed:'已关闭'}[usage.availability] || usage.status}${usage.modified ? ' · 本地内容已修改' : ''}`));usageBox.append(p);
      }
      if(page>1) usageBox.append(button('上一页引用',()=>loadUsages(page-1)));
      if(r.has_more) usageBox.append(button('下一页引用',()=>loadUsages(page+1)));
    }
    await loadUsages();
    if (detail.can_edit) {
      const shareBox = element('details'); shareBox.append(element('summary',`共享版本 ${version.number}`));
      const form = element('form'), label = element('label','接收账号'); const input = element('input'); input.required = true; input.pattern = '[A-Za-z0-9_]{3,40}'; label.append(input);
      const submit = element('button','共享此版本'); submit.type = 'submit'; form.append(label,submit);
      form.addEventListener('submit', event => {event.preventDefault(); run(async () => {
        await request(`${base}/versions/${version.id}/shares`,{method:'POST',body:JSON.stringify({username:input.value})}); await load(); $('history-message').textContent = '已共享指定版本，仅允许预览、导入和另存。';
      });});
      shareBox.append(form);
      for (const grant of await request(`${base}/versions/${version.id}/shares`)) {
        const p = element('p',`${grant.username} · ${grant.active ? '共享中' : '已撤销'} `);
        if (grant.active) p.append(button('撤销共享',async () => {
          await request(`${base}/versions/${version.id}/shares/${grant.grantee_id}`,{method:'DELETE',body:JSON.stringify({expected_version:grant.revision})});
          await load(); $('history-message').textContent = '已撤销：停止新的导入，已有问卷保留原内容。';
        }));
        shareBox.append(p);
      }
      card.append(shareBox);
    }
    $('version-history').append(card);
  }
  const nav=element('p');
  if(cursors.length) nav.append(button('上一页版本',async()=>{before=cursors.pop();await load();}));
  if(detail.next_before) nav.append(button('下一页版本',async()=>{cursors.push(before);before=detail.next_before;await load();}));
  $('version-history').append(nav);
}
$('history-refresh').addEventListener('click', () => {before=null;cursors=[];run(load);}); run(load);
