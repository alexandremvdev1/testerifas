# rifas/utils.py
import random, string, re
from datetime import timedelta
from django.utils import timezone

def gera_protocolo(n=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def abreviar_nome(nome: str) -> str:
    partes = [p for p in (nome or "").strip().split() if p]
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0]
    return f"{partes[0]} {partes[-1][0]}."

def cpf_normalize(cpf: str) -> str:
    return re.sub(r"\D+", "", cpf or "")

def cpf_mask(cpf: str) -> str:
    d = cpf_normalize(cpf).rjust(11, "0")[-11:]
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"

def reserva_expirou(reservado_em, minutos: int) -> bool:
    """
    True = já passou do tempo.
    Precisa informar quantos minutos valem a reserva.
    """
    if not reservado_em:
        return False
    return (timezone.now() - reservado_em) > timedelta(minutes=minutos)
