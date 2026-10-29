"""Structured semantic changes; option identity is independent of position and label."""
def compare_content(before, after):
    changes = {'title': [], 'options': [], 'validation': [], 'other': []}
    for key, group in [('title', 'title'), ('type', 'other'), ('required', 'other'), ('unit', 'other')]:
        if before.get(key) != after.get(key):
            changes[group].append({'field': key, 'before': before.get(key), 'after': after.get(key)})
    old = {o['id']: o['label'] for o in before.get('options', [])}
    new = {o['id']: o['label'] for o in after.get('options', [])}
    for key in dict.fromkeys([*old, *new]):
        if old.get(key) != new.get(key):
            changes['options'].append({'field': key, 'before': old.get(key), 'after': new.get(key)})
    old_order, new_order = list(old), list(new)
    if old_order != new_order:
        changes['options'].append({'field': 'order', 'before': old_order, 'after': new_order})
    old, new = before.get('validation', {}), after.get('validation', {})
    for key in dict.fromkeys([*old, *new]):
        if old.get(key) != new.get(key):
            changes['validation'].append({'field': key, 'before': old.get(key), 'after': new.get(key)})
    return changes
