// Use the server clock supplied by the API; callers advance it monotonically.
export function surveyState(survey, now) {
  let state = survey.status;
  if (state === 'published') state = survey.deadline && Date.parse(survey.deadline) <= now ? 'expired' : 'open';
  const labels = {draft:'草稿', open:'已发布', expired:'已截止', closed:'已关闭'};
  return {state, label:labels[state] || '未知状态', canEdit:state === 'draft',
    canPublish:state === 'draft', canClose:survey.status === 'published',
    canSetDeadline:survey.status === 'draft' || survey.status === 'published', canFill:state === 'open'};
}

export function localDateTime(iso) {
  if (!iso) return '';
  const date = new Date(iso), pad = number => String(number).padStart(2,'0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
