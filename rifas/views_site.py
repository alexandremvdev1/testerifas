# rifas/views_site.py
from __future__ import annotations

import json
from decimal import Decimal

from django.db import transaction, models
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.http import (
    JsonResponse,
    HttpRequest,
    HttpResponseBadRequest,
    HttpResponse,
)
from django.views.decorators.http import require_GET, require_POST

from .models import (
    Rifa,
    Pedido,
    Numero,
)

# tentar importar Cliente / Pagamento / CotaPremiada (nem todo projeto tem)
try:
    from .models import Cliente, Pagamento, CotaPremiada
except Exception:
    Cliente = None
    Pagamento = None
    CotaPremiada = None

# vamos reutilizar helpers do api_public
try:
    from .api_public import (
        _liberar_expirados as _liberar_expirados_public,
        _efi_disponivel,
        _criar_cobranca_efi_e_salvar,
    )
except Exception:
    _liberar_expirados_public = None
    _efi_disponivel = None
    _criar_cobranca_efi_e_salvar = None

# helper que você usava em alguns lugares
try:
    from .utils import gera_protocolo, cpf_normalize
except Exception:
    def gera_protocolo() -> str:
        return timezone.now().strftime("R%Y%m%d%H%M%S%f")[:20]

    def cpf_normalize(cpf: str) -> str:
        return "".join(ch for ch in (cpf or "") if ch.isdigit())


# ---------------------------------------------------------------------
# helpers de CPF
# ---------------------------------------------------------------------
def _cpf_only_digits(cpf: str) -> str:
    return "".join(ch for ch in (cpf or "") if ch.isdigit())


def _cpf_mask(cpf_digits: str) -> str:
    """Transforma '04272258176' em '042.722.581-76'."""
    d = _cpf_only_digits(cpf_digits)
    if len(d) != 11:
        return cpf_digits
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"


# ---------------------------------------------------------------------
# função interna para liberar reservas SÓ dessa rifa
# ---------------------------------------------------------------------
def _liberar_reservas_da_rifa(rifa: Rifa) -> int:
    """
    Libera reservas vencidas SOMENTE dessa rifa,
    usando Numero.expirou() que já lê rifa.minutos_expiracao_reserva.
    Também expira pedidos pendentes da mesma rifa que ficaram sem número.
    Retorna quantos números foram liberados.
    """
    # se estiver disponível o helper do api_public, usa ele
    if _liberar_expirados_public is not None:
        _liberar_expirados_public(rifa)
        return 0

    # caso contrário, faz local
    reservados = (
        Numero.objects
        .select_related("rifa", "pedido")
        .filter(rifa=rifa, status=Numero.RESERVADO)
    )

    expirados_ids: list[int] = []
    for n in reservados:
        if n.expirou():
            expirados_ids.append(n.id)

    if not expirados_ids:
        return 0

    with transaction.atomic():
        Numero.objects.filter(id__in=expirados_ids).update(
            status=Numero.LIVRE,
            cliente=None,
            reservado_em=None,
            pedido=None,
        )

        Pedido.objects.filter(
            rifa=rifa,
            status=Pedido.PENDENTE,
            numeros__isnull=True,
        ).update(status=Pedido.EXPIRADO)

    return len(expirados_ids)


# ---------------------------------------------------------------------
# páginas públicas simples
# ---------------------------------------------------------------------
def home(request: HttpRequest):
    agora = timezone.now()
    rifas = (
        Rifa.objects
        .filter(ativo=True, inicio_vendas__lte=agora, fim_vendas__gte=agora)
        .order_by("-created_at")
    )
    return render(request, "rifas/site/home.html", {"rifas": rifas})


def rifa_detail(request: HttpRequest, slug: str):
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)
    _liberar_reservas_da_rifa(rifa)

    pagos = Numero.objects.filter(rifa=rifa, status=Numero.PAGO).count()
    reservados = Numero.objects.filter(rifa=rifa, status=Numero.RESERVADO).count()
    livres = Numero.objects.filter(rifa=rifa, status=Numero.LIVRE).count()
    total_numeros = int(rifa.quantidade_numeros or 0)

    ctx = {
        "rifa": rifa,
        "pagos": pagos,
        "reservados": reservados,
        "livres": livres,
        "total_numeros": total_numeros,
        "numeros_lista": list(range(1, total_numeros + 1)),
    }
    return render(request, "rifas/site/rifa_detail.html", ctx)


def rifa_public_view(request: HttpRequest, slug: str):
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)

    _liberar_reservas_da_rifa(rifa)

    pagos = Numero.objects.filter(rifa=rifa, status=Numero.PAGO).count()
    reservados = Numero.objects.filter(rifa=rifa, status=Numero.RESERVADO).count()
    livres = Numero.objects.filter(rifa=rifa, status=Numero.LIVRE).count()
    total_numeros = int(rifa.quantidade_numeros or 0)

    top_compradores = []
    if getattr(rifa, "mostrar_top_compradores", False):
        top_qs = (
            Numero.objects
            .filter(rifa=rifa, cliente__isnull=False)
            .values("cliente__nome", "cliente__cpf")
            .annotate(qtd=models.Count("id"))
            .order_by("-qtd", "cliente__nome")[:5]
        )
        for item in top_qs:
            nome = item["cliente__nome"] or "Cliente"
            primeiro_nome = nome.strip().split(" ")[0].upper()
            top_compradores.append(
                {
                    "nome": nome,
                    "primeiro_nome": primeiro_nome,
                    "cpf": item["cliente__cpf"] or "",
                    "qtd": item["qtd"],
                }
            )

    cotas_premiadas = []
    if CotaPremiada is not None:
        cotas_premiadas = list(
            CotaPremiada.objects
            .filter(rifa=rifa, ativo=True)
            .order_by("numero")
            .values("numero", "descricao", "valor_premio")
        )
    elif hasattr(rifa, "cotas_premiadas") and rifa.cotas_premiadas:
        cotas_premiadas = rifa.cotas_premiadas

    ctx = {
        "rifa": rifa,
        "pagos": pagos,
        "reservados": reservados,
        "livres": livres,
        "total_numeros": total_numeros,
        "mostrar_top_compradores": getattr(rifa, "mostrar_top_compradores", False),
        "top_compradores": top_compradores,
        "cotas_premiadas": cotas_premiadas,
    }
    return render(request, "rifas/site/rifa_public.html", ctx)


# ---------------------------------------------------------------------
# checkout usado pelo JS (confere CPF)
# ---------------------------------------------------------------------
def checkout(request: HttpRequest, slug: str):
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)
    raw_cpf = (request.GET.get("cpf") or "").strip()
    wants_json = request.GET.get("json") == "1"

    if wants_json:
        found = False
        cliente_data = {}

        if raw_cpf and Cliente is not None:
            only = _cpf_only_digits(raw_cpf)
            masked = _cpf_mask(only)

            cli = (
                Cliente.objects
                .filter(cpf__in=[raw_cpf, only, masked])
                .first()
            )
            if cli:
                found = True
                cliente_data = {
                    "nome": cli.nome,
                    "telefone": getattr(cli, "telefone", ""),
                    "email": getattr(cli, "email", ""),
                    "cpf": cli.cpf,
                }

        return JsonResponse(
            {
                "exists": found,
                "cliente": cliente_data,
                "rifa": rifa.titulo,
                "slug": rifa.slug,
            }
        )

    return render(request, "rifas/site/checkout.html", {"rifa": rifa})


# ---------------------------------------------------------------------
# página/status do pedido
# ---------------------------------------------------------------------
def pedido_status(request: HttpRequest, protocolo: str):
    pedido = get_object_or_404(
        Pedido.objects.select_related("cliente", "rifa"),
        protocolo=protocolo,
    )
    _liberar_reservas_da_rifa(pedido.rifa)
    return render(request, "rifas/site/pedido_status.html", {"pedido": pedido})


def pedido_status_json(request: HttpRequest, protocolo: str):
    pedido = get_object_or_404(
        Pedido.objects.select_related("cliente", "rifa"),
        protocolo=protocolo,
    )
    nums = list(pedido.numeros.order_by("numero").values_list("numero", flat=True))
    return JsonResponse(
        {
            "protocolo": pedido.protocolo,
            "rifa": pedido.rifa.titulo,
            "status": pedido.status,
            "total": f"{pedido.total:.2f}",
            "numeros": nums,
        }
    )


# =====================================================================
# APIs que o template JS chama
# =====================================================================

@require_GET
def api_rifa_grade(request: HttpRequest, slug: str):
    """
    Endpoint que o front chama toda hora.
    Agora:
      - limpa reservas vencidas SÓ dessa rifa
      - expira pedidos pendentes que ficaram sem número
      - devolve a grade marcando cotas premiadas
    """
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)

    # 👇 aqui é o ponto que faltava
    _liberar_reservas_da_rifa(rifa)

    # ---------------------------
    # cotas premiadas dessa rifa
    # ---------------------------
    cotas_map = set()
    if CotaPremiada is not None:
        cotas_map = set(
            CotaPremiada.objects
            .filter(rifa=rifa, ativo=True)
            .values_list("numero", flat=True)
        )
    elif hasattr(rifa, "cotas_premiadas") and rifa.cotas_premiadas:
        for item in rifa.cotas_premiadas:
            try:
                cotas_map.add(int(item.get("numero")))
            except Exception:
                pass

    # ---------------------------
    # monta a grade
    # ---------------------------
    numeros = (
        Numero.objects
        .filter(rifa=rifa)
        .values("numero", "status")
        .order_by("numero")
    )

    data = []
    for item in numeros:
        n = item["numero"]
        data.append(
            {
                "numero": n,
                "status": item["status"],
                "cota_premiada": n in cotas_map,
            }
        )

    return JsonResponse({"numeros": data})


def _liberar_reservas_da_rifa(rifa: Rifa):
    """
    Libera somente as reservas EXPIRADAS dessa rifa
    e marca como EXPIRADO os pedidos pendentes que ficaram sem número.
    Isso é exatamente o que você falou: "número libera mas pedido continua pendente".
    """
    minutos = rifa.minutos_expiracao_reserva or 0
    if minutos <= 0:
        # se a rifa nem tem tempo de expiração configurado, sai
        return

    agora = timezone.now()

    # pega só os reservados dessa rifa
    reservados = (
        Numero.objects
        .select_related("pedido")
        .filter(rifa=rifa, status=Numero.RESERVADO)
    )

    expirados_ids = []
    pedidos_afetados = set()

    for num in reservados:
        if not num.reservado_em:
            continue

        if (agora - num.reservado_em).total_seconds() > (minutos * 60):
            # venceu
            expirados_ids.append(num.id)
            if num.pedido_id:
                pedidos_afetados.add(num.pedido_id)

    if not expirados_ids:
        # nada venceu
        return

    with transaction.atomic():
        # 1) soltar os números
        Numero.objects.filter(id__in=expirados_ids).update(
            status=Numero.LIVRE,
            cliente=None,
            pedido=None,
            reservado_em=None,
        )

        # 2) para cada pedido que perdeu número, se ficou sem NENHUM número -> expira
        for pid in pedidos_afetados:
            try:
                ped = Pedido.objects.get(id=pid)
            except Pedido.DoesNotExist:
                continue

            # se ainda tá pendente e não tem mais número, vira expirado
            if ped.status == Pedido.PENDENTE and ped.numeros.count() == 0:
                ped.status = Pedido.EXPIRADO
                ped.save(update_fields=["status"])

        # 3) garantia extra:
        #    se por algum outro lugar um pedido ficou pendente e sem número, expira também
        (
            Pedido.objects
            .filter(rifa=rifa, status=Pedido.PENDENTE)
            .annotate(qtd=models.Count("numeros"))
            .filter(qtd=0)
            .update(status=Pedido.EXPIRADO)
        )


@require_GET
def api_rifa_meus_numeros(request: HttpRequest, slug: str):
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)
    raw_cpf = (request.GET.get("cpf") or "").strip()
    if not raw_cpf:
        return JsonResponse({"ok": False, "error": "CPF obrigatório"}, status=400)

    cpf_only = _cpf_only_digits(raw_cpf)
    cpf_masked = _cpf_mask(cpf_only)

    _liberar_reservas_da_rifa(rifa)

    qs = (
        Numero.objects
        .filter(
            rifa=rifa,
            cliente__isnull=False,
            cliente__cpf__in=[raw_cpf, cpf_only, cpf_masked],
            status__in=[Numero.RESERVADO, Numero.PAGO],
        )
        .select_related("pedido")
        .order_by("numero")
    )

    resultados = []
    for n in qs:
        resultados.append(
            {
                "numero": n.numero,
                "status": n.status,
                "protocolo": n.pedido.protocolo if n.pedido else "",
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "rifa": rifa.titulo,
            "cpf": cpf_masked,
            "total": len(resultados),
            "numeros": resultados,
        }
    )


# ---------------------------------------------------------------------
# helper pra gerar protocolo (fallback)
# ---------------------------------------------------------------------
def _gerar_protocolo() -> str:
    return timezone.now().strftime("R%Y%m%d%H%M%S%f")[:20]


# ---------------------------------------------------------------------
# criação de pedido via JS público
# ---------------------------------------------------------------------
@require_POST
@transaction.atomic
def api_pedido_create(request: HttpRequest):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("JSON inválido")

    slug = payload.get("slug")
    cpf_raw = (payload.get("cpf") or "").strip()
    nome = (payload.get("nome") or "").strip()
    telefone = (payload.get("telefone") or "").strip()
    numeros_req = payload.get("numeros") or []

    if not slug or not cpf_raw or not numeros_req:
        return JsonResponse(
            {"error": "slug, cpf e numeros são obrigatórios"},
            status=400,
        )

    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)

    _liberar_reservas_da_rifa(rifa)

    if Cliente is None:
        return JsonResponse({"error": "Modelo Cliente não disponível"}, status=500)

    cpf_only = _cpf_only_digits(cpf_raw)
    cpf_masked = _cpf_mask(cpf_only)
    cli = (
        Cliente.objects
        .filter(cpf__in=[cpf_raw, cpf_only, cpf_masked])
        .first()
    )
    if not cli:
        cli = Cliente.objects.create(
            nome=nome or "Cliente",
            email=f"{cpf_only}@fake.local",
            telefone=telefone,
            cpf=cpf_masked,
        )
    else:
        changed = False
        if nome and not cli.nome:
            cli.nome = nome
            changed = True
        if telefone and not cli.telefone:
            cli.telefone = telefone
            changed = True
        if changed:
            cli.save()

    numeros_db = list(
        Numero.objects
        .select_for_update()
        .filter(rifa=rifa, numero__in=numeros_req)
    )

    if len(numeros_db) != len(set(numeros_req)):
        return JsonResponse({"error": "Alguns números não existem"}, status=400)

    for n in numeros_db:
        if n.status != Numero.LIVRE:
            return JsonResponse({"error": f"Número {n.numero} não está disponível"}, status=400)

    preco_unit = rifa.preco_numero or Decimal("0.00")
    subtotal = preco_unit * Decimal(len(numeros_db))

    pedido = Pedido.objects.create(
        rifa=rifa,
        cliente=cli,
        protocolo=_gerar_protocolo(),
        subtotal=subtotal,
        desconto_regras=Decimal("0.00"),
        desconto_cupom=Decimal("0.00"),
        total=subtotal,
        status=Pedido.PENDENTE,
        pricing_breakdown={
            "qtd": len(numeros_db),
            "preco_unit": str(preco_unit),
        },
    )

    now = timezone.now()
    for n in numeros_db:
        n.status = Numero.RESERVADO
        n.cliente = cli
        n.reservado_em = now
        n.pedido = pedido
        n.save(update_fields=["status", "cliente", "reservado_em", "pedido"])

    payment_url = None
    pix_qr = None
    pix_code = None

    pg = None
    if Pagamento is not None:
        pg = Pagamento.objects.create(
            pedido=pedido,
            provider="efi",
            status_provider="pending",
        )

    # tenta gerar PIX real na EFI
    pix_data = None
    if _efi_disponivel is not None and _efi_disponivel(rifa) and _criar_cobranca_efi_e_salvar is not None:
        pix_data = _criar_cobranca_efi_e_salvar(pedido, pg, rifa, subtotal)

    if pix_data and pix_data.get("ok"):
        pix_qr = pix_data.get("qr_code_base64")
        pix_code = pix_data.get("copia_cola")
    else:
        # fallback: se tiver registry
        try:
            from .payments.registry import current as pay
            pref = pay.create_preference(pedido)
            pix_code = getattr(pref, "copia_cola", None)
            pix_qr = getattr(pref, "qr_code_base64", None)
            if pg:
                pg.provider = "registry"
                pg.copia_cola = pix_code
                pg.qr_code_base64 = pix_qr
                pg.status_provider = "pending"
                pg.save()
        except Exception:
            pass

    return JsonResponse(
        {
            "ok": True,
            "protocolo": pedido.protocolo,
            "payment_url": payment_url,
            "pix_qr": pix_qr,
            "pix_code": pix_code,
        }
    )


def checkout_rapido(request: HttpRequest, slug: str) -> HttpResponse:
    rifa = get_object_or_404(Rifa, slug=slug, ativo=True)

    if request.method == "GET":
        return render(request, "rifas/site/checkout_rapido.html", {"rifa": rifa})

    cpf = (request.POST.get("cpf") or "").strip()
    nome = (request.POST.get("nome") or "").strip()
    telefone = (request.POST.get("telefone") or "").strip()
    nums_raw = request.POST.get("numeros") or ""
    if not nums_raw:
        nums_list = request.POST.getlist("numeros")
    else:
        nums_list = [x.strip() for x in nums_raw.split(",") if x.strip()]

    if not cpf or not nums_list:
        return render(request, "rifas/site/checkout_rapido.html", {
            "rifa": rifa,
            "erro": "Informe CPF e selecione pelo menos 1 número.",
        })

    cpf_clean = cpf_normalize(cpf)

    cliente, _ = Cliente.objects.get_or_create(
        cpf=cpf_clean,
        defaults={
            "nome": nome or "Cliente",
            "telefone": telefone or "-",
            "email": f"{cpf_clean}@fake.local",
        },
    )
    changed = False
    if nome and cliente.nome != nome:
        cliente.nome = nome
        changed = True
    if telefone and cliente.telefone != telefone:
        cliente.telefone = telefone
        changed = True
    if changed:
        cliente.save()

    with transaction.atomic():
        pedido = Pedido.objects.create(
            rifa=rifa,
            cliente=cliente,
            protocolo=gera_protocolo(),
            status=Pedido.PENDENTE,
        )

        qtd = 0
        now = timezone.now()

        mins = rifa.minutos_expiracao_reserva or 15
        limite = now - timezone.timedelta(minutes=mins)
        Numero.objects.filter(
            rifa=rifa,
            status=Numero.RESERVADO,
            reservado_em__lt=limite,
        ).update(
            status=Numero.LIVRE,
            cliente=None,
            pedido=None,
            reservado_em=None,
        )

        for n in nums_list:
            n_int = int(n)
            num_obj, _ = Numero.objects.select_for_update().get_or_create(
                rifa=rifa,
                numero=n_int,
                defaults={
                    "status": Numero.RESERVADO,
                    "cliente": cliente,
                    "pedido": pedido,
                    "reservado_em": now,
                },
            )
            if num_obj.status in (Numero.PAGO, Numero.RESERVADO) and num_obj.pedido_id != pedido.id:
                transaction.set_rollback(True)
                return render(request, "rifas/site/checkout_rapido.html", {
                    "rifa": rifa,
                    "erro": f"Número {n_int} não está disponível.",
                })

            num_obj.status = Numero.RESERVADO
            num_obj.cliente = cliente
            num_obj.pedido = pedido
            num_obj.reservado_em = now
            num_obj.save()
            qtd += 1

        total = (rifa.preco_numero or Decimal("0.00")) * Decimal(qtd)
        pedido.total = total
        pedido.subtotal = total
        pedido.save()

        pagamento = None
        if Pagamento is not None:
            pagamento = Pagamento.objects.create(
                pedido=pedido,
                provider="efi",
                status_provider="pending",
            )

        pix_data = None
        if _efi_disponivel is not None and _efi_disponivel(rifa) and _criar_cobranca_efi_e_salvar is not None:
            pix_data = _criar_cobranca_efi_e_salvar(
                pedido,
                pagamento,
                rifa,
                total,
            )

        if not pix_data:
            return render(request, "rifas/site/checkout_rapido.html", {
                "rifa": rifa,
                "erro": "Não foi possível gerar o PIX. Tente novamente.",
            })

        if pagamento is not None:
            pagamento.copia_cola = pix_data.get("copia_cola")
            pagamento.qr_code_base64 = pix_data.get("qr_code_base64")
            pagamento.provider_preference_id = pix_data.get("txid")
            pagamento.save()

    numeros_pedido = list(pedido.numeros.order_by("numero").values_list("numero", flat=True))

    return render(request, "rifas/site/pagamento_pix.html", {
        "rifa": rifa,
        "pedido": pedido,
        "cliente": cliente,
        "numeros": numeros_pedido,
        "copia_cola": pix_data.get("copia_cola"),
        "qr_code_base64": pix_data.get("qr_code_base64"),
    })
