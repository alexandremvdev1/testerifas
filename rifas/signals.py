from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    Rifa,
    Numero,
    RifaFinanceiro,
)

@receiver(post_save, sender=Rifa)
def gerar_numeros_apos_criar(sender, instance: Rifa, created, **kwargs):
    """
    - quando a rifa é criada: gera todos os números
    - garante que exista o registro financeiro 1:1
    - quando a rifa é editada e aumentam a quantidade de números,
      cria só os que faltam
    """
    # 1) garantir o financeiro 1:1
    RifaFinanceiro.objects.get_or_create(rifa=instance)

    # 2) se acabou de criar, gera tudo de uma vez
    if created:
        if instance.quantidade_numeros > 0 and not instance.numeros.exists():
            bulk = [
                Numero(rifa=instance, numero=i)
                for i in range(1, instance.quantidade_numeros + 1)
            ]
            Numero.objects.bulk_create(bulk, batch_size=5000)
        return

    # 3) se não foi criado (foi UPDATE), pode ter mudado a quantidade
    qtd_atual = instance.numeros.count()
    if instance.quantidade_numeros > qtd_atual:
        # criar só o que falta
        inicio = qtd_atual + 1
        fim = instance.quantidade_numeros
        bulk = [
            Numero(rifa=instance, numero=i)
            for i in range(inicio, fim + 1)
        ]
        Numero.objects.bulk_create(bulk, batch_size=5000)
    # se diminuir a quantidade, NÃO apagamos os números
    # (melhor fazer isso manual pra não perder venda/pedido)
