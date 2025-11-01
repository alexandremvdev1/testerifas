# rifas/urls.py — AJUSTE COMPLETO
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import painel
from . import api_public as api
from . import views_site as site
from .webhooks import webhook_provider
from .api_public import (
    RifasListView,
    RifaDetailView,
    GradeView,
    TopCompradoresView,
    CriarPedidoView,
    PedidoStatusView,
    ReservarNumeroView,
    webhook_provider,  # 👈 usa o webhook daqui mesmo
)

urlpatterns = [
    # =========================================================
    # PAINEL
    # =========================================================
    path("adminx/login/",  painel.login_view,  name="adminx_login"),
    path("adminx/logout/", painel.logout_view, name="adminx_logout"),
    path("adminx/",        painel.dashboard_view, name="adminx_dashboard"),

    path("adminx/rifas/",                      painel.rifas_list_view,        name="adminx_rifas"),
    path("adminx/rifas/novo/",                 painel.rifa_create_view,       name="adminx_rifa_create"),
    path("adminx/rifas/<int:rifa_id>/editar/", painel.rifa_edit_view,         name="adminx_rifa_edit"),
    path("adminx/rifas/<int:rifa_id>/",        painel.rifa_detail_admin_view, name="adminx_rifa_detail"),

    path("adminx/rifas/<int:rifa_id>/config/", painel.rifa_config_admin_view, name="adminx_rifa_config"),

    path(
    "adminx/rifas/<int:rifa_id>/financeiro/",
    painel.rifa_financeiro_admin_view,
    name="adminx_rifa_financeiro",
    ),


    path("adminx/rifas/<int:rifa_id>/stats.json", painel.rifa_stats_json,             name="adminx_rifa_stats_json"),
    path("adminx/rifas/<int:rifa_id>/top.json",   painel.rifa_top_compradores_json,   name="adminx_rifa_top_json"),

    path("adminx/pedidos/",                    painel.pedidos_list_view,      name="adminx_pedidos"),
    path("adminx/pedidos/<int:pedido_id>/",    painel.pedido_detail_view,     name="adminx_pedido_detail"),
    path("adminx/pedidos/<int:pedido_id>/mark-paid/", painel.pedido_mark_paid_view, name="adminx_pedido_mark_paid"),
    path("adminx/pedidos/<int:pedido_id>/cancel/",    painel.pedido_mark_cancel,    name="adminx_pedido_mark_cancel"),
    path("adminx/pedidos/<int:pedido_id>/numeros.json", painel.pedido_numeros_json, name="adminx_pedido_numeros_json"),

    path("adminx/pedidos/<int:pedido_id>/mark-paid.json",   painel.pedido_mark_paid_json,   name="adminx_pedido_mark_paid_json"),
    path("adminx/pedidos/<int:pedido_id>/mark-cancel.json", painel.pedido_mark_cancel_json, name="adminx_pedido_mark_cancel_json"),
    path("adminx/pedidos/<int:pedido_id>/whatsapp-msg.json", painel.pedido_whatsapp_msg_json, name="adminx_pedido_whatsapp_msg_json"),

    path("adminx/reservas/liberar/",      painel.liberar_reservas_view,  name="adminx_liberar_reservas"),
    path("adminx/export/pedidos.csv",     painel.export_pedidos_csv,     name="adminx_export_pedidos_csv"),
    path("adminx/lookup-numero/",         painel.lookup_numero_view,     name="adminx_lookup_numero"),
    path("adminx/numero-lookup.json",     painel.numero_lookup_json,     name="adminx_numero_lookup_json"),

    path("adminx/empresa/update/", painel.adminx_empresa_update_view, name="adminx_empresa_update"),

    # =========================================================
    # SITE PÚBLICO
    # =========================================================
    path("",                        site.home,             name="home"),
    path("r/<slug:slug>/",          site.rifa_public_view, name="rifa_public"),
    path("r/<slug:slug>/classico/", site.rifa_detail,      name="rifa_detail"),
    path("r/<slug:slug>/checkout/", site.checkout,         name="rifa_checkout"),
    path("r/<slug:slug>/checkout-rapido/", site.checkout_rapido, name="rifa_checkout_rapido"),

    path("pedido/<str:protocolo>/",      site.pedido_status,      name="pedido_status"),
    path("pedido/<str:protocolo>/json/", site.pedido_status_json, name="pedido_status_json"),

    path("api/rifas/<slug:slug>/grade/",        site.api_rifa_grade,        name="rifas_api_grade"),
    path("api/rifas/<slug:slug>/meus-numeros/", site.api_rifa_meus_numeros, name="rifas_api_meus_numeros"),

    path(
        "api/pedidos/",
        csrf_exempt(site.api_pedido_create),
        name="rifas_api_pedido_create",
    ),

    # =========================================================
    # APIs (DRF)
    # =========================================================
    path("api/rifas/",                       RifasListView.as_view(),      name="api_rifas_list"),
    path("api/rifas/<slug:slug>/info/",      RifaDetailView.as_view(),     name="api_rifa_detail"),
    path("api/rifas/<slug:slug>/grade-drf/", GradeView.as_view(),          name="api_rifa_grade_drf"),
    path("api/rifas/<slug:slug>/top/",       TopCompradoresView.as_view(), name="api_rifa_top"),
    path("api/rifas/<slug:slug>/reservar/",  ReservarNumeroView.as_view(), name="api_rifa_reservar"),

    path("api/pedidos/<str:protocolo>/", api.PedidoPublicDetailView.as_view(), name="api-pedido-public"),
    path("api/pedido/<str:protocolo>/", painel.pedido_status_public_json, name="pedido_status_public_json"),

    # criar pedido v2 (POST) — agora só EFI
    path(
        "api/v2/pedidos/",
        csrf_exempt(CriarPedidoView.as_view()),
        name="api_pedidos_v2",
    ),

    # JSON que o painel chama pra listar pedidos da rifa
    path(
        "rifas/<int:rifa_id>/pedidos-json/",
        painel.adminx_rifa_pedidos_json,
        name="adminx_rifa_pedidos_json",
    ),

    path(
        "adminx/rifas/<int:rifa_id>/sorteador/",
        painel.adminx_rifa_sorteador_view,
        name="adminx_rifa_sorteador",
    ),
    path(
        "adminx/rifas/<int:rifa_id>/sorteio-json/",
        painel.adminx_rifa_sorteio_json,
        name="adminx_rifa_sorteio_json",
    ),

    path(
        "api/pedidos/<str:protocolo>/",
        PedidoStatusView.as_view(),
        name="api_pedido_status",
    ),
    path(
        "api/v2/pedidos/<str:protocolo>/",
        PedidoStatusView.as_view(),
        name="api_pedido_status_v2",
    ),

    # =========================================================
    # WEBHOOK (só EFI/registry)
    # =========================================================
    path("api/pagamentos/webhook/<str:provider_key>/", webhook_provider),
]
