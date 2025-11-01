# rifas/tasks.py
from django.utils import timezone
from django.db import transaction, models

from .models import Numero, Pedido


def liberar_reservas_expiradas():
    """
    Libera TODAS as reservas expiradas de TODAS as rifas.
    E expira os PEDIDOS que ficaram sem nenhum número por causa disso.
    Retorna (qtd_numeros_liberados, qtd_pedidos_expirados)
    """
    expirados_ids = []
    pedidos_tocados = set()

    # pega todos os números reservados
    qs = (
        Numero.objects
        .select_related("rifa", "pedido")
        .filter(status=Numero.RESERVADO)
    )

    for n in qs:
        # usa o método do model (já respeita rifa.minutos_expiracao_reserva)
        if n.expirou():
            expirados_ids.append(n.id)
            if n.pedido_id:
                pedidos_tocados.add(n.pedido_id)

    # se não tem nada expirado, sai
    if not expirados_ids:
        return 0, 0

    with transaction.atomic():
        # 1) solta os números vencidos
        Numero.objects.filter(id__in=expirados_ids).update(
            status=Numero.LIVRE,
            cliente=None,
            reservado_em=None,
            pedido=None,
        )

        # 2) expira só os pedidos que realmente perderam número agora
        pedidos_expirados = 0
        if pedidos_tocados:
            for pid in pedidos_tocados:
                try:
                    ped = Pedido.objects.get(id=pid)
                except Pedido.DoesNotExist:
                    continue

                # só mexe se ainda estiver pendente
                if ped.status == Pedido.PENDENTE:
                    # depois de liberar os números acima, confere se o pedido ficou vazio
                    if ped.numeros.count() == 0:
                        ped.status = Pedido.EXPIRADO
                        ped.save(update_fields=["status"])
                        pedidos_expirados += 1

    return len(expirados_ids), pedidos_expirados
