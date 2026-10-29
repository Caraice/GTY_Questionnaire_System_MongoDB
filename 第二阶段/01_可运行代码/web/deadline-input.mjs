// Text input keeps the same yyyy/mm/dd placeholder on every operating system.
export function deadlineInput(iso) {
  if (!iso) return '';
  const d = new Date(iso), pad = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function parseDeadline(value) {
  if (!value.trim()) return null;
  const match = /^(\d{4})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!match) throw new Error('截止时间请按 yyyy/mm/dd hh:mm 输入，例如 2026/10/30 18:00');
  const [y,m,d,h,min,s] = match.slice(1).map(v => Number(v || 0));
  const date = new Date(y,m-1,d,h,min,s);
  if (y < 1000 || date.getFullYear() !== y || date.getMonth() !== m-1 || date.getDate() !== d ||
      date.getHours() !== h || date.getMinutes() !== min || date.getSeconds() !== s) {
    throw new Error('截止时间不是有效的本地日期和时间');
  }
  return date.toISOString();
}

// The text field retains its fixed format; the native picker only replaces the date.
export function attachDatePicker(input) {
  const group = document.createElement('span'); group.className = 'deadline-input-group';
  input.replaceWith(group); group.append(input);
  const picker = document.createElement('input'); picker.type = 'date'; picker.className = 'deadline-date-picker';
  picker.tabIndex = -1; picker.min = '1000-01-01'; picker.max = '9999-12-31'; picker.setAttribute('aria-label','选择截止日期');
  const button = document.createElement('button'); button.type = 'button'; button.className = 'deadline-date-button';
  button.setAttribute('aria-label','选择截止日期'); button.title = '选择年月日';
  button.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M8 2v4m8-4v4M3 10h18"/></svg>';
  group.append(button,picker);
  button.addEventListener('click', () => {
    if (input.disabled) return;
    const date = /^(\d{4})\/(\d{2})\/(\d{2})/.exec(input.value.trim());
    picker.value = date ? `${date[1]}-${date[2]}-${date[3]}` : '';
    if (typeof picker.showPicker === 'function') picker.showPicker();
    else { picker.classList.add('deadline-date-fallback'); picker.focus(); picker.click(); }
  });
  picker.addEventListener('change', () => {
    if (input.disabled) return;
    const time = / (\d{2}:\d{2}(?::\d{2})?)\s*$/.exec(input.value);
    const value = picker.value ? `${picker.value.replaceAll('-','/')} ${time ? time[1] : '00:00'}` : '';
    parseDeadline(value);
    input.value = value;
    input.dispatchEvent(new Event('input', {bubbles:true}));
    picker.classList.remove('deadline-date-fallback'); input.focus();
  });
}
