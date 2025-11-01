# rifas/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count, Sum, Q

from .models import (
    Empresa,
    EfiConfig,
    Cliente,
    Rifa,
    Numero,
    Premio,
    Sorteio,
    Pedido,
    Pagamento,
    WebhookEvent,
    Coupon,
    CouponRedemption,
    DiscountRule,
    DiscountApplication,
    Affiliate,
    AffiliateProgram,
    AffiliateLink,
    AffiliateClick,
    AffiliateAttribution,
    Commission,
    Payout,
    CotaPremiada,
    # 🆕 importa o financeiro da rifa
    RifaFinanceiro,
)

# -------------------------------------------------
# Helpers
# -------------------------------------------------
@admin.display(description="R$ Total")
def fmt_total(obj):
    return f"R$ {obj.total:.2f}"


@admin.display(description="Pago?")
def is_pago(obj):
    return obj.status == Pedido.PAGO


# -------------------------------------------------
# Inlines
# -------------------------------------------------
class PremioInline(admin.TabularInline):
    model = Premio
    extra = 0


class CotaPremiadaInline(admin.TabularInline):
    model = CotaPremiada
    extra = 0
    fields = ("numero", "descricao", "valor_premio", "ativo")
    ordering = ("numero",)


class PagamentoInline(admin.StackedInline):
    model = Pagamento
    can_delete = False
    extra = 0
    readonly_fields = (
        "provider",
        "provider_preference_id",
        "provider_payment_id",
        "efi_txid",
        "efi_loc_id",
        "copia_cola",
        "qr_code_base64",
        "status_provider",
        "criado_em",
        "atualizado_em",
    )


class CouponRedemptionInline(admin.TabularInline):
    model = CouponRedemption
    extra = 0
    readonly_fields = ("desconto_aplicado", "criado_em")


class DiscountApplicationInline(admin.TabularInline):
    model = DiscountApplication
    extra = 0
    readonly_fields = ("desconto_aplicado", "criado_em")


# 🆕 inline pra editar o financeiro direto na rifa
class RifaFinanceiroInline(admin.StackedInline):
    model = RifaFinanceiro
    extra = 0
    can_delete = False
    fieldsets = (
        ("Custos / Despesas da ação", {
            "fields": (
                "custo_premio",
                "premio_top_comprador",
                "outras_despesas",
            )
        }),
        ("(Opcional) Sobrescrever taxa padrão", {
            "classes": ("collapse",),
            "fields": (
                "taxa_admin_percentual",
                "taxa_admin_fixa",
            )
        }),
    )


# -------------------------------------------------
# Empresa
# -------------------------------------------------
@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nome",
        "documento",
        "email",
        "telefone",
        # 🆕 mostrar as taxas padrão
        "taxa_admin_percentual_padrao",
        "taxa_admin_fixa_padrao",
        "created_at",
    )
    search_fields = ("nome", "documento", "email")
    list_filter = ("created_at",)
    # se quiser já editar direto:
    fieldsets = (
        (None, {
            "fields": (
                "nome",
                "documento",
                "email",
                "telefone",
            )
        }),
        ("Taxas padrão (aplicam em todas as rifas, se a rifa não sobrescrever)", {
            "fields": (
                "taxa_admin_percentual_padrao",
                "taxa_admin_fixa_padrao",
            )
        }),
    )


# -------------------------------------------------
# EFI CONFIG
# -------------------------------------------------
@admin.register(EfiConfig)
class EfiConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nome",
        "empresa",
        "rifa",
        "sandbox",
        "ativo",
        "chave_pix",
        "criado_em",
    )
    list_filter = ("ativo", "sandbox", "empresa")
    search_fields = ("nome", "client_id", "chave_pix")
    autocomplete_fields = ("empresa", "rifa")


# -------------------------------------------------
# Clientes
# -------------------------------------------------
@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nome",
        "email",
        "telefone",
        "cpf",
        "created_at",
        "pedidos_count",
        "total_gasto",
    )
    search_fields = ("nome", "email", "cpf", "telefone")
    list_filter = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _pedidos=Count("pedidos", distinct=True),
            _gasto=Sum("pedidos__total", filter=Q(pedidos__status=Pedido.PAGO)),
        )

    @admin.display(ordering="_pedidos", description="Pedidos")
    def pedidos_count(self, obj):
        return obj._pedidos or 0

    @admin.display(ordering="_gasto", description="Gasto total")
    def total_gasto(self, obj):
        return f"R$ {(obj._gasto or 0):.2f}"


# -------------------------------------------------
# Rifa
# -------------------------------------------------
@admin.register(Rifa)
class RifaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "titulo",
        "slug",
        "empresa",
        "preco_numero",
        "quantidade_numeros",
        "inicio_vendas",
        "fim_vendas",
        "ativo",
        "em_vendas_badge",
        "meio_pagamento",
        "efi_config",
        "mostrar_top_compradores",
    )
    list_filter = (
        "ativo",
        "inicio_vendas",
        "fim_vendas",
        "mostrar_top_compradores",
        "meio_pagamento",
        "empresa",
    )
    search_fields = ("titulo", "slug", "descricao")
    # 🆕 agora tem financeiro e cotas premiadas
    inlines = [PremioInline, CotaPremiadaInline, RifaFinanceiroInline]
    readonly_fields = ("created_at",)
    autocomplete_fields = ("empresa", "efi_config")

    @admin.display(description="Status vendas")
    def em_vendas_badge(self, obj):
        color = "#16a34a" if obj.em_vendas else "#ef4444"
        txt = "Em vendas" if obj.em_vendas else "Fora da janela"
        return format_html('<b style="color:{}">{}</b>', color, txt)


# -------------------------------------------------
# Número
# -------------------------------------------------
@admin.register(Numero)
class NumeroAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rifa",
        "numero",
        "status",
        "cliente",
        "reservado_em",
        "pedido",
        "expirado_agora",
    )
    list_filter = ("status", "rifa")
    search_fields = (
        "numero",
        "rifa__slug",
        "cliente__nome",
        "cliente__cpf",
        "pedido__protocolo",
    )
    actions = ["liberar_reservas_expiradas"]

    @admin.display(description="Expirou?")
    def expirado_agora(self, obj):
        if obj.expirou():
            return format_html('<span style="color:#ef4444;font-weight:600">Sim</span>')
        return "Não"

    @admin.action(description="Liberar reservas expiradas selecionadas")
    def liberar_reservas_expiradas(self, request, queryset):
        count = 0
        for n in queryset.select_related("rifa"):
            if n.expirou():
                n.status = Numero.LIVRE
                n.cliente = None
                n.reservado_em = None
                n.pedido = None
                n.save(update_fields=["status", "cliente", "reservado_em", "pedido"])
                count += 1
        self.message_user(request, f"{count} reservas liberadas.")


# -------------------------------------------------
# Pedido
# -------------------------------------------------
@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "protocolo",
        "rifa",
        "cliente",
        "subtotal",
        "desconto_regras",
        "desconto_cupom",
        fmt_total,
        "status",
        "criado_em",
        "pago_em",
        is_pago,
    )
    list_filter = ("status", "rifa", "criado_em", "pago_em")
    search_fields = ("protocolo", "cliente__nome", "cliente__cpf", "rifa__slug")
    inlines = [PagamentoInline, CouponRedemptionInline, DiscountApplicationInline]
    readonly_fields = ("pricing_breakdown",)

    actions = ["marcar_pago"]

    @admin.action(description="Marcar como PAGO (atualiza números e cotas)")
    def marcar_pago(self, request, queryset):
        upd = 0
        for p in queryset.select_related("rifa"):
            if p.status != Pedido.PAGO:
                p.marcar_como_pago()
                upd += 1
        self.message_user(request, f"{upd} pedidos marcados como PAGO.")


# -------------------------------------------------
# Pagamento / Webhook
# -------------------------------------------------
@admin.register(Pagamento)
class PagamentoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pedido",
        "provider",
        "provider_preference_id",
        "provider_payment_id",
        "efi_txid",
        "efi_loc_id",
        "status_provider",
        "criado_em",
        "atualizado_em",
    )
    list_filter = ("provider", "status_provider", "criado_em")
    search_fields = (
        "pedido__protocolo",
        "provider_preference_id",
        "provider_payment_id",
        "efi_txid",
    )
    readonly_fields = (
        "qr_code_base64",
        "copia_cola",
        "criado_em",
        "atualizado_em",
    )


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "event_id", "received_at")
    list_filter = ("provider", "received_at")
    search_fields = ("event_id",)


# -------------------------------------------------
# Cupons / Descontos
# -------------------------------------------------
@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "codigo",
        "tipo",
        "valor",
        "ativo",
        "rifa",
        "inicio",
        "fim",
        "max_uso_global",
        "max_uso_por_cliente",
        "uso_cache",
        "acumulavel",
    )
    list_filter = ("ativo", "tipo", "rifa")
    search_fields = ("codigo",)
    readonly_fields = ("uso_cache",)


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("id", "coupon", "pedido", "cliente", "desconto_aplicado", "criado_em")
    search_fields = ("coupon__codigo", "pedido__protocolo", "cliente__cpf")


@admin.register(DiscountRule)
class DiscountRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "nome",
        "rifa",
        "tipo",
        "valor",
        "prioridade",
        "exclusiva",
        "ativo",
        "qtd_numeros_min",
        "inicio",
        "fim",
    )
    list_filter = ("ativo", "exclusiva", "tipo", "rifa")
    search_fields = ("nome",)


@admin.register(DiscountApplication)
class DiscountApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "rule", "pedido", "desconto_aplicado", "criado_em")
    search_fields = ("rule__nome", "pedido__protocolo")


# -------------------------------------------------
# Afiliados
# -------------------------------------------------
@admin.register(Affiliate)
class AffiliateAdmin(admin.ModelAdmin):
    list_display = ("id", "nome", "email", "telefone", "documento", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("nome", "email", "documento")


@admin.register(AffiliateProgram)
class AffiliateProgramAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rifa",
        "modelo_comissao",
        "valor_comissao",
        "cookie_days",
        "atribuicao",
        "permitir_compra_propria",
        "ativo",
    )
    list_filter = ("ativo", "modelo_comissao", "atribuicao", "rifa")
    search_fields = ("rifa__slug",)


@admin.register(AffiliateLink)
class AffiliateLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "affiliate", "program", "token", "url_destino", "created_at")
    search_fields = ("token", "affiliate__nome", "program__rifa__slug")


@admin.register(AffiliateClick)
class AffiliateClickAdmin(admin.ModelAdmin):
    list_display = ("id", "link", "ip", "clicked_at")
    list_filter = ("clicked_at",)
    search_fields = ("link__token", "ip", "fingerprint")


@admin.register(AffiliateAttribution)
class AffiliateAttributionAdmin(admin.ModelAdmin):
    list_display = ("id", "link", "cliente", "expira_em", "modelo")
    list_filter = ("modelo",)
    search_fields = ("link__token", "cliente__cpf")


@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ("id", "affiliate", "pedido", "base_calculo", "percentual", "valor", "status", "criado_em")
    list_filter = ("status", "criado_em")
    search_fields = ("pedido__protocolo", "affiliate__nome")


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("id", "affiliate", "valor_total", "status", "pago_em", "criado_em", "comprovante_url")
    list_filter = ("status", "criado_em")
    search_fields = ("affiliate__nome",)
