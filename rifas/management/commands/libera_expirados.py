# rifas/management/commands/liberar_reservas.py
from django.core.management.base import BaseCommand
from rifas.tasks import liberar_reservas_expiradas

class Command(BaseCommand):
    help = "Libera números reservados que expiraram e expira pedidos pendentes sem números."

    def handle(self, *args, **kwargs):
        n, p = liberar_reservas_expiradas()
        self.stdout.write(self.style.SUCCESS(f"Liberados {n} números; {p} pedidos expirados."))
