"""Bounded layout values shared by disk loading and the UI bridge."""
DEFAULTS = {'sidebar_width': 208, 'name_width': 320, 'size_width': 95,
            'date_width': 150, 'show_size': True, 'show_date': True}
LIMITS = {'sidebar_width': (180, 300), 'name_width': (180, 700),
          'size_width': (70, 180), 'date_width': (110, 240)}


def clean(values):
    out = {}
    for key, (low, high) in LIMITS.items():
        value = values.get(key)
        if type(value) is int and low <= value <= high:
            out[key] = value
    for key in ('show_size', 'show_date'):
        if type(values.get(key)) is bool:
            out[key] = values[key]
    return out
