from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from django.db.models import Count, Q
from .models import Rifa, Pedido, Coupon, CouponRedemption, DiscountRule, DiscountApplication, Cliente

def _money(x): 
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def aplicar_regras(rifa: Rifa, qtd: int, subtotal: Decimal):
    """Aplica DiscountRules ativas, respeitando janelas/prioridade/exclusiva."""
    now = timezone.now()
    regras = (DiscountRule.objects
              .filter(Q(rifa__isnull=True) | Q(rifa=rifa), ativo=True)
              .order_by("-prioridade", "-created_at"))
    aplicado = []
    parcial = subtotal

    for r in regras:
        # gatilhos
        if r.inicio and now < r.inicio: 
            continue
        if r.fim and now > r.fim: 
            continue
        if r.qtd_numeros_min and qtd < r.qtd_numeros_min:
            continue

        # benefício
        if r.tipo == DiscountRule.PERCENTUAL:
            desc = _money(parcial * (r.valor/Decimal("100")))
        else:
            desc = _money(r.valor)
        if desc <= 0: 
            continue

        aplicado.append((r, desc))
        parcial = max(_money(parcial - desc), Decimal("0.00"))

        if r.exclusiva:
            break

    total_regras = _money(sum(v for _, v in aplicado)) if aplicado else Decimal("0.00")
    return total_regras, parcial, aplicado

def validar_cupom(codigo: str, rifa: Rifa, cliente: Cliente, subtotal_pos_regras: Decimal, qtd: int):
    if not codigo: 
        return None, Decimal("0.00")
    codigo = codigo.strip().upper()
    try:
        c = Coupon.objects.get(codigo=codigo, ativo=True)
    except Coupon.DoesNotExist:
        return None, Decimal("0.00")

    now = timezone.now()
    if c.inicio and now < c.inicio: 
        return None, Decimal("0.00")
    if c.fim and now > c.fim: 
        return None, Decimal("0.00")
    if c.rifa_id and c.rifa_id != rifa.id:
        return None, Decimal("0.00")
    if subtotal_pos_regras < (c.min_compra or Decimal("0.00")):
        return None, Decimal("0.00")
    if c.qtd_min_numeros and qtd < c.qtd_min_numeros:
        return None, Decimal("0.00")
    if c.so_primeira_compra and cliente.pedidos.exists():
        return None, Decimal("0.00")

    # limites de uso
    if c.max_uso_global is not None and c.uso_cache >= c.max_uso_global:
        return None, Decimal("0.00")
    if c.max_uso_por_cliente is not None:
        usos_cliente = CouponRedemption.objects.filter(coupon=c, cliente=cliente).count()
        if usos_cliente >= c.max_uso_por_cliente:
            return None, Decimal("0.00")

    # calcular valor
    if c.tipo == Coupon.PERCENTUAL:
        desc = _money(subtotal_pos_regras * (c.valor/Decimal("100")))
    else:
        desc = _money(c.valor)
    desc = min(desc, subtotal_pos_regras)  # nunca negativo

    return c, desc

def precificar(rifa: Rifa, qtd: int, cliente: Cliente, cupom_codigo: str | None):
    subtotal = _money(Decimal(qtd) * rifa.preco_numero)

    # Descontos automáticos
    desc_regras, parcial, regras_aplicadas = aplicar_regras(rifa, qtd, subtotal)

    # Cupom
    c, desc_cupom = validar_cupom(cupom_codigo, rifa, cliente, parcial, qtd)
    if c and c.acumulavel is False and desc_regras > 0:
        # cupom não acumulável com regras -> ignora regras
        parcial = subtotal
        desc_regras = Decimal("0.00")
        # recalcula cupom nesse cenário
        c, desc_cupom = validar_cupom(cupom_codigo, rifa, cliente, parcial, qtd)
    total = max(_money(parcial - desc_cupom), Decimal("0.00"))

    breakdown = {
        "subtotal": str(subtotal),
        "desc_regras": str(desc_regras),
        "desc_cupom": str(desc_cupom),
        "total": str(total),
    }
    return subtotal, desc_regras, desc_cupom, total, breakdown, regras_aplicadas, c
