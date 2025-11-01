from django import template

register = template.Library()

@register.filter(name="add_class")
def add_class(field, css):
    """Anexa classes CSS ao widget mantendo as existentes."""
    attrs = field.field.widget.attrs.copy()
    prev = attrs.get("class", "")
    attrs["class"] = (prev + " " + css).strip()
    return field.as_widget(attrs=attrs)
