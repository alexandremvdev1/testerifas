import random
from django.core.management.base import BaseCommand
from django.utils.text import slugify
from django.db import transaction

# ⚠️ AJUSTE AQUI: importe os seus modelos reais
from rifas.models import Rifa  # ex.: app "rifas"
from rifas.models import NumeroRifa  # se for CotaRifa ou Numero, troca aqui
from rifas.models import Cliente     # se você usa outro, tipo Participante
from rifas.models import Pedido, PedidoItem  # se chamar PedidoNumero, troca


class Command(BaseCommand):
    help = "Cria uma rifa demo com 50 números, 10 clientes e vários pedidos"

    def handle(self, *args, **options):
        with transaction.atomic():
            self.stdout.write(self.style.WARNING("⏳ Criando dados de demonstração..."))
            rifa = self._create_rifa()
            self._create_numeros(rifa, total=50)
            clientes = self._create_clientes(qtd=10)
            self._create_pedidos(rifa, clientes)
            self.stdout.write(self.style.SUCCESS("✅ Demo criada com sucesso!"))

    # ------------------------------------------------------------------
    def _create_rifa(self):
        titulo = "Rifa Demo 50 Números"
        slug = slugify("rifa-demo-50")
        rifa, created = Rifa.objects.get_or_create(
            slug=slug,
            defaults={
                "titulo": titulo,
                "preco_numero": 10.00,   # ajusta se no teu modelo for Decimal
                "descricao": "Rifa de demonstração para testes de front e checkout.",
            }
        )
        if not created:
            # limpa os números antigos dessa rifa pra recriar
            NumeroRifa.objects.filter(rifa=rifa).delete()
        return rifa

    # ------------------------------------------------------------------
    def _create_numeros(self, rifa, total=50):
        """
        Cria 50 números todos como livres (status=0).
        Se no seu modelo o campo for outro (ex: situacao), troque ali.
        """
        bulk = []
        for n in range(1, total + 1):
            num = NumeroRifa(
                rifa=rifa,
                numero=n,
                status=0,     # 0 = livre | 1 = reservado | 2 = pago  (ajuste p/ seu enum)
            )
            bulk.append(num)
        NumeroRifa.objects.bulk_create(bulk)
        self.stdout.write(self.style.SUCCESS(f"• Criados {total} números para {rifa.titulo}"))

    # ------------------------------------------------------------------
    def _create_clientes(self, qtd=10):
        """
        Gera 10 clientes fake.
        """
        base_nomes = [
            "Ana Souza",
            "Bruno Oliveira",
            "Carlos Mendes",
            "Daniela Castro",
            "Eduardo Lima",
            "Fernanda Nogueira",
            "Gabriel Alves",
            "Helena Dias",
            "Igor Ferreira",
            "Juliana Rocha",
        ]
        clientes = []
        for i in range(qtd):
            nome = base_nomes[i % len(base_nomes)]
            cpf = f"11122233{i:02d}"  # gera um cpf fake só p/ teste
            tel = f"(63) 9 8{i:03d}-000{i%10}"
            cli, _ = Cliente.objects.get_or_create(
                cpf=cpf,
                defaults={
                    "nome": nome,
                    "telefone": tel,
                }
            )
            clientes.append(cli)
        self.stdout.write(self.style.SUCCESS(f"• Criados {len(clientes)} clientes"))
        return clientes

    # ------------------------------------------------------------------
    def _create_pedidos(self, rifa, clientes):
        """
        Aqui é o 'tempero':
        - alguns pedidos com 1 número
        - alguns com 2 ou 3
        - alguns pagos
        - alguns reservados
        - cliente repetindo compra
        """
        # pega todos os números livres
        numeros = list(NumeroRifa.objects.filter(rifa=rifa, status=0).order_by("numero"))

        if not numeros:
            self.stdout.write(self.style.ERROR("Sem números livres para criar pedidos."))
            return

        pedidos_criados = 0

        for idx, cliente in enumerate(clientes):
            # cada cliente vai fazer de 1 a 3 compras
            qtd_compras = random.randint(1, 3)

            for _ in range(qtd_compras):
                # escolhe quantos números esse pedido vai ter
                qtd_numeros_pedido = random.choice([1, 1, 2, 3])  # mais chance de ser 1
                # pega os primeiros disponíveis
                if len(numeros) < qtd_numeros_pedido:
                    break

                numeros_escolhidos = numeros[:qtd_numeros_pedido]
                numeros = numeros[qtd_numeros_pedido:]  # remove da lista

                # define se esse pedido é pago ou reservado
                # regra: a cada 3 pedidos, 2 são pagos
                pago = (pedidos_criados % 3 != 0)

                pedido = Pedido.objects.create(
                    rifa=rifa,
                    cliente=cliente,
                    status="PAGO" if pago else "RESERVADO",  # AJUSTE: se seu modelo usa int, troque aqui
                    valor_total=sum([rifa.preco_numero for _ in numeros_escolhidos]),
                    origem="seed",  # só pra você saber que foi seed
                )

                # cria itens do pedido
                itens_bulk = []
                for num in numeros_escolhidos:
                    itens_bulk.append(PedidoItem(
                        pedido=pedido,
                        numero=num,
                        valor_unit=rifa.preco_numero,
                    ))
                    # atualiza o status do número
                    num.status = 2 if pago else 1
                    num.save(update_fields=["status"])
                PedidoItem.objects.bulk_create(itens_bulk)

                pedidos_criados += 1

        self.stdout.write(self.style.SUCCESS(f"• Criados {pedidos_criados} pedidos com itens"))
