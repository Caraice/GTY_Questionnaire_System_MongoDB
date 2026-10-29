"""Explicit compatibility checks, never match options by their display text."""
def compatibility(base, version, mapping=None, unit=None, base_unit=None):
    reasons, warnings = [], []
    source, target = version['content'], base['content']
    if source['type'] != target['type']:
        return {'reasons': ['题型与基准版本不一致'], 'warnings': [], 'mapping': None, 'unit': None}
    kind = source['type']
    resolved = None
    if kind in ('single', 'multiple'):
        ids = [o['id'] for o in source['options']]
        target_ids = [o['id'] for o in target['options']]
        if mapping is None:
            if set(ids) == set(target_ids):
                resolved = {key: key for key in ids}
            else:
                reasons.append('选项ID集合不同，尚未配置完整映射；相同选项文字不能作为对应依据')
        elif set(mapping) != set(ids) or len(set(mapping.values())) != len(target_ids) or set(mapping.values()) != set(target_ids) or len(ids) != len(target_ids):
            reasons.append('选项映射必须完整、一一对应到基准选项，不能漏项、折叠或使用未知ID')
        else:
            resolved = mapping
        if resolved:
            labels = {o['id']: o['label'] for o in target['options']}
            if any(o['label'] != labels[resolved[o['id']]] for o in source['options']):
                warnings.append('对应选项的文字有变化，按稳定ID或本次明确映射合并，结果使用基准版本的标签')
    elif mapping:
        reasons.append('非选择题不能配置选项映射')
    effective_unit = source.get('unit') or unit
    if kind == 'number':
        if source.get('unit') and unit is not None and unit != source['unit']:
            reasons.append('不能覆盖版本已声明的计量单位；本次不执行单位换算')
        if not effective_unit:
            reasons.append('计量单位未声明，请明确确认本次统计单位后再合并')
        elif not base_unit:
            reasons.append('基准版本的计量单位未声明')
        elif effective_unit != base_unit:
            reasons.append(f'计量单位不一致：{effective_unit} 与 {base_unit}；本次不执行单位换算')
    elif unit is not None:
        reasons.append('只有数字题可以确认计量单位')
    return {'reasons': reasons, 'warnings': warnings, 'mapping': resolved, 'unit': effective_unit if kind == 'number' else None}
