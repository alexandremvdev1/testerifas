from __future__ import annotations

import os
import json
import requests  # 👈 para chamar a API da Efí
from decimal import Decimal
from datetime import timedelta

from django.conf import settings  # 👈 para pegar SITE_URL
from django.db import models
from django.db.models import Sum, Count
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator

User = get_user_model()

# ============================================================
# EMPRESA (dona da ação / da rifa)
# ============================================================
class Empresa(models.Model):
    """
    Empresa dona/responsável pela rifa.
    Aqui também ficam as TAXAS PADRÃO (cartão/gateway) para TODAS as rifas dela.
    Depois a rifa pode sobrescrever.
    """
    nome = models.CharField(max_length=160)
    documento = models.CharField(
        max_length=18,
        blank=True,
        help_text="CPF ou CNPJ",
    )
    email = models.EmailField(blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True)
    logo = models.ImageField(
        upload_to="empresas_logos/",
        blank=True,
        null=True,
    )

    # 👇 links padrão
    whatsapp_suporte = models.URLField(
        blank=True,
        null=True,
        help_text="Link do WhatsApp para suporte geral da empresa",
    )
    whatsapp_grupo = models.URLField(
        blank=True,
        null=True,
        help_text="Link do grupo/comunidade da empresa",
    )

    # 👇 domínio para montar webhook / links públicos
    dominio_publico = models.URLField(
        blank=True,
        null=True,
        help_text="Ex.: https://rifas.meucliente.com.br — usado para montar o webhook da Efí",
    )
    subdominio = models.CharField(
        max_length=80,
        blank=True,
        null=True,
        help_text="Opcional. Ex.: cliente01 (o sistema pode montar cliente01.seudominio.com)",
    )

    # quem cadastrou
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="empresas_criadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # 🆕 taxas padrão (usa em todas as rifas dessa empresa)
    taxa_admin_percentual_padrao = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Percentual padrão da operadora, ex: 3.99",
    )
    taxa_admin_fixa_padrao = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Taxa fixa padrão por ação, ex: 1.00",
    )

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        if self.documento:
            return f"{self.nome} ({self.documento})"
        return self.nome


# ============================================================
# CONFIGURAÇÕES DE PAGAMENTO — EFI (GERENCIANET)
# ============================================================
class EfiConfig(models.Model):
    """
    Credenciais da Efí para gerar cobrança Pix.

    Pode ser:
    - global (sem empresa e sem rifa)
    - por empresa
    - por rifa

    SUPORTA:
    - 1 arquivo só (certificate) com cert+key juntos
    - OU 2 arquivos separados (certificate_cert + certificate_key)

    Em DESENVOLVIMENTO (DEBUG=True) e sem nenhum cert enviado,
    o método register_webhook() **SIMULA** o registro e já grava
    a URL no banco, pra não travar o painel.
    """
    nome = models.CharField("nome interno", max_length=120)

    client_id = models.CharField(max_length=180)
    client_secret = models.CharField(max_length=180)

    # 🔴 modo antigo: 1 arquivo só (mantido)
    certificate = models.FileField(
        upload_to="efi_certs/",
        blank=True,
        null=True,
        help_text="Arquivo ÚNICO .pem/.p12 da Efí (cert + key no mesmo arquivo).",
    )

    # 🆕 modo recomendado: 2 arquivos
    certificate_cert = models.FileField(
        upload_to="efi_certs/",
        blank=True,
        null=True,
        help_text="CERT do cliente (.pem) — se usar arquivo separado.",
    )
    certificate_key = models.FileField(
        upload_to="efi_certs/",
        blank=True,
        null=True,
        help_text="KEY do cliente (.key/.pem) — se usar arquivo separado.",
    )

    chave_pix = models.CharField(
        max_length=140,
        help_text="Chave Pix cadastrada na Efí",
    )

    # escopo
    empresa = models.ForeignKey(
        "Empresa",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="efi_configs",
        help_text="Se preenchido, essa conta é da empresa.",
    )
    rifa = models.ForeignKey(
        "Rifa",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="efi_configs",
        help_text="Se preenchido, essa conta é só desta rifa.",
    )

    # ambiente
    sandbox = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    # guarda o último webhook registrado
    webhook_url_registrado = models.URLField(
        blank=True,
        null=True,
        help_text="Última URL de webhook registrada na Efí para esta credencial.",
    )

    class Meta:
        verbose_name = "Configuração Efí"
        verbose_name_plural = "Configurações Efí"

    def __str__(self):
        scope = "global"
        if self.empresa_id:
            scope = f"empresa: {self.empresa.nome}"
        if self.rifa_id:
            scope = f"rifa: {self.rifa.slug}"
        return f"{self.nome} ({scope})"

    # ======================================================
    # helpers de URL / domínio
    # ======================================================
    def get_base_url(self) -> str:
        """
        Ordem:
        1) domínio da empresa da rifa
        2) domínio da empresa desta config
        3) settings.SITE_URL
        4) http://127.0.0.1:8000
        """
        if self.rifa_id and self.rifa.empresa and self.rifa.empresa.dominio_publico:
            return self.rifa.empresa.dominio_publico.rstrip("/")

        if self.empresa_id and self.empresa.dominio_publico:
            return self.empresa.dominio_publico.rstrip("/")

        site = getattr(settings, "SITE_URL", None) or os.getenv("SITE_URL")
        if site:
            return site.rstrip("/")

        return "http://127.0.0.1:8000"

    def build_webhook_url(self) -> str:
        base = self.get_base_url()
        return f"{base}/api/pagamentos/webhook/efi/"

    # ======================================================
    # helper de CERT
    # ======================================================
    def _get_requests_cert_param(self):
        """
        Monta o parâmetro `cert` do requests:
        - se tiver cert E key separados → (cert, key)
        - senão, se tiver 1 arquivo só → cert
        - senão → None
        """
        # 2 arquivos separados
        if self.certificate_cert and self.certificate_key:
            return (self.certificate_cert.path, self.certificate_key.path)

        # 1 arquivo só
        if self.certificate:
            return self.certificate.path

        return None

    def _has_any_cert(self) -> bool:
        """
        True se tem pelo menos um jeito de autenticar com cert.
        """
        if self.certificate and os.path.exists(self.certificate.path):
            return True
        if (
            self.certificate_cert
            and os.path.exists(self.certificate_cert.path)
            and self.certificate_key
            and os.path.exists(self.certificate_key.path)
        ):
            return True
        return False

    # ======================================================
    # registro na Efí
    # ======================================================
    def get_token(self) -> str:
        """
        Pega o access_token na Efí.
        Usa o certificado (1 ou 2 arquivos) se houver.
        """
        if self.sandbox:
            auth_url = "https://pix-h.api.efipay.com.br/oauth/token"
        else:
            auth_url = "https://pix.api.efipay.com.br/oauth/token"

        cert_param = self._get_requests_cert_param()

        resp = requests.post(
            auth_url,
            auth=(self.client_id, self.client_secret),
            json={"grant_type": "client_credentials"},
            verify=True if cert_param else False,
            cert=cert_param,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"]

    def register_webhook(self) -> dict:
        """
        Registra (ou sobrescreve) o webhook na Efí usando o domínio que está no modelo.

        ⚠️ Em DEBUG=True e SEM certificado → simula o sucesso.
        """
        webhook_url = self.build_webhook_url()
        is_debug = getattr(settings, "DEBUG", False)
        has_cert = self._has_any_cert()

        # =============== MODO DESENVOLVIMENTO ===============
        if is_debug and not has_cert:
            # não vamos nem bater na Efí, só salvar e retornar
            self.webhook_url_registrado = webhook_url
            self.save(update_fields=["webhook_url_registrado"])
            return {
                "ok": True,
                "status_code": 200,
                "text": "SIMULAÇÃO LOCAL: DEBUG=True e nenhum certificado enviado. "
                        "Em produção a chamada real será feita.",
                "webhook": webhook_url,
                "endpoint": "simulado-local",
            }
        # =============== FIM MODO DESENVOLVIMENTO ===============

        # fluxo REAL
        access_token = self.get_token()

        if self.sandbox:
            url = f"https://pix-h.api.efipay.com.br/v2/webhook/{self.chave_pix}"
        else:
            url = f"https://pix.api.efipay.com.br/v2/webhook/{self.chave_pix}"

        cert_param = self._get_requests_cert_param()

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {"webhookUrl": webhook_url}

        try:
            resp = requests.put(
                url,
                headers=headers,
                data=json.dumps(payload),
                verify=True if cert_param else False,
                cert=cert_param,
                timeout=20,
            )
        except requests.exceptions.RequestException as e:
            # isso aqui vai cair exatamente no teu erro:
            # ('Connection aborted.', RemoteDisconnected(...))
            return {
                "ok": False,
                "status_code": None,
                "text": f"Erro de rede/TLS ao falar com a Efí: {e}",
                "webhook": webhook_url,
                "endpoint": url,
            }

        if 200 <= resp.status_code < 300:
            self.webhook_url_registrado = webhook_url
            self.save(update_fields=["webhook_url_registrado"])

        return {
            "ok": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "text": resp.text,
            "webhook": webhook_url,
            "endpoint": url,
        }

# ============================================================
# PESSOAS (quem compra)
# ============================================================
class Cliente(models.Model):
    nome = models.CharField(max_length=120)
    email = models.EmailField()
    telefone = models.CharField(max_length=20)
    cpf = models.CharField(max_length=14, db_index=True)  # 000.000.000-00
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["cpf"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return f"{self.nome} ({self.cpf})"


# ============================================================
# RIFA / NÚMEROS / PRÊMIOS / SORTEIO / COTAS PREMIADAS
# ============================================================
class Rifa(models.Model):
    MEIO_EFI = "efi"
    MEIO_CHOICES = [
        (MEIO_EFI, "Efí (Gerencianet)"),
    ]

    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rifas",
        help_text="Empresa dona/responsável pela rifa",
    )

    titulo = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    descricao = models.TextField(blank=True)
    banner = models.ImageField(upload_to="rifas_banners/", blank=True, null=True)
    preco_numero = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    quantidade_numeros = models.PositiveIntegerField(
        default=1000,
        validators=[MinValueValidator(1)],
    )
    inicio_vendas = models.DateTimeField()
    fim_vendas = models.DateTimeField()
    ativo = models.BooleanField(default=True)

    permitir_numero_escolhido = models.BooleanField(default=True)
    limite_por_pedido = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(1)],
    )
    minutos_expiracao_reserva = models.PositiveIntegerField(
        default=15,
        validators=[MinValueValidator(1)],
    )
    mostrar_top_compradores = models.BooleanField(default=True)

    em_vendas = models.BooleanField(default=True)

    # sobrescrevem o que vier da empresa
    link_whatsapp = models.URLField(
        blank=True,
        null=True,
        help_text="Se preencher aqui, usa este link de suporte. Senão, usa o da empresa.",
    )
    link_grupo = models.URLField(
        blank=True,
        null=True,
        help_text="Se preencher aqui, usa este link de grupo. Senão, usa o da empresa.",
    )

    meio_pagamento = models.CharField(
        max_length=10,
        choices=MEIO_CHOICES,
        default=MEIO_EFI,
        help_text="Define se esta rifa cobra pela Efí (Pix).",
    )

    efi_config = models.ForeignKey(
        EfiConfig,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="rifas_usando",
        help_text="Se definido, esta rifa sempre usará esta credencial Efí.",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rifas_criadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.empresa:
            return f"{self.titulo} — {self.empresa.nome}"
        return self.titulo

    @property
    def em_vendas_agora(self) -> bool:
        now = timezone.now()
        return self.ativo and (self.inicio_vendas <= now <= self.fim_vendas)

    # 👇👇👇 herança dos links da empresa
    @property
    def whatsapp_suporte(self):
        """
        Retorna o link de suporte da rifa, caindo para o da empresa se não tiver aqui.
        """
        if self.link_whatsapp:
            return self.link_whatsapp
        if self.empresa and self.empresa.whatsapp_suporte:
            return self.empresa.whatsapp_suporte
        return None

    @property
    def whatsapp_grupo(self):
        """
        Retorna o link de grupo/comunidade da rifa, caindo para o da empresa se não tiver aqui.
        """
        if self.link_grupo:
            return self.link_grupo
        if self.empresa and self.empresa.whatsapp_grupo:
            return self.empresa.whatsapp_grupo
        return None


class Numero(models.Model):
    LIVRE, RESERVADO, PAGO = "livre", "reservado", "pago"
    STATUS_CHOICES = [
        (LIVRE, "Livre"),
        (RESERVADO, "Reservado"),
        (PAGO, "Pago"),
    ]

    rifa = models.ForeignKey(
        Rifa,
        on_delete=models.CASCADE,
        related_name="numeros",
    )
    numero = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=LIVRE,
        db_index=True,
    )
    cliente = models.ForeignKey(
        "Cliente",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="numeros",
    )
    reservado_em = models.DateTimeField(null=True, blank=True)
    pedido = models.ForeignKey(
        "Pedido",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="numeros",
    )

    class Meta:
        unique_together = ("rifa", "numero")
        indexes = [
            models.Index(fields=["rifa", "status"]),
            models.Index(fields=["rifa", "numero"]),
        ]

    def __str__(self):
        return f"{self.rifa.slug} #{self.numero}"

    def expirou(self) -> bool:
        if self.status != self.RESERVADO or not self.reservado_em:
            return False
        delta = timezone.now() - self.reservado_em
        return delta.total_seconds() > (self.rifa.minutos_expiracao_reserva * 60)

    def marcar_como_pago(self):
        """
        Marca o número como pago e, se ele for uma cota premiada ativa,
        garante que exista uma entrada em RifaPremiacao para ele.
        Se já tivermos cliente, amarra o cliente na premiação.
        """
        if self.status == self.PAGO:
            return

        self.status = self.PAGO
        self.save(update_fields=["status"])

        # se esse número for cota premiada, joga na RifaPremiacao
        cota = self.rifa.cotas_premiadas.filter(numero=self.numero, ativo=True).first()
        if cota:
            from .models import RifaPremiacao  # import interno, evita topo do arquivo
            prem, created = RifaPremiacao.objects.get_or_create(
                rifa=self.rifa,
                tipo=RifaPremiacao.TIPO_COTA,
                numero=self.numero,
                defaults={
                    "descricao": cota.descricao or f"Cota premiada #{self.numero}",
                    "valor": cota.valor_premio,
                },
            )
            # se veio cliente no número, amarra
            if self.cliente_id and not prem.cliente_id:
                prem.cliente = self.cliente
                prem.save(update_fields=["cliente"])


class CotaPremiada(models.Model):
    rifa = models.ForeignKey(
        Rifa,
        on_delete=models.CASCADE,
        related_name="cotas_premiadas",
    )
    numero = models.PositiveIntegerField()
    descricao = models.CharField(max_length=200, blank=True)
    valor_premio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        default=Decimal("0.00"),
    )
    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("rifa", "numero")
        ordering = ["numero"]

    def __str__(self):
        return f"{self.rifa.slug} • cota #{self.numero}"


class Premio(models.Model):
    rifa = models.ForeignKey(
        Rifa,
        on_delete=models.CASCADE,
        related_name="premios",
    )
    ordem = models.PositiveIntegerField(default=1)
    descricao = models.CharField(max_length=200)

    class Meta:
        ordering = ["ordem"]

    def __str__(self):
        return f"{self.rifa.slug} • {self.ordem}º"


class Sorteio(models.Model):
    rifa = models.OneToOneField(
        Rifa,
        on_delete=models.CASCADE,
        related_name="sorteio",
    )
    realizado_em = models.DateTimeField(null=True, blank=True)
    numero_sorteado = models.PositiveIntegerField(null=True, blank=True)
    auditoria_hash = models.CharField(max_length=64, blank=True, null=True)

    def __str__(self):
        return f"Sorteio {self.rifa.slug}"


# ============================================================
# PEDIDO / PAGAMENTO
# ============================================================
class Pedido(models.Model):
    PENDENTE, PAGO, CANCELADO, EXPIRADO = (
        "pendente",
        "pago",
        "cancelado",
        "expirado",
    )
    STATUS_CHOICES = [
        (PENDENTE, "Pendente"),
        (PAGO, "Pago"),
        (CANCELADO, "Cancelado"),
        (EXPIRADO, "Expirado"),
    ]

    rifa = models.ForeignKey(
        Rifa,
        on_delete=models.CASCADE,
        related_name="pedidos",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )
    protocolo = models.CharField(max_length=20, unique=True, db_index=True)

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    desconto_regras = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    desconto_cupom = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=PENDENTE,
    )
    pricing_breakdown = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    pago_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.protocolo} — {self.get_status_display()}"

    def marcar_como_pago(self):
        """
        Ao pagar o pedido:
        - marca o pedido
        - garante que todos os números fiquem pagos
        - garante que o número tenha o cliente do pedido
        - atualiza/gera a RifaPremiacao do top comprador da rifa
        """
        if self.status in (self.PAGO, self.CANCELADO, self.EXPIRADO):
            return

        self.status = self.PAGO
        self.pago_em = timezone.now()
        self.save(update_fields=["status", "pago_em"])

        # marca todos os números e garante o cliente
        for num in self.numeros.select_related("rifa"):
            if not num.cliente_id:
                num.cliente = self.cliente
            num.marcar_como_pago()
            # se só atribuímos o cliente (acima), precisamos salvar
            if num.cliente_id and num.pk:
                num.save(update_fields=["cliente"])

        # recalcula o top comprador
        self.atualizar_premiacao_top_comprador()

    def atualizar_premiacao_top_comprador(self):
        """
        Pega quem mais tem números pagos na rifa e garante 1 registro
        em RifaPremiacao(tipo='top_comprador'). Se já houver outro top, remove.
        """
        from .models import Numero, RifaPremiacao

        top = (
            Numero.objects
            .filter(rifa=self.rifa, status=Numero.PAGO, cliente__isnull=False)
            .values("cliente", "cliente__nome")
            .annotate(qtd=Count("id"))
            .order_by("-qtd")
            .first()
        )
        if not top:
            return

        cliente_id = top["cliente"]
        cliente_nome = top["cliente__nome"]

        # remove outros TOPs que não sejam o atual
        RifaPremiacao.objects.filter(
            rifa=self.rifa,
            tipo=RifaPremiacao.TIPO_TOP
        ).exclude(cliente_id=cliente_id).delete()

        # cria/atualiza o TOP do cliente
        RifaPremiacao.objects.update_or_create(
            rifa=self.rifa,
            tipo=RifaPremiacao.TIPO_TOP,
            cliente_id=cliente_id,
            defaults={
                "descricao": f"Top comprador — {cliente_nome}",
                # valor: o admin coloca depois no painel financeiro
            },
        )

    def liberar_numeros(self):
        from .models import Numero  # evitar circular
        numeros = Numero.objects.filter(pedido=self)
        for n in numeros:
            n.status = Numero.LIVRE
            n.pedido = None
            n.cliente = None
            n.reservado_em = None
            n.save(update_fields=["status", "pedido", "cliente", "reservado_em"])

    def tem_numeros(self):
        return self.numeros.exists()

    def expirar(self, soltar_numeros=True, deletar_se_vazio=True):
        if self.status == self.PAGO:
            return
        self.status = self.EXPIRADO
        self.save(update_fields=["status"])
        if soltar_numeros:
            self.liberar_numeros()
        if deletar_se_vazio and not self.tem_numeros():
            self.delete()

    def get_expiration_datetime(self):
        tempo = getattr(self.rifa, "tempo_reserva_segundos", None)
        if not tempo:
            return None
        return self.criado_em + timedelta(seconds=tempo)

    def get_tempo_restante_segundos(self):
        exp = self.get_expiration_datetime()
        if not exp:
            return None
        agora = timezone.now()
        diff = (exp - agora).total_seconds()
        return int(diff)


class Pagamento(models.Model):
    pedido = models.OneToOneField(
        Pedido,
        on_delete=models.CASCADE,
        related_name="pagamento",
    )

    provider = models.CharField(max_length=30, default="efi")

    provider_preference_id = models.CharField(max_length=80, blank=True, null=True)
    provider_payment_id = models.CharField(max_length=80, blank=True, null=True)

    qr_code_base64 = models.TextField(blank=True, null=True)
    copia_cola = models.TextField(blank=True, null=True)

    efi_txid = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="txid da cobrança Pix gerada na Efí",
    )
    efi_loc_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        help_text="ID do location na Efí (para recuperar QRCode)",
    )

    status_provider = models.CharField(
        max_length=30,
        blank=True,
        null=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pagamento {self.pedido.protocolo} ({self.provider})"


class WebhookEvent(models.Model):
    provider = models.CharField(max_length=30)
    event_id = models.CharField(max_length=80, db_index=True)
    raw = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.provider} • {self.event_id}"



# ============================================================
# CUPONS / DESCONTOS
# ============================================================
class Coupon(models.Model):
    PERCENTUAL, VALOR = "percentual", "valor"
    TIPO_CHOICES = [
        (PERCENTUAL, "%"),
        (VALOR, "R$"),
    ]

    codigo = models.CharField(max_length=32, unique=True)
    tipo = models.CharField(
        max_length=12,
        choices=TIPO_CHOICES,
        default=PERCENTUAL,
    )
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    max_uso_global = models.PositiveIntegerField(null=True, blank=True)
    max_uso_por_cliente = models.PositiveIntegerField(null=True, blank=True)
    min_compra = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    qtd_min_numeros = models.PositiveIntegerField(null=True, blank=True)
    so_primeira_compra = models.BooleanField(default=False)
    rifa = models.ForeignKey(
        Rifa,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="coupons",
    )
    acumulavel = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)
    inicio = models.DateTimeField(null=True, blank=True)
    fim = models.DateTimeField(null=True, blank=True)
    uso_cache = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["codigo"])]

    def __str__(self):
        return self.codigo.upper()


class CouponRedemption(models.Model):
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name="redemptions",
    )
    pedido = models.OneToOneField(
        Pedido,
        on_delete=models.CASCADE,
        related_name="coupon_redemption",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,   # ← aqui estava "ondelete"
        related_name="coupon_redemptions",
    )
    desconto_aplicado = models.DecimalField(max_digits=12, decimal_places=2)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.coupon.codigo} → {self.pedido.protocolo}"



class DiscountRule(models.Model):
    PERCENTUAL, VALOR = "percentual", "valor"
    TIPO_CHOICES = [
        (PERCENTUAL, "%"),
        (VALOR, "R$"),
    ]

    nome = models.CharField(max_length=120)
    rifa = models.ForeignKey(
        Rifa,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="discount_rules",
    )

    qtd_numeros_min = models.PositiveIntegerField(null=True, blank=True)
    inicio = models.DateTimeField(null=True, blank=True)
    fim = models.DateTimeField(null=True, blank=True)

    tipo = models.CharField(
        max_length=12,
        choices=TIPO_CHOICES,
        default=PERCENTUAL,
    )
    valor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    prioridade = models.IntegerField(default=0)
    exclusiva = models.BooleanField(default=False)
    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ativo", "-prioridade", "-created_at"]

    def __str__(self):
        return self.nome


class DiscountApplication(models.Model):
    rule = models.ForeignKey(
        DiscountRule,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="discount_applications",
    )
    desconto_aplicado = models.DecimalField(max_digits=12, decimal_places=2)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.rule.nome} → {self.pedido.protocolo}"


# ============================================================
# AFILIADOS
# ============================================================
class Affiliate(models.Model):
    nome = models.CharField(max_length=120)
    email = models.EmailField()
    telefone = models.CharField(max_length=20, blank=True)
    documento = models.CharField(max_length=18, blank=True)
    status = models.CharField(max_length=12, default="ativo")  # ativo/suspenso
    pix_chave = models.CharField(max_length=120, blank=True)
    banco_agencia_conta = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome


class AffiliateProgram(models.Model):
    PERC_VENDA = "percentual_venda"
    VALOR_FIXO_NUM = "valor_fixopornumero"
    PERC_LUCRO = "percentual_sobre_lucro"
    MODELOS = [
        (PERC_VENDA, "% sobre venda"),
        (VALOR_FIXO_NUM, "R$ por número"),
        (PERC_LUCRO, "% sobre lucro"),
    ]

    rifa = models.ForeignKey(
        Rifa,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="affiliate_programs",
    )
    modelo_comissao = models.CharField(
        max_length=40,
        choices=MODELOS,
        default=PERC_VENDA,
    )
    valor_comissao = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    cookie_days = models.PositiveIntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
    )
    atribuicao = models.CharField(max_length=12, default="last_click")
    permitir_compra_propria = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f"Programa ({self.rifa.slug if self.rifa_id else 'global'})"


class AffiliateLink(models.Model):
    affiliate = models.ForeignKey(
        Affiliate,
        on_delete=models.CASCADE,
        related_name="links",
    )
    program = models.ForeignKey(
        AffiliateProgram,
        on_delete=models.CASCADE,
        related_name="links",
    )
    token = models.SlugField(unique=True, max_length=32)
    url_destino = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.affiliate.nome} • {self.token}"


class AffiliateClick(models.Model):
    link = models.ForeignKey(
        AffiliateLink,
        on_delete=models.CASCADE,
        related_name="clicks",
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    referer = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["clicked_at"])]

    def __str__(self):
        return f"Click {self.link.token} @ {self.clicked_at:%Y-%m-%d}"


class AffiliateAttribution(models.Model):
    link = models.ForeignKey(
        AffiliateLink,
        on_delete=models.CASCADE,
        related_name="attributions",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name="affiliate_attributions",
    )
    expira_em = models.DateTimeField()
    modelo = models.CharField(max_length=12, default="last_click")

    def __str__(self):
        return f"Atribuição {self.link.token} → {self.cliente_id}"


class Commission(models.Model):
    PENDENTE, APROVADA, PAGA, NEGADA = "pendente", "aprovada", "paga", "negada"

    affiliate = models.ForeignKey(
        Affiliate,
        on_delete=models.CASCADE,
        related_name="commissions",
    )
    pedido = models.OneToOneField(
        Pedido,
        on_delete=models.CASCADE,
        related_name="commission",
    )
    base_calculo = models.DecimalField(max_digits=12, decimal_places=2)
    percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, default=PENDENTE)
    motivo_negacao = models.CharField(max_length=200, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Comissão {self.pedido.protocolo} → {self.affiliate.nome}"


class Payout(models.Model):
    EM_PROC, PAGO, FALHOU = "em_processamento", "pago", "falhou"

    affiliate = models.ForeignKey(
        Affiliate,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default=EM_PROC)
    comprovante_url = models.URLField(blank=True)
    pago_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payout {self.affiliate.nome} — {self.valor_total}"


# ============================================================
# 🆕 FINANCEIRO DA RIFA (1:1)
# ============================================================
class RifaFinanceiro(models.Model):
    rifa = models.OneToOneField(
        Rifa,
        on_delete=models.CASCADE,
        related_name="financeiro",
    )

    # custos diretos
    custo_premio = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Custo do prêmio principal.",
    )
    premio_top_comprador = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Prêmio do top comprador (se tiver).",
    )
    outras_despesas = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Outras despesas da ação.",
    )

    # sobrescrever taxa padrão da empresa
    taxa_admin_percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Se > 0, usa esta taxa (%) em vez da da empresa.",
    )
    taxa_admin_fixa = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Se > 0, usa esta taxa (R$) em vez da da empresa.",
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Financeiro da rifa"
        verbose_name_plural = "Financeiros de rifa"

    def __str__(self):
        return f"Financeiro — {self.rifa.titulo}"

    # ============ helpers ============
    def get_taxa_percentual_efetiva(self) -> Decimal:
        if self.taxa_admin_percentual and self.taxa_admin_percentual > 0:
            return self.taxa_admin_percentual
        empresa = self.rifa.empresa
        if empresa and empresa.taxa_admin_percentual_padrao:
            return empresa.taxa_admin_percentual_padrao
        return Decimal("0.00")

    def get_taxa_fixa_efetiva(self) -> Decimal:
        if self.taxa_admin_fixa and self.taxa_admin_fixa > 0:
            return self.taxa_admin_fixa
        empresa = self.rifa.empresa
        if empresa and empresa.taxa_admin_fixa_padrao:
            return empresa.taxa_admin_fixa_padrao
        return Decimal("0.00")

    def calcular_resumo(self):
        """
        Mantém tua lógica original (somar cotas premiadas cujos números foram pagos)
        e soma também o que estiver na tabela de premiações e já estiver marcado como pago.
        Assim o painel financeiro bate com o que o admin marcou como 'pago'.
        """
        from .models import Pedido, Numero, CotaPremiada, RifaPremiacao  # pra evitar ordem

        # total vendido (pedidos pagos)
        total_vendido = (
            Pedido.objects
            .filter(rifa=self.rifa, status=Pedido.PAGO)
            .aggregate(s=Sum("total"))["s"] or Decimal("0.00")
        )

        taxa_percentual = self.get_taxa_percentual_efetiva()
        taxa_fixa = self.get_taxa_fixa_efetiva()

        valor_taxa_percentual = (
            total_vendido * taxa_percentual / Decimal("100.00")
        ).quantize(Decimal("0.01"))
        valor_taxa_fixa = taxa_fixa

        # ------- tua regra antiga: "cotas premiadas que realmente foram pagas"
        total_cotas_premiadas = Decimal("0.00")
        cotas = CotaPremiada.objects.filter(rifa=self.rifa, ativo=True)
        for c in cotas:
            num_pago = Numero.objects.filter(
                rifa=self.rifa,
                numero=c.numero,
                status=Numero.PAGO,
            ).first()
            if num_pago and c.valor_premio:
                total_cotas_premiadas += c.valor_premio

        # ------- nova regra: tudo que o admin marcou como pago em RifaPremiacao
        total_premiacoes_pagas = (
            RifaPremiacao.objects
            .filter(rifa=self.rifa, pago=True)
            .aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
        )

        # somamos as duas coisas (se você quiser, pode escolher uma só)
        total_despesas = (
            self.custo_premio +
            self.premio_top_comprador +
            self.outras_despesas +
            valor_taxa_percentual +
            valor_taxa_fixa +
            total_cotas_premiadas +
            total_premiacoes_pagas
        )

        lucro_liquido = total_vendido - total_despesas

        return {
            "total_vendido": total_vendido,
            "taxa_percentual_usada": taxa_percentual,
            "taxa_fixa_usada": taxa_fixa,
            "valor_taxa_percentual": valor_taxa_percentual,
            "valor_taxa_fixa": valor_taxa_fixa,
            "total_cotas_premiadas": total_cotas_premiadas,
            "total_premiacoes_pagas": total_premiacoes_pagas,
            "total_despesas": total_despesas,
            "lucro_liquido": lucro_liquido,
        }


# ============================================================
# rifas/models.py — PREMIAÇÃO (NÃO REMOVER)
# ============================================================
class RifaPremiacao(models.Model):
    TIPO_PRINCIPAL = "principal"
    TIPO_COTA = "cota_premiada"
    TIPO_TOP = "top_comprador"
    TIPO_EXTRA = "extra"
    TIPOS = [
        (TIPO_PRINCIPAL, "Prêmio principal"),
        (TIPO_COTA, "Cota premiada"),
        (TIPO_TOP, "Top comprador"),
        (TIPO_EXTRA, "Outro / Manual"),
    ]

    rifa = models.ForeignKey("Rifa", on_delete=models.CASCADE, related_name="premiacoes")
    tipo = models.CharField(max_length=20, choices=TIPOS, default=TIPO_PRINCIPAL)
    cliente = models.ForeignKey(
        "Cliente",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="premiacoes"
    )
    numero = models.PositiveIntegerField(null=True, blank=True)
    descricao = models.CharField(max_length=200, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    pago = models.BooleanField(default=False)
    pago_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self):
        base = f"{self.rifa.slug} — {self.get_tipo_display()}"
        if self.numero:
            base += f" #{self.numero}"
        return base

    def marcar_pago(self):
        if not self.pago:
            self.pago = True
            self.pago_em = timezone.now()
            self.save(update_fields=["pago", "pago_em"])
