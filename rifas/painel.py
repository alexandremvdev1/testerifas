# rifas/painel.py
from __future__ import annotations
import random
import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from django.urls import reverse
from django.db import models
from django import forms
from django.contrib import messages
from django.db import connection
from django.contrib.auth import (
    authenticate,
    get_user,
    login,
    logout,
)
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import LoginForm, RifaForm
from .models import (
    CotaPremiada,
    Numero,
    Pedido,
    Rifa,
    Empresa,
    RifaFinanceiro,
    RifaPremiacao,
    # 👇 novos
    Coupon,
    DiscountRule,
    Affiliate,
    AffiliateProgram,
    AffiliateLink,
    AffiliateClick,
    Commission,
    Payout,
    Cliente,
    EfiConfig,
)



# ================================================================
# HELPERS
# ================================================================
def _to_decimal_br(val: str, default=None):
    """
    Converte '10,00' ou '10.00' para Decimal.
    Se não conseguir converter, devolve `default`.
    """
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    s = s.replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def _is_staff(user) -> bool:
    return bool(user.is_authenticated and user.is_staff)


def _staff_or_403(request: HttpRequest):
    if not _is_staff(get_user(request)):
        return HttpResponseForbidden()
    return None


# ================================================================
# AUTENTICAÇÃO
# ================================================================
def login_view(request: HttpRequest):
    """Tela de login do painel adminx."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("adminx_dashboard")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if user and user.is_staff:
            login(request, user)
            return redirect("adminx_dashboard")
        messages.error(request, "Credenciais inválidas ou sem permissão.")

    return render(request, "rifas/admin/login.html", {"form": form})


def logout_view(request: HttpRequest):
    logout(request)
    return redirect("adminx_login")


@login_required(login_url="adminx_login")
def dashboard_view(request):
    guard = _staff_or_403(request)
    if guard:
        return guard

    # ---------- EMPRESA (seguro mesmo sem tabela) ----------
    empresa = None
    efi = None
    tables = connection.introspection.table_names()
    if "rifas_empresa" in tables and request.user.is_authenticated:
        empresa = Empresa.objects.filter(created_by=request.user).first()
        # 👇 se tiver empresa, tenta pegar a conta EFI dela
        if empresa and "rifas_eficonfig" in tables:
            efi = EfiConfig.objects.filter(empresa=empresa).first()
    # -------------------------------------------------------

    hoje = timezone.localdate()
    tz = timezone.get_current_timezone()
    inicio_dia = datetime.combine(hoje, datetime.min.time(), tzinfo=tz)
    fim_dia = datetime.combine(hoje, datetime.max.time(), tzinfo=tz)

    vendas_hoje = (
        Pedido.objects
        .filter(status=Pedido.PAGO, pago_em__range=(inicio_dia, fim_dia))
        .aggregate(qtd=Count("id"), total=Sum("total"))
    )
    vendas_mes = (
        Pedido.objects
        .filter(
            status=Pedido.PAGO,
            pago_em__year=hoje.year,
            pago_em__month=hoje.month,
        )
        .aggregate(qtd=Count("id"), total=Sum("total"))
    )

    # pode acontecer de você rodar o painel antes de migrar Numero
    try:
        numeros_status = {
            r["status"]: r["qtd"]
            for r in Numero.objects.values("status").annotate(qtd=Count("id"))
        }
    except Exception:
        numeros_status = {"livre": 0, "reservado": 0, "pago": 0}

    rifas_ativas = Rifa.objects.filter(ativo=True).count()
    pendentes = Pedido.objects.filter(status=Pedido.PENDENTE).count()

    ctx = {
        "empresa": empresa,
        "efi": efi,  # 👈 AGORA o template enxerga
        "vendas_hoje": vendas_hoje,
        "vendas_mes": vendas_mes,
        "numeros_status": numeros_status,
        "rifas_ativas": rifas_ativas,
        "pendentes": pendentes,
        "ultimos_pedidos": (
            Pedido.objects
            .select_related("cliente", "rifa")
            .order_by("-criado_em")[:10]
        ),
    }
    return render(request, "rifas/admin/dashboard.html", ctx)

# ================================================================
# RIFAS (listar / criar / editar)
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
def rifas_list_view(request: HttpRequest):
    q = request.GET.get("q", "").strip()

    qs = Rifa.objects.all().order_by("-created_at")
    if q:
        qs = qs.filter(Q(titulo__icontains=q) | Q(slug__icontains=q))

    return render(
        request,
        "rifas/admin/rifas_list.html",
        {
            "rifas": list(qs),
            "q": q,
        },
    )


@user_passes_test(_is_staff, login_url="adminx_login")
def rifa_create_view(request: HttpRequest):
    if request.method == "POST":
        form = RifaForm(request.POST, request.FILES)
        if form.is_valid():
            rifa = form.save(commit=False)
            if request.user.is_authenticated:
                rifa.created_by = request.user
            rifa.save()
            _ensure_grade(rifa)
            messages.success(request, "Rifa criada com sucesso.")
            return redirect("adminx_rifas")
        else:
            for field, errs in form.errors.items():
                for e in errs:
                    messages.error(request, f"{field}: {e}")
    else:
        agora = timezone.localtime()
        form = RifaForm(
            initial={
                "inicio_vendas": (agora + timedelta(minutes=5)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
                "fim_vendas": (agora + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M"),
                "ativo": True,
                "permitir_numero_escolhido": True,
                "limite_por_pedido": 10,
                "minutos_expiracao_reserva": 15,
                "mostrar_top_compradores": True,
                "quantidade_numeros": 1000,
                "preco_numero": "5.00",
            }
        )
    return render(request, "rifas/admin/rifa_form.html", {"form": form, "modo": "novo"})


@user_passes_test(_is_staff, login_url="adminx_login")
@transaction.atomic
def rifa_edit_view(request: HttpRequest, rifa_id: int):
    rifa = get_object_or_404(Rifa, pk=rifa_id)
    old_qtd = int(rifa.quantidade_numeros)
    if request.method == "POST":
        form = RifaForm(request.POST, request.FILES, instance=rifa)
        if form.is_valid():
            rifa = form.save()
            if int(rifa.quantidade_numeros) != old_qtd:
                _ensure_grade(rifa)
            messages.success(request, "Rifa atualizada.")
            return redirect("adminx_rifas")
    else:
        form = RifaForm(instance=rifa)
    return render(
        request,
        "rifas/admin/rifa_form.html",
        {"form": form, "is_edit": True, "rifa": rifa},
    )


def _ensure_grade(rifa: Rifa):
    """
    Garante a criação dos números 1..quantidade_numeros para a rifa (não remove existentes).
    """
    existentes = set(
        Numero.objects.filter(rifa=rifa).values_list("numero", flat=True)
    )
    to_create = []
    for n in range(1, int(rifa.quantidade_numeros) + 1):
        if n not in existentes:
            to_create.append(Numero(rifa=rifa, numero=n, status=Numero.LIVRE))
    if to_create:
        Numero.objects.bulk_create(to_create, ignore_conflicts=True)


# ================================================================
# PEDIDOS (listar / detalhe / ações)
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
def pedidos_list_view(request: HttpRequest):
    status_f = request.GET.get("status") or ""
    rifa_f = request.GET.get("rifa") or ""
    q = request.GET.get("q") or ""

    qs = Pedido.objects.select_related("cliente", "rifa").order_by("-criado_em")
    if status_f:
        qs = qs.filter(status=status_f)
    if rifa_f:
        qs = qs.filter(rifa__id=rifa_f)
    if q:
        qs = qs.filter(
            Q(protocolo__icontains=q)
            | Q(cliente__nome__icontains=q)
            | Q(cliente__cpf__icontains=q)
        )

    rifas = Rifa.objects.order_by("titulo")
    ctx = {
        "pedidos": qs[:500],
        "rifas": rifas,
        "status_f": status_f,
        "rifa_f": rifa_f,
        "q": q,
    }
    return render(request, "rifas/admin/pedidos_list.html", ctx)


@user_passes_test(_is_staff, login_url="adminx_login")
def pedido_detail_view(request: HttpRequest, pedido_id: int):
    p = get_object_or_404(
        Pedido.objects.select_related("cliente", "rifa", "pagamento"), pk=pedido_id
    )
    nums = list(p.numeros.order_by("numero").values_list("numero", flat=True))
    return render(
        request, "rifas/admin/pedido_detail.html", {"p": p, "numeros": nums}
    )


@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
@transaction.atomic
def pedido_mark_paid_view(request: HttpRequest, pedido_id: int):
    p = get_object_or_404(Pedido.objects.select_for_update(), pk=pedido_id)
    if p.status != Pedido.PAGO:
        p.status = Pedido.PAGO
        p.pago_em = timezone.now()
        p.save(update_fields=["status", "pago_em"])
        p.numeros.update(status=Numero.PAGO)
        messages.success(request, "Pedido marcado como PAGO.")
    return redirect("adminx_pedido_detail", pedido_id=pedido_id)


@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
def pedido_mark_paid(request: HttpRequest, pedido_id: int):
    p = get_object_or_404(Pedido, pk=pedido_id)
    if p.status != Pedido.PAGO:
        p.status = Pedido.PAGO
        p.pago_em = timezone.now()
        p.save(update_fields=["status", "pago_em"])
        p.numeros.update(status=Numero.PAGO)
        messages.success(request, f"Pedido {p.protocolo} marcado como PAGO.")
    return redirect("adminx_pedidos")


@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
def pedido_mark_cancel(request: HttpRequest, pedido_id: int):
    p = get_object_or_404(Pedido, pk=pedido_id)
    if p.status != Pedido.CANCELADO:
        p.status = Pedido.CANCELADO
        p.save(update_fields=["status"])
        p.numeros.update(
            status=Numero.LIVRE,
            pedido=None,
            cliente=None,
            reservado_em=None,
        )
        messages.warning(request, f"Pedido {p.protocolo} marcado como CANCELADO.")
    return redirect("adminx_pedidos")


@user_passes_test(_is_staff, login_url="adminx_login")
def pedido_numeros_json(request: HttpRequest, pedido_id: int):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return HttpResponseBadRequest("Somente AJAX")
    p = get_object_or_404(Pedido, pk=pedido_id)
    nums = (
        Numero.objects.filter(pedido=p)
        .order_by("numero")
        .values_list("numero", flat=True)
    )
    return JsonResponse({"pedido": p.id, "numeros": list(nums)})


# ================================================================
# RESERVAS / EXPORT
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
@transaction.atomic
def liberar_reservas_view(request: HttpRequest):
    """
    Libera reservas expiradas e expira pedidos pendentes que ficaram sem números.
    (força uma varredura final nos pedidos pendentes)
    """
    # tenta usar a task (a nossa de cima)
    try:
        from .tasks import liberar_reservas_expiradas
        n_liberados, p_expirados = liberar_reservas_expiradas()
    except Exception:
        # fallback antigo: bem parecido com o que vc já tinha
        n_liberados = 0
        p_expirados = 0
        agora = timezone.now()
        from .models import Numero, Pedido

        reservados = (
            Numero.objects
            .select_related("rifa", "pedido")
            .filter(status=Numero.RESERVADO)
        )
        for num in reservados:
            if num.reservado_em and (
                agora - num.reservado_em
            ).total_seconds() > (num.rifa.minutos_expiracao_reserva * 60):
                ped = num.pedido
                # libera o número
                num.status = Numero.LIVRE
                num.pedido = None
                num.cliente = None
                num.reservado_em = None
                num.save(update_fields=["status", "pedido", "cliente", "reservado_em"])
                n_liberados += 1

                # se o pedido existir e estiver pendente e ficou sem números -> expira
                if ped and ped.status == Pedido.PENDENTE and ped.numeros.count() == 0:
                    ped.status = Pedido.EXPIRADO
                    ped.save(update_fields=["status"])
                    p_expirados += 1

    # 🔒 garantia EXTRA:
    # se por qualquer motivo ainda existir pedido PENDENTE sem nenhum número,
    # expira agora.
    from .models import Pedido
    vazios = (
        Pedido.objects
        .filter(status=Pedido.PENDENTE)
        .annotate(qtd=models.Count("numeros"))
        .filter(qtd=0)
    )
    extra = vazios.update(status=Pedido.EXPIRADO)
    p_expirados += extra

    messages.info(
        request,
        f"Reservas liberadas: {n_liberados}; pedidos expirados: {p_expirados}."
    )
    return redirect("adminx_pedidos")




@user_passes_test(_is_staff, login_url="adminx_login")
def export_pedidos_csv(request: HttpRequest):
    qs = Pedido.objects.select_related("cliente", "rifa").order_by("-criado_em")[:5000]

    def rows():
        yield [
            "protocolo",
            "rifa",
            "cliente",
            "cpf",
            "email",
            "telefone",
            "qtd_numeros",
            "subtotal",
            "desc_regras",
            "desc_cupom",
            "total",
            "status",
            "criado_em",
            "pago_em",
        ]
        for p in qs:
            yield [
                p.protocolo,
                p.rifa.titulo,
                p.cliente.nome,
                p.cliente.cpf,
                p.cliente.email,
                p.cliente.telefone,
                p.numeros.count(),
                f"{p.subtotal:.2f}",
                f"{p.desconto_regras:.2f}",
                f"{p.desconto_cupom:.2f}",
                f"{p.total:.2f}",
                p.status,
                p.criado_em.isoformat(),
                p.pago_em.isoformat() if p.pago_em else "",
            ]

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())
    resp = StreamingHttpResponse(
        (writer.writerow(r) for r in rows()), content_type="text/csv"
    )
    resp["Content-Disposition"] = 'attachment; filename="pedidos.csv"'
    return resp


# ================================================================
# LOOKUP DE NÚMERO (GUI)
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
def lookup_numero_view(request: HttpRequest):
    q = request.GET.get("n")
    rifa_id = request.GET.get("rifa")
    rifas = Rifa.objects.all().order_by("titulo")
    numero = None
    pedido = None
    if q and rifa_id:
        try:
            n_int = int(q)
            numero = (
                Numero.objects.select_related(
                    "pedido",
                    "pedido__cliente",
                    "rifa",
                )
                .filter(rifa_id=rifa_id, numero=n_int)
                .first()
            )
            if numero and numero.pedido_id:
                pedido = numero.pedido
        except ValueError:
            numero = None
    ctx = {
        "rifas": rifas,
        "q": q,
        "rifa_f": rifa_id,
        "numero": numero,
        "pedido": pedido,
    }
    return render(request, "rifas/admin/lookup_numero.html", ctx)


# ================================================================
# RIFA: PÁGINA DE DETALHE (VISÃO)
# ================================================================
def rifa_detail_admin_view(request, rifa_id):
    rifa = get_object_or_404(Rifa, id=rifa_id)

    # 1) base
    total_configurado = rifa.quantidade_numeros or 0
    numeros_qs = Numero.objects.filter(rifa=rifa)

    total_criado = numeros_qs.count()
    pagos = numeros_qs.filter(status=Numero.PAGO).count()
    reservados = numeros_qs.filter(status=Numero.RESERVADO).count()
    livres = numeros_qs.filter(status=Numero.LIVRE).count()

    # 2) últimos pedidos (SEM cancelado e SEM expirado)
    ultimos_pedidos = (
        Pedido.objects
        .filter(rifa=rifa)
        .exclude(status__in=[Pedido.CANCELADO, Pedido.EXPIRADO])
        .select_related("cliente")
        .order_by("-criado_em")[:20]
    )

    # 3) top compradores
    top_compradores = (
        numeros_qs
        .filter(
            status__in=[Numero.PAGO, Numero.RESERVADO],
            cliente__isnull=False,
        )
        .values("cliente__nome", "cliente__cpf", "cliente__telefone")
        .annotate(qtd=Count("id"))
        .order_by("-qtd")[:10]
    )

    # 4) total de vendas (só pedidos bons)
    vendas_total = (
        Pedido.objects
        .filter(rifa=rifa)
        .exclude(status__in=[Pedido.CANCELADO, Pedido.EXPIRADO])
        .aggregate(total=Sum("total"))
        .get("total") or 0
    )

    # 5) último pagamento aprovado
    ultimo_pagamento = (
        Pedido.objects
        .filter(rifa=rifa, status=Pedido.PAGO)
        .select_related("cliente")
        .order_by("-pago_em", "-criado_em")
        .first()
    )

    # =========================================================
    # 6) ÚLTIMO NÚMERO COMPRADO (O QUE FECHOU A AÇÃO)
    # =========================================================
    ultimo_numero_final = None
    ultimo_numero_vendido = None

    # 6.1: tenta pegar exatamente o número FINAL da rifa (ex.: 1000)
    if total_configurado > 0:
      ultimo_numero_final = (
          numeros_qs
          .filter(
              numero=total_configurado,
              status__in=[Numero.PAGO, Numero.RESERVADO]
          )
          .select_related("cliente", "pedido", "pedido__cliente")
          .first()
      )

    # 6.2: se ainda não venderam o último número da grade,
    # pega o MAIOR número que já saiu (pago ou reservado)
    if not ultimo_numero_final:
        ultimo_numero_vendido = (
            numeros_qs
            .filter(status__in=[Numero.PAGO, Numero.RESERVADO])
            .select_related("cliente", "pedido", "pedido__cliente")
            .order_by("-numero")  # o maior número que já saiu
            .first()
        )

    # =========================================================
    # 7) GANHADORES DAS COTAS PREMIADAS
    # =========================================================
    # pega todas as cotas ativas dessa rifa
    cotas_da_rifa = CotaPremiada.objects.filter(rifa=rifa, ativo=True).order_by("numero")

    cotas_premiadas_ganhas = []
    for cota in cotas_da_rifa:
        # procura se o número dessa cota já foi PAGO
        num_pago = (
            numeros_qs
            .filter(numero=cota.numero, status=Numero.PAGO)
            .select_related("cliente", "pedido", "pedido__cliente")
            .first()
        )
        if not num_pago:
            # ainda não saiu / ainda não pagou -> não mostra
            continue

        # tenta pegar o cliente (direto no número ou via pedido)
        cliente_nome = ""
        cliente_tel = ""
        if num_pago.cliente:
            cliente_nome = num_pago.cliente.nome or ""
            cliente_tel = num_pago.cliente.telefone or ""
        elif num_pago.pedido and num_pago.pedido.cliente:
            cliente_nome = num_pago.pedido.cliente.nome or ""
            cliente_tel = num_pago.pedido.cliente.telefone or ""

        cotas_premiadas_ganhas.append({
            "numero": cota.numero,
            "descricao": cota.descricao,
            "valor_premio": cota.valor_premio,
            "cliente_nome": cliente_nome,
            "cliente_telefone": cliente_tel,
        })

    # =========================================================
    # 8) regra do sorteio (a sua)
    # =========================================================
    pode_sortear = False
    if total_configurado > 0:
        if total_criado == total_configurado and pagos == total_configurado:
            pode_sortear = True

    context = {
        "rifa": rifa,
        "total_numeros": total_configurado,
        "pagos": pagos,
        "reservados": reservados,
        "livres": livres,
        "vendas_total": vendas_total,
        "ultimos_pedidos": ultimos_pedidos,
        "top_compradores": top_compradores,
        "pode_sortear": pode_sortear,

        # novos:
        "ultimo_pagamento": ultimo_pagamento,
        # se o final existir, usa ele; senão, usa o último vendido
        "ultimo_numero": ultimo_numero_final or ultimo_numero_vendido,
        "ultimo_numero_final": ultimo_numero_final,
        "ultimo_numero_vendido": ultimo_numero_vendido,
        "cotas_premiadas_ganhas": cotas_premiadas_ganhas,
    }

    return render(request, "rifas/admin/rifa_detail.html", context)

# ================================================================
# RIFA: PÁGINA DE CONFIGURAÇÃO
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
@transaction.atomic
def rifa_config_admin_view(request: HttpRequest, rifa_id: int):
    """
    Tela que ajusta TUDO da rifa, incluindo cotas premiadas.
    """
    rifa = get_object_or_404(Rifa, pk=rifa_id)
    old_qtd = int(rifa.quantidade_numeros or 0)

    if request.method == "POST":
        # ------------------------
        # CAMPOS BÁSICOS
        # ------------------------
        rifa.titulo = request.POST.get("titulo") or rifa.titulo
        rifa.slug = request.POST.get("slug") or rifa.slug
        rifa.descricao = request.POST.get("descricao") or ""

        rifa.ativo = bool(request.POST.get("ativo"))
        rifa.mostrar_top_compradores = bool(
            request.POST.get("mostrar_top_compradores")
        )
        rifa.permitir_numero_escolhido = bool(
            request.POST.get("permitir_numero_escolhido")
        )

        # PREÇO
        preco_raw = (request.POST.get("preco_numero") or "").strip()
        if preco_raw:
            preco_dec = _to_decimal_br(preco_raw)
            if preco_dec is None:
                messages.error(request, "Preço inválido. Use 10,00.")
            else:
                rifa.preco_numero = preco_dec

        # QUANTIDADE
        qtd_raw = (request.POST.get("quantidade_numeros") or "").strip()
        if qtd_raw:
            try:
                rifa.quantidade_numeros = int(qtd_raw)
            except Exception:
                messages.error(request, "Quantidade de números inválida.")

        # DATAS
        def _parse_dt(name: str):
            v = request.POST.get(name)
            if not v:
                return None
            try:
                dt = datetime.strptime(v, "%Y-%m-%dT%H:%M")
            except ValueError:
                return None
            return timezone.make_aware(dt, timezone.get_current_timezone())

        rifa.inicio_vendas = _parse_dt("inicio_vendas")
        rifa.fim_vendas = _parse_dt("fim_vendas")

        # LINKS
        rifa.link_whatsapp = request.POST.get("link_whatsapp") or ""
        rifa.link_grupo = request.POST.get("link_grupo") or ""

        # CONTROLES
        min_exp = (request.POST.get("minutos_expiracao_reserva") or "").strip()
        if min_exp:
            try:
                rifa.minutos_expiracao_reserva = int(min_exp)
            except Exception:
                messages.error(request, "Minutos de expiração inválido.")

        lim = (request.POST.get("limite_por_pedido") or "").strip()
        if lim:
            try:
                rifa.limite_por_pedido = int(lim)
            except Exception:
                messages.error(request, "Limite por pedido inválido.")

        # BANNER
        if "banner" in request.FILES:
            rifa.banner = request.FILES["banner"]

        # ----------------------------------------------------
        # COTAS PREMIADAS
        # ----------------------------------------------------
        cotas_final = []
        numeros = request.POST.getlist("cota_numero[]")
        descrs = request.POST.getlist("cota_desc[]")
        valores = request.POST.getlist("cota_valor[]")

        for idx, n_raw in enumerate(numeros):
            n_raw = (n_raw or "").strip()
            if not n_raw:
                continue
            try:
                n_int = int(n_raw)
            except Exception:
                continue

            d_raw = (descrs[idx] if idx < len(descrs) else "").strip()
            v_raw = (valores[idx] if idx < len(valores) else "").strip()

            item = {"numero": n_int}
            if d_raw:
                item["descricao"] = d_raw
            if v_raw:
                v_dec = _to_decimal_br(v_raw, default=v_raw)
                item["valor_premio"] = (
                    str(v_dec) if isinstance(v_dec, Decimal) else v_raw
                )
            cotas_final.append(item)

        # se não veio por linha, tenta JSON bruto
        if not cotas_final:
            raw_json = (request.POST.get("cotas_premiadas") or "").strip()
            if raw_json:
                try:
                    cotas_final = json.loads(raw_json)
                except Exception:
                    messages.error(request, "JSON de cotas premiadas inválido.")
                    cotas_final = []

        # salva cotas
        if CotaPremiada is not None:
            # modelo separado: apaga e recria
            CotaPremiada.objects.filter(rifa=rifa).delete()
            for item in cotas_final:
                numero = item.get("numero")
                if not numero:
                    continue
                CotaPremiada.objects.create(
                    rifa=rifa,
                    numero=numero,
                    descricao=item.get("descricao", ""),
                    valor_premio=item.get("valor_premio", ""),
                    ativo=True,
                )
        else:
            # JSONField na própria rifa
            rifa.cotas_premiadas = cotas_final or []

        # salva rifa
        rifa.save()

        # se mudou quantidade, refaz grade
        if int(rifa.quantidade_numeros or 0) != old_qtd:
            _ensure_grade(rifa)

        messages.success(request, "Configurações da rifa salvas.")
        return redirect("adminx_rifa_config", rifa_id=rifa.id)

    # ------------------ GET ------------------
    cotas = []
    if CotaPremiada is not None:
        for c in CotaPremiada.objects.filter(rifa=rifa, ativo=True).order_by("numero"):
            cotas.append(
                {
                    "numero": c.numero,
                    "descricao": c.descricao or "",
                    "valor_premio": c.valor_premio or "",
                }
            )
    else:
        if rifa.cotas_premiadas:
            data = rifa.cotas_premiadas
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = []
            if isinstance(data, list):
                for item in data:
                    cotas.append(
                        {
                            "numero": item.get("numero"),
                            "descricao": item.get("descricao", ""),
                            "valor_premio": item.get("valor_premio")
                            or item.get("valor")
                            or "",
                        }
                    )

    return render(
        request,
        "rifas/admin/rifa_config.html",
        {
            "rifa": rifa,
            "cotas": cotas,
        },
    )


# ================================================================
# JSONs AUXILIARES
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
@require_GET
def rifa_stats_json(request: HttpRequest, rifa_id: int):
    rifa = get_object_or_404(Rifa, pk=rifa_id)
    data = {
        "rifa": rifa.id,
        "em_vendas": bool(getattr(rifa, "em_vendas", False)),
        "inicio_vendas": rifa.inicio_vendas.isoformat() if rifa.inicio_vendas else None,
        "fim_vendas": rifa.fim_vendas.isoformat() if rifa.fim_vendas else None,
        "livres": Numero.objects.filter(rifa=rifa, status=Numero.LIVRE).count(),
        "reservados": Numero.objects.filter(
            rifa=rifa, status=Numero.RESERVADO
        ).count(),
        "pagos": Numero.objects.filter(rifa=rifa, status=Numero.PAGO).count(),
        "total": Numero.objects.filter(rifa=rifa).count(),
        "preco_numero": str(rifa.preco_numero),
    }
    return JsonResponse(data)


@user_passes_test(_is_staff, login_url="adminx_login")
@require_GET
def rifa_top_compradores_json(request: HttpRequest, rifa_id: int):
    rifa = get_object_or_404(Rifa, pk=rifa_id)
    top = (
        Numero.objects.filter(
            rifa=rifa,
            status=Numero.PAGO,
            cliente__isnull=False,
        )
        .values("cliente__id", "cliente__nome", "cliente__cpf", "cliente__telefone")
        .annotate(qtd=Count("id"))
        .order_by("-qtd")[:20]
    )
    return JsonResponse({"rifa": rifa.id, "top": list(top)})


# ================================================================
# PEDIDOS AJAX
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
@transaction.atomic
def pedido_mark_paid_json(request: HttpRequest, pedido_id: int):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return HttpResponseBadRequest("Somente AJAX")

    p = get_object_or_404(Pedido.objects.select_for_update(), pk=pedido_id)
    if p.status != Pedido.PAGO:
        p.status = Pedido.PAGO
        p.pago_em = timezone.now()
        p.save(update_fields=["status", "pago_em"])
        p.numeros.update(status=Numero.PAGO)
    return JsonResponse(
        {
            "ok": True,
            "status": p.status,
            "pago_em": p.pago_em.isoformat() if p.pago_em else None,
        }
    )


@user_passes_test(_is_staff, login_url="adminx_login")
@require_POST
@transaction.atomic
def pedido_mark_cancel_json(request: HttpRequest, pedido_id: int):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return HttpResponseBadRequest("Somente AJAX")

    p = get_object_or_404(Pedido.objects.select_for_update(), pk=pedido_id)
    if p.status != Pedido.CANCELADO:
        p.status = Pedido.CANCELADO
        p.save(update_fields=["status"])
        p.numeros.update(
            status=Numero.LIVRE,
            pedido=None,
            cliente=None,
            reservado_em=None,
        )
    return JsonResponse({"ok": True, "status": p.status})


@user_passes_test(_is_staff, login_url="adminx_login")
@require_GET
def pedido_whatsapp_msg_json(request: HttpRequest, pedido_id: int):
    p = get_object_or_404(
        Pedido.objects.select_related("cliente", "rifa"), pk=pedido_id
    )
    numeros = list(p.numeros.order_by("numero").values_list("numero", flat=True))
    nums_str = ", ".join(str(n) for n in numeros) if numeros else "—"
    status_label = p.get_status_display() if hasattr(p, "get_status_display") else p.status
    texto = (
        f"Olá, {p.cliente.nome}! 👋\n"
        f"Seu pedido {p.protocolo} da rifa '{p.rifa.titulo}' está *{status_label}*.\n"
        f"Números: {nums_str}\n"
        f"Total: R$ {p.total:.2f}\n"
        f"Qualquer dúvida, estamos à disposição! ✅"
    )
    return JsonResponse(
        {
            "ok": True,
            "telefone": p.cliente.telefone,
            "mensagem": texto,
        }
    )


# ================================================================
# LOOKUP RÁPIDO DE NÚMERO (AJAX)
# ================================================================
@user_passes_test(_is_staff, login_url="adminx_login")
@require_GET
def numero_lookup_json(request: HttpRequest):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return HttpResponseBadRequest("Somente AJAX")

    rifa_id = request.GET.get("rifa")
    n = request.GET.get("n")
    if not rifa_id or not n:
        return JsonResponse(
            {"ok": False, "error": "Parâmetros obrigatórios ausentes."}, status=400
        )

    try:
        n_int = int(n)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Número inválido."}, status=400)

    numero = (
        Numero.objects.select_related("rifa", "pedido", "pedido__cliente")
        .filter(rifa_id=rifa_id, numero=n_int)
        .first()
    )
    if not numero:
        return JsonResponse(
            {"ok": False, "error": "Número não encontrado para esta rifa."}, status=404
        )

    data = {
        "ok": True,
        "rifa": numero.rifa_id,
        "numero": numero.numero,
        "status": numero.status,
        "pedido_id": numero.pedido_id,
        "cliente": None,
    }
    if numero.pedido_id and numero.pedido and numero.pedido.cliente_id:
        c = numero.pedido.cliente
        data["cliente"] = {
            "id": c.id,
            "nome": c.nome,
            "cpf": c.cpf,
            "email": c.email,
            "telefone": c.telefone,
        }
        data["protocolo"] = numero.pedido.protocolo

    return JsonResponse(data)

def adminx_rifa_pedidos_json(request, rifa_id):
    """
    Retorna os últimos pedidos da rifa + info extra:
    - últimos pedidos (SEM cancelado e SEM expirado)
    - último pagamento aprovado
    - último número comprado (prioridade: último que FECHA a ação)
    - ganhadores das cotas premiadas
    """
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        return JsonResponse({"error": "Somente AJAX"}, status=400)

    rifa = get_object_or_404(Rifa, id=rifa_id)
    total_configurado = rifa.quantidade_numeros or 0

    # -------------------------------------------------
    # 1) ÚLTIMOS PEDIDOS (sem cancelado e sem expirado)
    # -------------------------------------------------
    # alguns projetos usam "expirado" como string, outros como constante
    expirado_val = getattr(Pedido, "EXPIRADO", "expirado")

    pedidos_qs = (
        Pedido.objects
        .filter(rifa=rifa)
        .exclude(status__in=[Pedido.CANCELADO, expirado_val])
        .select_related("cliente")
        .order_by("-criado_em")[:25]
    )

    pedidos_data = []
    for p in pedidos_qs:
        tel = p.cliente.telefone if p.cliente_id and p.cliente.telefone else ""
        tel_clean = (
            tel.replace(" ", "")
               .replace("-", "")
               .replace("(", "")
               .replace(")", "")
               .replace(".", "")
        )

        try:
            url_detail = reverse("adminx_pedido_detail", args=[p.id])
        except Exception:
            url_detail = ""

        pedidos_data.append({
            "id": p.id,
            "protocolo": p.protocolo,
            "cliente_nome": p.cliente.nome if p.cliente_id else "",
            "cliente_telefone": tel_clean,
            "total": f"{p.total}" if p.total is not None else "0,00",
            "status": (p.status or "").lower(),
            "criado": p.criado_em.strftime("%d/%m %H:%M") if p.criado_em else "",
            "url_detail": url_detail,
        })

    # -------------------------------------------------
    # 2) ÚLTIMO PAGAMENTO APROVADO
    # -------------------------------------------------
    ultimo_pago_obj = (
        Pedido.objects
        .filter(rifa=rifa, status=Pedido.PAGO)
        .select_related("cliente")
        .order_by("-pago_em", "-criado_em")
        .first()
    )
    if ultimo_pago_obj:
        tel = ultimo_pago_obj.cliente.telefone if ultimo_pago_obj.cliente_id and ultimo_pago_obj.cliente.telefone else ""
        tel_clean = (
            tel.replace(" ", "")
               .replace("-", "")
               .replace("(", "")
               .replace(")", "")
               .replace(".", "")
        )
        ultimo_pagamento_data = {
            "protocolo": ultimo_pago_obj.protocolo,
            "cliente_nome": ultimo_pago_obj.cliente.nome if ultimo_pago_obj.cliente_id else "",
            "cliente_telefone": tel_clean,
            "total": f"{ultimo_pago_obj.total}" if ultimo_pago_obj.total is not None else "0,00",
            "pago_em": ultimo_pago_obj.pago_em.strftime("%d/%m %H:%M") if ultimo_pago_obj.pago_em else "",
        }
    else:
        ultimo_pagamento_data = None

    # -------------------------------------------------
    # 3) ÚLTIMO NÚMERO COMPRADO (O QUE FECHA A AÇÃO)
    # -------------------------------------------------
    # regra que você pediu:
    # 1º: tentar pegar o número == total_configurado com status pago/reservado
    # 2º: se não existir, pegar o MAIOR número pago/reservado
    ultimo_numero_data = None

    numeros_qs = Numero.objects.filter(rifa=rifa)

    # tenta o ÚLTIMO DA AÇÃO
    ultimo_restante = None
    if total_configurado > 0:
        ultimo_restante = (
            numeros_qs
            .filter(
                numero=total_configurado,
                status__in=[Numero.PAGO, Numero.RESERVADO]
            )
            .select_related("cliente", "pedido__cliente")
            .first()
        )

    if ultimo_restante:
        # pegou o que fecha a ação
        cli = (
            ultimo_restante.cliente
            or (ultimo_restante.pedido.cliente if ultimo_restante.pedido_id and ultimo_restante.pedido and ultimo_restante.pedido.cliente_id else None)
        )
        tel_cli = cli.telefone if cli and cli.telefone else ""
        tel_cli_clean = (
            tel_cli.replace(" ", "")
                  .replace("-", "")
                  .replace("(", "")
                  .replace(")", "")
                  .replace(".", "")
        )
        ultimo_numero_data = {
            "ultimo_numero_restante": ultimo_restante.numero,
            "cliente_nome": cli.nome if cli else "",
            "cliente_telefone": tel_cli_clean,
            "status": ultimo_restante.status.lower(),
        }
    else:
        # não fechou ainda → pega o maior número já comprado
        ult_num = (
            numeros_qs
            .filter(status__in=[Numero.PAGO, Numero.RESERVADO])
            .select_related("cliente", "pedido__cliente")
            .order_by("-numero")  # maior número mesmo
            .first()
        )
        if ult_num:
            cli = (
                ult_num.cliente
                or (ult_num.pedido.cliente if ult_num.pedido_id and ult_num.pedido and ult_num.pedido.cliente_id else None)
            )
            tel_cli = cli.telefone if cli and cli.telefone else ""
            tel_cli_clean = (
                tel_cli.replace(" ", "")
                      .replace("-", "")
                      .replace("(", "")
                      .replace(")", "")
                      .replace(".", "")
            )
            ultimo_numero_data = {
                "ultimo_numero_vendido": ult_num.numero,
                "cliente_nome": cli.nome if cli else "",
                "cliente_telefone": tel_cli_clean,
                "status": ult_num.status.lower(),
            }

    # -------------------------------------------------
    # 4) GANHADORES DAS COTAS PREMIADAS
    # -------------------------------------------------
    cotas_premiadas_ganhas = []
    if CotaPremiada is not None:
      # pega as cotas da rifa
      cotas = CotaPremiada.objects.filter(rifa=rifa, ativo=True).order_by("numero")
      for cota in cotas:
          # procura se o número da cota já foi pago (ou pelo menos reservado, se vc quiser mostrar assim)
          num_premiado = (
              numeros_qs
              .filter(
                  numero=cota.numero,
                  status__in=[Numero.PAGO]  # se quiser mostrar reservado tb, põe Numero.RESERVADO aqui
              )
              .select_related("cliente", "pedido__cliente")
              .first()
          )
          if not num_premiado:
              continue  # ainda não foi ganho essa cota

          cli = (
              num_premiado.cliente
              or (num_premiado.pedido.cliente if num_premiado.pedido_id and num_premiado.pedido and num_premiado.pedido.cliente_id else None)
          )
          tel_cli = cli.telefone if cli and cli.telefone else ""
          tel_cli_clean = (
              tel_cli.replace(" ", "")
                     .replace("-", "")
                     .replace("(", "")
                     .replace(")", "")
                     .replace(".", "")
          )

          cotas_premiadas_ganhas.append({
              "numero": cota.numero,
              "descricao": getattr(cota, "descricao", "") or "",
              "valor_premio": f"{getattr(cota, 'valor_premio', 0):.2f}" if getattr(cota, "valor_premio", None) is not None else "",
              "cliente_nome": cli.nome if cli else "",
              "cliente_telefone": tel_cli_clean,
          })

    # -------------------------------------------------
    # 5) RETORNO
    # -------------------------------------------------
    return JsonResponse({
        "pedidos": pedidos_data,
        "ultimo_pagamento": ultimo_pagamento_data,
        # o template já faz fallback: ultimo_numero || ultimo_numero_restante || ultimo_numero_vendido
        "ultimo_numero": ultimo_numero_data,
        "cotas_premiadas_ganhas": cotas_premiadas_ganhas,
    })


def adminx_rifa_sorteador_view(request, rifa_id):
    """
    Tela do sorteador.
    Só libera o botão se TODOS os números da rifa estiverem PAGOS.
    """
    rifa = get_object_or_404(Rifa, pk=rifa_id)

    # total configurado na rifa
    total_configurado = rifa.quantidade_numeros or 0

    # quantos números pagos existem de fato
    qtd_pagos = Numero.objects.filter(rifa=rifa, status=Numero.PAGO).count()

    # condição para estar liberado:
    # - rifa tem quantidade configurada
    # - e a quantidade de pagos == quantidade configurada
    liberado = total_configurado > 0 and qtd_pagos == total_configurado

    context = {
        "rifa": rifa,
        "liberado": liberado,
        "total_configurado": total_configurado,
        "qtd_pagos": qtd_pagos,
    }
    return render(request, "rifas/admin/rifa_sorteador.html", context)


def adminx_rifa_sorteio_json(request, rifa_id):
    """
    Endpoint AJAX que devolve um número sorteado da rifa.
    Só funciona se TODOS os números estiverem pagos.
    """
    # só ajax
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise Http404("Somente AJAX")

    rifa = get_object_or_404(Rifa, pk=rifa_id)

    total_configurado = rifa.quantidade_numeros or 0

    # pega já com pedido e cliente pra não ficar consultando depois
    pagos_qs = (
        Numero.objects
        .filter(rifa=rifa, status=Numero.PAGO)
        .select_related("pedido__cliente", "cliente")
    )
    qtd_pagos = pagos_qs.count()

    # 🔐 trava: se não estiver 100% pago, não sorteia
    if total_configurado == 0 or qtd_pagos == 0 or qtd_pagos != total_configurado:
        return JsonResponse(
            {
                "ok": False,
                "error": "Sorteio bloqueado: ainda não estão todos os números pagos.",
                "total_configurado": total_configurado,
                "qtd_pagos": qtd_pagos,
            },
            status=400,
        )

    # até aqui: está tudo pago ✅
    numeros = list(pagos_qs)
    if not numeros:
        return JsonResponse(
            {
                "ok": False,
                "error": "Nenhum número pago encontrado para sortear.",
            },
            status=400,
        )

    # escolhe 1 número
    escolhido = random.choice(numeros)

    cliente_nome = ""
    cliente_cpf = ""
    cliente_tel = ""
    pedido_url = ""
    cliente_obj = None

    # tenta pegar a partir do pedido
    pedido = getattr(escolhido, "pedido", None)
    if pedido:
        cli = getattr(pedido, "cliente", None)
        if cli:
            cliente_obj = cli
            cliente_nome = cli.nome or ""
            cliente_cpf = cli.cpf or ""
            cliente_tel = cli.telefone or ""
        # tenta montar a url do pedido
        try:
            pedido_url = reverse("adminx_pedido_detail", args=[pedido.id])
        except Exception:
            pedido_url = ""

    # fallback: se o número tem cliente direto
    if not cliente_obj:
        cli = getattr(escolhido, "cliente", None)
        if cli:
            cliente_obj = cli
            if not cliente_nome:
                cliente_nome = cli.nome or ""
            if not cliente_cpf:
                cliente_cpf = cli.cpf or ""
            if not cliente_tel:
                cliente_tel = cli.telefone or ""

    # normaliza tel pro link do Whats
    if cliente_tel:
        cliente_tel = (
            cliente_tel.replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
            .replace(".", "")
        )

    # 👇 AQUI o que você pediu:
    # - quantas cotas esse cliente comprou (números pagos dele nessa rifa)
    # - quanto ele já gastou nessa rifa (soma dos pedidos pagos dele nessa rifa)
    qtd_cotas_cliente = 0
    total_gasto_cliente = 0.0

    if cliente_obj:
        # conta números pagos dessa rifa pra esse cliente
        qtd_cotas_cliente = Numero.objects.filter(
            rifa=rifa,
            status=Numero.PAGO,
            cliente=cliente_obj,
        ).count()

        # soma total de pedidos pagos desse cliente nessa rifa
        soma = (
            Pedido.objects.filter(
                rifa=rifa,
                cliente=cliente_obj,
                status=Pedido.PAGO,
            ).aggregate(s=Sum("total"))["s"]
            or 0
        )
        total_gasto_cliente = float(soma)

    return JsonResponse(
        {
            "ok": True,
            "numero": escolhido.numero,
            "status": escolhido.status,
            "cliente": cliente_nome,
            "cpf": cliente_cpf,
            "telefone": cliente_tel,
            "pedido_url": pedido_url,
            "qtd_cotas_cliente": qtd_cotas_cliente,
            "total_gasto_cliente": f"{total_gasto_cliente:.2f}",
        }
    )

@login_required(login_url="adminx_login")
@require_POST
def adminx_empresa_update_view(request):
    # precisa ser staff/admin do painel
    guard = _staff_or_403(request)
    if guard:
        return guard

    user = request.user
    empresa_id = request.POST.get("empresa_id")

    if empresa_id:
        empresa = get_object_or_404(Empresa, pk=empresa_id)

        # segurança: só o dono ou superuser
        if empresa.created_by and empresa.created_by != user and not user.is_superuser:
            messages.error(request, "Você não tem permissão para editar esta empresa.")
            return redirect("adminx_dashboard")
    else:
        # fallback: cria/pega a empresa do usuário
        empresa, _ = Empresa.objects.get_or_create(
            created_by=user,
            defaults={
                "nome": user.get_full_name() or user.username,
                "email": user.email or "",
            },
        )

    # -------- campos básicos --------
    empresa.nome = (request.POST.get("nome") or empresa.nome).strip()
    empresa.documento = (request.POST.get("documento") or "").strip()
    empresa.email = (request.POST.get("email") or "").strip()
    empresa.telefone = (request.POST.get("telefone") or "").strip()

    # -------- novos campos que também existem no form grande --------
    empresa.whatsapp_suporte = (request.POST.get("whatsapp_suporte") or "").strip()
    empresa.whatsapp_grupo = (request.POST.get("whatsapp_grupo") or "").strip()

    # taxas (se vierem no modal)
    taxa_percent = request.POST.get("taxa_admin_percentual_padrao")
    if taxa_percent is not None:
        empresa.taxa_admin_percentual_padrao = taxa_percent or "0.00"

    taxa_fixa = request.POST.get("taxa_admin_fixa_padrao")
    if taxa_fixa is not None:
        empresa.taxa_admin_fixa_padrao = taxa_fixa or "0.00"

    # 👇 novos de domínio
    empresa.dominio_publico = (request.POST.get("dominio_publico") or "").strip() or None
    empresa.subdominio = (request.POST.get("subdominio") or "").strip() or None

    # logo (opcional)
    if "logo" in request.FILES and request.FILES["logo"]:
        empresa.logo = request.FILES["logo"]

    # garante dono
    if not empresa.created_by:
        empresa.created_by = user

    empresa.save()

    messages.success(request, "Dados da empresa atualizados.")
    return redirect("adminx_dashboard")

# ================================================================
# API PÚBLICA / AJAX PARA CONSULTAR PEDIDO E EXPIRAR SE PRECISAR
# ================================================================
@require_GET
@transaction.atomic
def pedido_status_public_json(request: HttpRequest, protocolo: str):
    """
    Endpoint que o front pode chamar para ver o status do pedido.
    Se tiver passado o tempo de reserva, ele expira o pedido e libera os números.
    """
    pedido = get_object_or_404(Pedido.objects.select_for_update(), protocolo=protocolo)

    # se ainda está pendente, vamos checar se já passou o tempo
    if pedido.status == Pedido.PENDENTE:
        # cada pedido está ligado a uma rifa -> ela tem minutos_expiracao_reserva
        minutos = pedido.rifa.minutos_expiracao_reserva or 0
        limite_seg = minutos * 60

        # o pedido nasceu em criado_em
        agora = timezone.now()
        diff = (agora - pedido.criado_em).total_seconds()

        if limite_seg > 0 and diff > limite_seg:
            # EXPIRA DE VERDADE: libera números e marca o pedido
            for num in pedido.numeros.all():
                num.status = Numero.LIVRE
                num.pedido = None
                num.cliente = None
                num.reservado_em = None
                num.save(
                    update_fields=["status", "pedido", "cliente", "reservado_em"]
                )

            pedido.status = Pedido.EXPIRADO
            pedido.save(update_fields=["status"])

    # monta resposta
    numeros = list(
        pedido.numeros.order_by("numero").values_list("numero", flat=True)
    )

    return JsonResponse(
        {
            "protocolo": pedido.protocolo,
            "status": pedido.status,
            "total": f"{pedido.total:.2f}",
            "rifa": pedido.rifa_id,
            "cliente": {
                "nome": pedido.cliente.nome if pedido.cliente_id else "",
                "telefone": pedido.cliente.telefone if pedido.cliente_id else "",
            },
            "numeros": numeros,
        }
    )

def rifa_financeiro_admin_view(request, rifa_id):
    rifa = get_object_or_404(Rifa, pk=rifa_id)

    # garante que exista registro 1:1
    financeiro, _ = RifaFinanceiro.objects.get_or_create(rifa=rifa)

    # --- 1) POST para marcar premiação como paga ---
    if request.method == "POST" and "pagar_premiacao_id" in request.POST:
        premiacao_id = request.POST.get("pagar_premiacao_id")
        prem = get_object_or_404(RifaPremiacao, pk=premiacao_id, rifa=rifa)
        prem.marcar_pago()
        return redirect("adminx_rifa_financeiro", rifa_id=rifa.id)

    # --- 2) POST para salvar custos/taxas ---
    if request.method == "POST" and "pagar_premiacao_id" not in request.POST:
        form = RifaFinanceiroForm(request.POST, instance=financeiro)
        if form.is_valid():
            form.save()
            return redirect("adminx_rifa_financeiro", rifa_id=rifa.id)
    else:
        form = RifaFinanceiroForm(instance=financeiro)

    # --- 3) garantir premiações das cotas premiadas ---
    cotas = CotaPremiada.objects.filter(rifa=rifa, ativo=True)
    for c in cotas:
        existe = RifaPremiacao.objects.filter(
            rifa=rifa,
            tipo="cota_premiada",
            numero=c.numero,
        ).exists()
        if not existe:
            RifaPremiacao.objects.create(
                rifa=rifa,
                tipo="cota_premiada",
                numero=c.numero,
                descricao=c.descricao or f"Cota premiada #{c.numero}",
                valor=c.valor_premio or Decimal("0.00"),
            )

    # --- 4) garantir premiação do top comprador se houver valor ---
    if financeiro.premio_top_comprador and financeiro.premio_top_comprador > 0:
        # pega o top mesmo (mais pedidos pagos)
        top = (
            Pedido.objects.filter(rifa=rifa, status=Pedido.PAGO)
            .values("cliente__id", "cliente__nome", "cliente__cpf")
            .annotate(qtd=Count("id"))
            .order_by("-qtd")
            .first()
        )
        premiacao_top = RifaPremiacao.objects.filter(rifa=rifa, tipo="top_comprador").first()

        if not premiacao_top:
            RifaPremiacao.objects.create(
                rifa=rifa,
                tipo="top_comprador",
                cliente=Cliente.objects.filter(pk=top["cliente__id"]).first() if top else None,
                descricao="Prêmio Top Comprador",
                valor=financeiro.premio_top_comprador,
            )
        else:
            # atualiza valor se mudou no financeiro
            if premiacao_top.valor != financeiro.premio_top_comprador:
                premiacao_top.valor = financeiro.premio_top_comprador
                premiacao_top.save(update_fields=["valor"])
            # se agora já dá pra vincular o cliente, vincula
            if top and not premiacao_top.cliente_id:
                premiacao_top.cliente = Cliente.objects.filter(pk=top["cliente__id"]).first()
                premiacao_top.save(update_fields=["cliente"])

    # --- 5) resumo calculado no modelo ---
    resumo = financeiro.calcular_resumo()

    pedidos_pagos = (
        Pedido.objects
        .filter(rifa=rifa, status=Pedido.PAGO)
        .select_related("cliente")
        .order_by("-criado_em")
    )
    pedidos = (
        Pedido.objects
        .filter(rifa=rifa)
        .select_related("cliente")
        .order_by("-criado_em")[:200]
    )

    premiacoes = RifaPremiacao.objects.filter(rifa=rifa).order_by("-criado_em")

    top_compradores = (
        Pedido.objects.filter(rifa=rifa, status=Pedido.PAGO)
        .values("cliente__nome", "cliente__cpf")
        .annotate(qtd=Count("id"))
        .order_by("-qtd")[:10]
    )

    ctx = {
        "rifa": rifa,
        "form": form,
        "premiacoes": premiacoes,
        "total_bruto": resumo["total_vendido"],
        "taxa_perc": resumo["taxa_percentual_usada"],
        "taxa_fixa": resumo["taxa_fixa_usada"],
        "total_taxas": (resumo["valor_taxa_percentual"] + resumo["valor_taxa_fixa"]),
        "total_liquido": resumo["lucro_liquido"],
        "qtd_pedidos_pagos": pedidos_pagos.count(),
        "pedidos_pagos": pedidos_pagos,
        "pedidos": pedidos,
        "top_compradores": top_compradores,
    }
    return render(request, "rifas/admin/rifa_financeiro.html", ctx)


class RifaFinanceiroForm(forms.ModelForm):
    class Meta:
        model = RifaFinanceiro
        fields = [
            "custo_premio",
            "premio_top_comprador",
            "outras_despesas",
            "taxa_admin_percentual",
            "taxa_admin_fixa",
        ]
        widgets = {
            "custo_premio": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "premio_top_comprador": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "outras_despesas": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "taxa_admin_percentual": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "taxa_admin_fixa": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }

def _to_decimal_or_none(value: str):
    """
    Converte strings como '0,00', '10,5', '1.234,56' pra Decimal.
    Se vier vazio ou der erro, devolve None.
    """
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    # remove separador de milhar e troca vírgula por ponto
    # ex: "1.234,56" -> "1234.56"
    value = value.replace(".", "").replace(",", ".")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


@login_required(login_url="adminx_login")
def empresa_form_view(request):
    """
    Tela completa de cadastro/edição da empresa.
    1 empresa por usuário.
    Também permite cadastrar/editar UMA EfiConfig ligada a essa empresa.
    """
    empresa = Empresa.objects.filter(created_by=request.user).first()

    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        documento = request.POST.get("documento", "").strip()
        email = request.POST.get("email", "").strip()
        telefone = request.POST.get("telefone", "").strip()
        whatsapp_suporte = request.POST.get("whatsapp_suporte", "").strip()
        whatsapp_grupo = request.POST.get("whatsapp_grupo", "").strip()

        # vêm como string ("0,00" / "10,5" / "")
        taxa_admin_percentual_padrao_raw = request.POST.get("taxa_admin_percentual_padrao") or ""
        taxa_admin_fixa_padrao_raw = request.POST.get("taxa_admin_fixa_padrao") or ""

        # normaliza pra Decimal
        taxa_admin_percentual_padrao = _to_decimal_or_none(taxa_admin_percentual_padrao_raw)
        taxa_admin_fixa_padrao = _to_decimal_or_none(taxa_admin_fixa_padrao_raw)

        # 👇 novos campos
        dominio_publico = request.POST.get("dominio_publico", "").strip()
        subdominio = request.POST.get("subdominio", "").strip()

        if empresa:
            emp = empresa
        else:
            emp = Empresa(created_by=request.user)

        emp.nome = nome
        emp.documento = documento
        emp.email = email
        emp.telefone = telefone
        emp.whatsapp_suporte = whatsapp_suporte
        emp.whatsapp_grupo = whatsapp_grupo
        emp.dominio_publico = dominio_publico
        emp.subdominio = subdominio

        # ⚠️ aqui é onde dava o erro: agora só atribui se converteu
        if taxa_admin_percentual_padrao is not None:
            emp.taxa_admin_percentual_padrao = taxa_admin_percentual_padrao
        else:
            # se quiser permitir vazio, zera
            emp.taxa_admin_percentual_padrao = Decimal("0.00")

        if taxa_admin_fixa_padrao is not None:
            emp.taxa_admin_fixa_padrao = taxa_admin_fixa_padrao
        else:
            emp.taxa_admin_fixa_padrao = Decimal("0.00")

        # logo (opcional)
        if request.FILES.get("logo"):
            emp.logo = request.FILES["logo"]

        # salva empresa primeiro
        emp.save()

        # ---------- bloco EFI ----------
        efi_nome = request.POST.get("efi_nome", "").strip()
        efi_client_id = request.POST.get("efi_client_id", "").strip()
        efi_client_secret = request.POST.get("efi_client_secret", "").strip()
        efi_chave_pix = request.POST.get("efi_chave_pix", "").strip()
        efi_sandbox = request.POST.get("efi_sandbox") == "on"

        # só cria/atualiza se o usuário preencheu algo da EFI
        if efi_nome or efi_client_id or efi_chave_pix:
            efi_obj = EfiConfig.objects.filter(empresa=emp).first()  # 1 por empresa
            if not efi_obj:
                efi_obj = EfiConfig(
                    empresa=emp,
                    nome=efi_nome or "Conta EFI",
                )

            efi_obj.nome = efi_nome or efi_obj.nome
            efi_obj.client_id = efi_client_id
            efi_obj.client_secret = efi_client_secret
            efi_obj.chave_pix = efi_chave_pix
            efi_obj.sandbox = efi_sandbox

            # certificado opcional
            if request.FILES.get("efi_certificate"):
                efi_obj.certificate = request.FILES["efi_certificate"]

            efi_obj.save()

        messages.success(request, "Dados da empresa salvos com sucesso.")
        # ⚠️ volta pra própria página
        return redirect("adminx_empresa")

    # GET
    efi = None
    if empresa:
        efi = EfiConfig.objects.filter(empresa=empresa).first()

    context = {
        "empresa": empresa,
        "efi": efi,
    }
    return render(request, "rifas/admin/empresa_form.html", context)


def _gen_token() -> str:
    return secrets.token_hex(6)


# ============================================================
# CUPONS
# ============================================================
def adminx_coupons_list(request):
    coupons = Coupon.objects.select_related("rifa").order_by("-created_at")
    return render(request, "rifas/admin/coupons_list.html", {
        "coupons": coupons,
    })


def adminx_coupons_edit(request, pk=None):
    if pk:
        coupon = get_object_or_404(Coupon, pk=pk)
    else:
        coupon = None

    if request.method == "POST":
        data = request.POST
        codigo = (data.get("codigo") or "").strip()
        if not codigo:
            messages.error(request, "Informe o código do cupom.")
            return redirect(request.path)

        rifa_id = data.get("rifa") or None
        rifa = Rifa.objects.filter(pk=rifa_id).first() if rifa_id else None

        attrs = {
            "codigo": codigo,
            "tipo": data.get("tipo") or Coupon.PERCENTUAL,
            "valor": data.get("valor") or 0,
            "max_uso_global": data.get("max_uso_global") or None,
            "max_uso_por_cliente": data.get("max_uso_por_cliente") or None,
            "min_compra": data.get("min_compra") or 0,
            "qtd_min_numeros": data.get("qtd_min_numeros") or None,
            "so_primeira_compra": bool(data.get("so_primeira_compra")),
            "rifa": rifa,
            "acumulavel": bool(data.get("acumulavel")),
            "ativo": bool(data.get("ativo")),
        }

        if coupon is None:
            Coupon.objects.create(**attrs)
        else:
            for k, v in attrs.items():
                setattr(coupon, k, v)
            coupon.save()

        messages.success(request, "Cupom salvo.")
        return redirect("adminx_coupons_list")

    rifas = Rifa.objects.order_by("-created_at")
    return render(request, "rifas/admin/coupons_form.html", {
        "coupon": coupon,
        "rifas": rifas,
    })


# ============================================================
# REGRAS DE DESCONTO
# ============================================================
def adminx_discount_rules_list(request):
    rules = DiscountRule.objects.select_related("rifa").order_by("-created_at")
    return render(request, "rifas/admin/discount_rules_list.html", {
        "rules": rules,
    })


def adminx_discount_rules_edit(request, pk=None):
    if pk:
        rule = get_object_or_404(DiscountRule, pk=pk)
    else:
        rule = None

    if request.method == "POST":
        data = request.POST
        nome = (data.get("nome") or "").strip()
        if not nome:
            messages.error(request, "Informe o nome da regra.")
            return redirect(request.path)

        rifa_id = data.get("rifa") or None
        rifa = Rifa.objects.filter(pk=rifa_id).first() if rifa_id else None

        attrs = {
            "nome": nome,
            "rifa": rifa,
            "qtd_numeros_min": data.get("qtd_numeros_min") or None,
            "inicio": data.get("inicio") or None,
            "fim": data.get("fim") or None,
            "tipo": data.get("tipo") or DiscountRule.PERCENTUAL,
            "valor": data.get("valor") or 0,
            "prioridade": data.get("prioridade") or 0,
            "exclusiva": bool(data.get("exclusiva")),
            "ativo": bool(data.get("ativo")),
        }

        if rule is None:
            DiscountRule.objects.create(**attrs)
        else:
            for k, v in attrs.items():
                setattr(rule, k, v)
            rule.save()

        messages.success(request, "Regra de desconto salva.")
        return redirect("adminx_discount_rules_list")

    rifas = Rifa.objects.order_by("-created_at")
    return render(request, "rifas/admin/discount_rules_form.html", {
        "rule": rule,
        "rifas": rifas,
    })


# ============================================================
# AFILIADOS
# ============================================================
def adminx_affiliates_list(request):
    affiliates = Affiliate.objects.order_by("-created_at")
    return render(request, "rifas/admin/affiliates_list.html", {
        "affiliates": affiliates,
    })


def adminx_affiliates_edit(request, pk=None):
    if pk:
        affiliate = get_object_or_404(Affiliate, pk=pk)
    else:
        affiliate = None

    if request.method == "POST":
        data = request.POST
        nome = (data.get("nome") or "").strip()
        email = (data.get("email") or "").strip()

        if not nome or not email:
            messages.error(request, "Nome e e-mail são obrigatórios.")
            return redirect(request.path)

        attrs = {
            "nome": nome,
            "email": email,
            "telefone": data.get("telefone") or "",
            "documento": data.get("documento") or "",
            "status": data.get("status") or "ativo",
            "pix_chave": data.get("pix_chave") or "",
            "banco_agencia_conta": data.get("banco_agencia_conta") or "",
        }

        if affiliate is None:
            Affiliate.objects.create(**attrs)
        else:
            for k, v in attrs.items():
                setattr(affiliate, k, v)
            affiliate.save()

        messages.success(request, "Afiliado salvo.")
        return redirect("adminx_affiliates_list")

    return render(request, "rifas/admin/affiliates_form.html", {
        "affiliate": affiliate,
    })


# ============================================================
# PROGRAMAS DE AFILIAÇÃO
# ============================================================
def adminx_affiliate_programs_list(request):
    programs = (
        AffiliateProgram.objects
        .select_related("rifa")
        .order_by("-id")
    )
    return render(request, "rifas/admin/affiliate_programs_list.html", {
        "programs": programs,
    })


def adminx_affiliate_programs_edit(request, pk=None):
    if pk:
        program = get_object_or_404(AffiliateProgram, pk=pk)
    else:
        program = None

    if request.method == "POST":
        data = request.POST
        rifa_id = data.get("rifa") or None
        rifa = Rifa.objects.filter(pk=rifa_id).first() if rifa_id else None

        attrs = {
            "rifa": rifa,
            "modelo_comissao": data.get("modelo_comissao") or AffiliateProgram.PERC_VENDA,
            "valor_comissao": data.get("valor_comissao") or 0,
            "cookie_days": data.get("cookie_days") or 7,
            "atribuicao": data.get("atribuicao") or "last_click",
            "permitir_compra_propria": bool(data.get("permitir_compra_propria")),
            "ativo": bool(data.get("ativo")),
        }

        if program is None:
            AffiliateProgram.objects.create(**attrs)
        else:
            for k, v in attrs.items():
                setattr(program, k, v)
            program.save()

        messages.success(request, "Programa de afiliação salvo.")
        return redirect("adminx_affiliate_programs_list")

    rifas = Rifa.objects.order_by("-created_at")
    return render(request, "rifas/admin/affiliate_programs_form.html", {
        "program": program,
        "rifas": rifas,
    })


# ============================================================
# LINKS DE AFILIADO
# ============================================================
def adminx_affiliate_links_list(request):
    links = (
        AffiliateLink.objects
        .select_related("affiliate", "program", "program__rifa")
        .order_by("-id")
    )
    return render(request, "rifas/admin/affiliate_links_list.html", {
        "links": links,
    })


def adminx_affiliate_links_edit(request, pk=None):
    if pk:
        link = get_object_or_404(AffiliateLink, pk=pk)
    else:
        link = None

    if request.method == "POST":
        data = request.POST
        aff_id = data.get("affiliate")
        prog_id = data.get("program")

        affiliate = get_object_or_404(Affiliate, pk=aff_id)
        program = get_object_or_404(AffiliateProgram, pk=prog_id)

        token = data.get("token") or _gen_token()
        url_destino = data.get("url_destino") or ""

        if link is None:
            AffiliateLink.objects.create(
                affiliate=affiliate,
                program=program,
                token=token,
                url_destino=url_destino,
            )
        else:
            link.affiliate = affiliate
            link.program = program
            link.token = token
            link.url_destino = url_destino
            link.save()

        messages.success(request, "Link de afiliado salvo.")
        return redirect("adminx_affiliate_links_list")

    affiliates = Affiliate.objects.order_by("nome")
    programs = AffiliateProgram.objects.order_by("-id")
    return render(request, "rifas/admin/affiliate_links_form.html", {
        "link": link,
        "affiliates": affiliates,
        "programs": programs,
    })


# ============================================================
# CLIQUES
# ============================================================
def adminx_affiliate_clicks_list(request):
    clicks = (
        AffiliateClick.objects
        .select_related("link", "link__affiliate", "link__program", "link__program__rifa")
        .order_by("-clicked_at")[:200]
    )
    return render(request, "rifas/admin/affiliate_clicks_list.html", {
        "clicks": clicks,
    })


# ============================================================
# COMISSÕES
# ============================================================
def adminx_commissions_list(request):
    commissions = (
        Commission.objects
        .select_related("affiliate", "pedido", "pedido__rifa")
        .order_by("-criado_em")
    )
    return render(request, "rifas/admin/commissions_list.html", {
        "commissions": commissions,
    })


def adminx_commission_approve(request, pk):
    com = get_object_or_404(Commission, pk=pk)
    com.status = Commission.APROVADA
    com.motivo_negacao = ""
    com.save(update_fields=["status", "motivo_negacao"])
    messages.success(request, "Comissão aprovada.")
    return redirect("adminx_commissions_list")


def adminx_commission_pay(request, pk):
    com = get_object_or_404(Commission, pk=pk)
    com.status = Commission.PAGA
    com.save(update_fields=["status"])
    messages.success(request, "Comissão marcada como paga.")
    return redirect("adminx_commissions_list")


# ============================================================
# PAYOUTS
# ============================================================
def adminx_payouts_list(request):
    payouts = (
        Payout.objects
        .select_related("affiliate")
        .order_by("-criado_em")
    )
    return render(request, "rifas/admin/payouts_list.html", {
        "payouts": payouts,
    })


def adminx_payouts_edit(request, pk=None):
    if pk:
        payout = get_object_or_404(Payout, pk=pk)
    else:
        payout = None

    if request.method == "POST":
        data = request.POST
        aff_id = data.get("affiliate")
        affiliate = get_object_or_404(Affiliate, pk=aff_id)

        valor_total = data.get("valor_total") or 0
        status = data.get("status") or Payout.EM_PROC
        comprovante_url = data.get("comprovante_url") or ""

        if payout is None:
            payout = Payout.objects.create(
                affiliate=affiliate,
                valor_total=valor_total,
                status=status,
                comprovante_url=comprovante_url,
                pago_em=timezone.now() if status == Payout.PAGO else None,
            )
        else:
            payout.affiliate = affiliate
            payout.valor_total = valor_total
            payout.status = status
            payout.comprovante_url = comprovante_url
            payout.pago_em = timezone.now() if status == Payout.PAGO else None
            payout.save()

        messages.success(request, "Payout salvo.")
        return redirect("adminx_payouts_list")

    affiliates = Affiliate.objects.order_by("nome")
    return render(request, "rifas/admin/payouts_form.html", {
        "payout": payout,
        "affiliates": affiliates,
    })

@user_passes_test(_is_staff, login_url="adminx_login")
def financeiro_geral_view(request: HttpRequest):
    """
    Painel financeiro geral do sistema.
    - Vendas por rifa (só pedidos PAGO)
    - Resumo vindo do RifaFinanceiro (custos, taxas, premiações)
    - Comissões de afiliados
    - Payouts já pagos
    """
    # filtros
    dt_ini = request.GET.get("ini", "").strip()
    dt_fim = request.GET.get("fim", "").strip()

    filtros_pedido = Q(status=Pedido.PAGO)
    if dt_ini:
        try:
            ini = datetime.strptime(dt_ini, "%Y-%m-%d")
            ini = timezone.make_aware(datetime.combine(ini, datetime.min.time()))
            filtros_pedido &= Q(pago_em__gte=ini)
        except Exception:
            pass
    if dt_fim:
        try:
            fim = datetime.strptime(dt_fim, "%Y-%m-%d")
            fim = timezone.make_aware(datetime.combine(fim, datetime.max.time()))
            filtros_pedido &= Q(pago_em__lte=fim)
        except Exception:
            pass

    rifas = Rifa.objects.order_by("-created_at")

    total_bruto_geral = Decimal("0.00")
    total_taxas_geral = Decimal("0.00")
    total_custos_geral = Decimal("0.00")
    total_premiacoes_geral = Decimal("0.00")
    total_liquido_geral = Decimal("0.00")

    rifas_rows = []

    for r in rifas:
        # 1) total vendido dessa rifa (pedidos pagos)
        pedidos_rifa = (
            Pedido.objects
            .filter(filtros_pedido, rifa=r)
            .select_related("cliente")
        )
        total_vendido = pedidos_rifa.aggregate(t=Sum("total"))["t"] or Decimal("0.00")

        # 2) pega financeiro 1:1 (se existir)
        financeiro = RifaFinanceiro.objects.filter(rifa=r).first()
        resumo = None
        taxa_total = Decimal("0.00")
        custo_premio = Decimal("0.00")
        outras_desp = Decimal("0.00")
        liquido = None

        if financeiro:
            resumo = financeiro.calcular_resumo()
            # o método já deve te dar: total_vendido, valor_taxa_percentual, valor_taxa_fixa, lucro_liquido, etc.
            taxa_total = (resumo.get("valor_taxa_percentual") or Decimal("0")) + (
                resumo.get("valor_taxa_fixa") or Decimal("0")
            )
            custo_premio = financeiro.custo_premio or Decimal("0")
            outras_desp = financeiro.outras_despesas or Decimal("0")
            liquido = resumo.get("lucro_liquido")

        # 3) premiações dessa rifa (tabela RifaPremiacao)
        premiacoes = RifaPremiacao.objects.filter(rifa=r)
        total_premiacoes_rifa = (
            premiacoes.aggregate(t=Sum("valor"))["t"] or Decimal("0.00")
        )

        # acumula no geral
        total_bruto_geral += total_vendido
        total_taxas_geral += taxa_total
        total_custos_geral += (custo_premio + outras_desp)
        total_premiacoes_geral += total_premiacoes_rifa

        # se não veio lucro_liquido do modelo, calcula um básico
        if liquido is None:
            liquido = total_vendido - taxa_total - custo_premio - outras_desp - total_premiacoes_rifa

        total_liquido_geral += liquido

        rifas_rows.append({
            "rifa": r,
            "total_vendido": total_vendido,
            "taxas": taxa_total,
            "custo_premio": custo_premio,
            "outras_despesas": outras_desp,
            "premiacoes": total_premiacoes_rifa,
            "liquido": liquido,
        })

    # ================================
    # AFILIAÇÃO (geral)
    # ================================
    # comissões geradas
    commissions_qs = Commission.objects.all()
    payouts_qs = Payout.objects.all()

    if dt_ini:
        commissions_qs = commissions_qs.filter(criado_em__date__gte=dt_ini)
        payouts_qs = payouts_qs.filter(criado_em__date__gte=dt_ini)
    if dt_fim:
        commissions_qs = commissions_qs.filter(criado_em__date__lte=dt_fim)
        payouts_qs = payouts_qs.filter(criado_em__date__lte=dt_fim)

    total_comissoes_geradas = commissions_qs.aggregate(t=Sum("valor"))["t"] or Decimal("0.00")
    total_comissoes_aprovadas = commissions_qs.filter(
        status=Commission.APROVADA
    ).aggregate(t=Sum("valor"))["t"] or Decimal("0.00")
    total_comissoes_pagas = commissions_qs.filter(
        status=Commission.PAGA
    ).aggregate(t=Sum("valor"))["t"] or Decimal("0.00")

    total_payouts = payouts_qs.filter(
        Q(status="pago") | Q(status=Payout.PAGO)
    ).aggregate(t=Sum("valor_total"))["t"] or Decimal("0.00")

    # saldo a pagar = comissões pagas - payouts efetivamente pagos
    saldo_afiliados = total_comissoes_pagas - total_payouts

    ctx = {
        "rifas_rows": rifas_rows,
        "total_bruto_geral": total_bruto_geral,
        "total_taxas_geral": total_taxas_geral,
        "total_custos_geral": total_custos_geral,
        "total_premiacoes_geral": total_premiacoes_geral,
        "total_liquido_geral": total_liquido_geral,

        # afiliados
        "total_comissoes_geradas": total_comissoes_geradas,
        "total_comissoes_aprovadas": total_comissoes_aprovadas,
        "total_comissoes_pagas": total_comissoes_pagas,
        "total_payouts": total_payouts,
        "saldo_afiliados": saldo_afiliados,

        # filtros
        "dt_ini": dt_ini,
        "dt_fim": dt_fim,
    }
    return render(request, "rifas/admin/financeiro_geral.html", ctx)

def _staff_or_403(request):
    if not request.user.is_authenticated:
        # deixa o @login_required cuidar
        return None
    if not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Você não tem permissão para acessar este painel.")
    return None


@require_POST
@login_required(login_url="adminx_login")
def registrar_webhook_efi_view(request, pk):
    """
    Registra o webhook na Efí para uma EfiConfig específica.
    Usa o método config.register_webhook().
    Mostra mensagem de sucesso/erro e VOLTA pra tela de empresa,
    que é onde está o modal.
    """
    # garante que é staff/adminx
    guard = _staff_or_403(request)
    if guard:
        return guard

    # pega só config ativa
    config = get_object_or_404(EfiConfig, pk=pk, ativo=True)

    try:
        resp = config.register_webhook()
    except Exception as e:
        # erro de rede / SSL / conexão
        messages.error(
            request,
            f"Erro ao registrar webhook na Efí: {e}"
        )
        # volta pra EMPRESA (onde está o modal)
        return redirect("adminx_empresa")

    # Normalizar resposta
    status_code = None
    text = ""
    webhook_url = None
    ok_flag = None

    # 1) se o método já devolve dict (como no teu management command)
    if isinstance(resp, dict):
        status_code = resp.get("status_code")
        # pega mais texto pra mostrar no modal
        text = (resp.get("text") or "")[:800]
        webhook_url = resp.get("webhook")
        ok_flag = resp.get("ok")
    else:
        # 2) pode ser um objeto Response do requests
        try:
            import requests  # noqa
            if isinstance(resp, requests.Response):
                status_code = resp.status_code
                try:
                    text = resp.text[:800]
                except Exception:
                    text = ""
                # tenta pegar json
                try:
                    data = resp.json()
                    webhook_url = data.get("webhook") or data.get("url")
                except Exception:
                    pass
        except ImportError:
            # sem requests instalado
            text = str(resp)[:800]

    # Decidir se deu certo
    if ok_flag is True or (status_code and 200 <= status_code < 300):
        msg = "Webhook registrado na Efí com sucesso."
        if webhook_url:
            msg += f" URL: {webhook_url}"
        messages.success(request, msg)
    else:
        err = "Falha ao registrar webhook na Efí."
        if status_code:
            err += f" Status: {status_code}."
        if text:
            err += f" Detalhe: {text}"
        messages.error(request, err)

    # 👇 sempre volta pra tela da empresa (onde está o modal)
    return redirect("adminx_empresa")