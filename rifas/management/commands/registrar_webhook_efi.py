# rifas/management/commands/registrar_webhook_efi.py
from django.core.management.base import BaseCommand, CommandError

from rifas.models import EfiConfig

# se você já usa requests dentro do model, isso aqui é só pra tipar o except
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False


class Command(BaseCommand):
    help = "Registra (ou atualiza) o webhook da Efí para as credenciais configuradas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=int,
            help="ID específico de uma EfiConfig",
        )
        parser.add_argument(
            "--empresa",
            type=int,
            help="Registrar apenas para as configs dessa empresa (id)",
        )
        parser.add_argument(
            "--rifa",
            type=int,
            help="Registrar apenas para as configs dessa rifa (id)",
        )
        parser.add_argument(
            "--dominio",
            type=str,
            help="Força o domínio/base usado para montar o webhook, ex: https://minhaapp.com",
        )
        parser.add_argument(
            "--inativas",
            action="store_true",
            help="Incluir configs inativas também",
        )

    def handle(self, *args, **options):
        config_id = options.get("config")
        empresa_id = options.get("empresa")
        rifa_id = options.get("rifa")
        dominio_forcado = options.get("dominio")
        incluir_inativas = options.get("inativas")

        qs = EfiConfig.objects.all()

        # por padrão, só as ativas
        if not incluir_inativas:
            qs = qs.filter(ativo=True)

        if config_id:
            qs = qs.filter(id=config_id)

        if empresa_id:
            qs = qs.filter(empresa_id=empresa_id)

        if rifa_id:
            qs = qs.filter(rifa_id=rifa_id)

        if not qs.exists():
            raise CommandError("Nenhuma EfiConfig encontrada com esses filtros.")

        total = 0
        ok = 0
        erros = 0

        self.stdout.write(self.style.NOTICE("Iniciando registro de webhooks na Efí..."))
        self.stdout.write(self.style.NOTICE(f"Configs encontradas: {qs.count()}"))
        self.stdout.write("")

        for cfg in qs:
            total += 1

            # tenta descobrir o nome bonitinho pra log
            dono = None
            if cfg.empresa_id:
                dono = f"empresa={cfg.empresa_id}"
            elif cfg.rifa_id:
                dono = f"rifa={cfg.rifa_id}"
            else:
                dono = "global"

            # força domínio se veio na linha de comando
            if dominio_forcado:
                # se tem empresa, injeta o domínio ali
                if cfg.empresa_id and cfg.empresa:
                    cfg.empresa.dominio_publico = dominio_forcado
                # se a config está ligada à rifa e a rifa tem empresa, joga lá
                elif cfg.rifa_id and cfg.rifa and cfg.rifa.empresa:
                    cfg.rifa.empresa.dominio_publico = dominio_forcado
                # se não tem onde injetar, o próprio register_webhook deve cair no default/SITE_URL

            # info de ambiente (ajusta o nome do campo se o seu model for diferente)
            ambiente = "PRODUÇÃO"
            if getattr(cfg, "sandbox", False):
                ambiente = "HOMOLOG (sandbox)"

            self.stdout.write(
                self.style.NOTICE(
                    f"→ Registrando webhook para cfg={cfg.id} ({dono}) [{ambiente}]"
                )
            )

            # chama o método do model
            try:
                resp = cfg.register_webhook()
            except (requests.exceptions.SSLError,) if REQUESTS_AVAILABLE else (Exception,) as e:
                # erro típico: SSL: PEM lib (...) / RemoteDisconnected (...)
                erros += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"   ✖ Erro de SSL/conexão para cfg={cfg.id}: {e}"
                    )
                )
                self.stderr.write(
                    "     → Verifique se o CERT/KEY batem com o ambiente (homolog x produção)\n"
                    "       e se o host usado no register_webhook é o mesmo do certificado."
                )
                continue
            except Exception as e:
                erros += 1
                self.stderr.write(
                    self.style.ERROR(f"   ✖ Erro inesperado para cfg={cfg.id}: {e}")
                )
                continue

            # agora precisamos normalizar o que o model retornou
            status = None
            texto = ""
            webhook_url = None

            # caso o model já devolva um dict (exatamente como você mostrou antes)
            if isinstance(resp, dict):
                status = resp.get("status_code")
                texto = (resp.get("text") or "")[:200]
                webhook_url = resp.get("webhook")
            else:
                # pode ser um requests.Response
                if REQUESTS_AVAILABLE and isinstance(resp, requests.Response):
                    status = resp.status_code
                    try:
                        texto = resp.text[:200]
                    except Exception:  # noqa
                        texto = ""
                    try:
                        data = resp.json()
                        webhook_url = data.get("webhook") or data.get("url")
                    except Exception:  # noqa
                        webhook_url = None
                else:
                    # última tentativa: só mostrar o repr
                    texto = str(resp)[:200]

            if status and 200 <= status < 300:
                ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"   ✔ OK ({status}) → {webhook_url or 'sem-url'}"
                    )
                )
            else:
                erros += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"   ✖ Falhou ({status or 'sem-status'}) → {texto or 'sem corpo'}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Concluído. Total: {total}, OK: {ok}, Erros: {erros}"
            )
        )
