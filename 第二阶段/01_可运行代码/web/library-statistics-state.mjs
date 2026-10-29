export const TYPE_NAMES = {single:'单选题',multiple:'多选题',text:'文本填空',number:'数字填空'};

export function mergePayload(versions, selected, baseId, mappings = {}, units = {}) {
  const version_ids = versions.filter(v => selected.has(v.id)).map(v => v.id);
  const option_mappings = {}, unit_confirmations = {};
  for (const id of version_ids) {
    if (id !== baseId && mappings[id] && Object.values(mappings[id]).some(Boolean))
      option_mappings[id] = Object.fromEntries(Object.entries(mappings[id]).filter(([,target]) => target));
    if (units[id]?.trim()) unit_confirmations[id] = units[id].trim();
  }
  return {version_ids,base_version_id:baseId,option_mappings,unit_confirmations};
}

export function metricLabels(metrics) {
  return [`涉及问卷数：${metrics.survey_count}`,`答卷数：${metrics.response_count}`,`题目回答次数：${metrics.answer_count}`];
}

export function versionLabel(v) { return `v${v.number} · ${TYPE_NAMES[v.content.type]} · ${v.content.title}`; }
