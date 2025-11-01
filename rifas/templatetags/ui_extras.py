import re
from django import template

register = template.Library()

@register.filter
def regex_replace(value: str, args: str) -> str:
    """
    Uso: {{ texto|regex_replace:"\\D+,"" }}  -> remove tudo que não é dígito
         {{ texto|regex_replace:"pattern,repl" }}
    """
    if value is None:
        return ""
    try:
        pattern, repl = args.split(",", 1)
    except ValueError:
        # se não veio no formato esperado, não quebra o template
        return value
    return re.sub(pattern, repl, str(value))

@register.filter
def digits(value: str) -> str:
    """Retorna apenas os dígitos de uma string (útil para telefone/CPF)."""
    if value is None:
        return ""
    return re.sub(r"\D+", "", str(value))

@register.filter
def whatsapp_link(telefone: str, ddi: str = "55") -> str:
    """
    Monta o link wa.me com DDI (padrão 55 Brasil).
    Uso:
        <a href="{{ cliente.telefone|whatsapp_link }}" target="_blank">WhatsApp</a>
    Ou com DDI custom:
        {{ telefone|whatsapp_link:"351" }}
    """
    num = re.sub(r"\D+", "", str(telefone or ""))
    if not num:
        return ""
    return f"https://wa.me/{ddi}{num}"
