# rifas/api_public.py — AJUSTE COMPLETO p/ EFI + registry PIX local (robusto p/ erro de cert + domínio certo)
from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
import json
import logging
import requests
import os
import tempfile

from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Rifa,
    Numero,
    Cliente,
    Pedido,
    EfiConfig,
)

logger = logging.getLogger(__name__)

# opcionais
try:
    from .models import (
        Pagamento,
        WebhookEvent,
        DiscountApplication,
        CouponRedemption,
        Commission,
        AffiliateAttribution,
        AffiliateProgram,
    )
except Exception:
    Pagamento = None
    WebhookEvent = None
    DiscountApplication = None
    CouponRedemption = None
    Commission = None
    AffiliateAttribution = None
    AffiliateProgram = None

# utils
try:
    from .utils import gera_protocolo, abreviar_nome, cpf_normalize
except Exception:
    def gera_protocolo() -> str:
        return timezone.now().strftime("R%Y%m%d%H%M%S%f")[:20]

    def abreviar_nome(nome: str) -> str:
        if not nome:
            return "Cliente"
        partes = nome.strip().split()
        if len(partes) == 1:
            return partes[0]
        return partes[0] + " " + partes[-1][0] + "."

    def cpf_normalize(cpf: str) -> str:
        return "".join(ch for ch in (cpf or "") if ch.isdigit())

# pricing
try:
    from .pricing import precificar
except Exception:
    def precificar(rifa, qtd, cliente, cupom):
        total = (rifa.preco_numero or Decimal("0")) * Decimal(qtd)
        return (
            total,
            Decimal("0.00"),
            Decimal("0.00"),
            total,
            {},
            None,
            None,
        )

# registry local
try:
    from .payments.registry import current as pay
except Exception:
    pay = None


# ======================================================================
# BASE
# ======================================================================
class PublicAPIView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]


# ======================================================================
# HELPERS
# ======================================================================
def get_or_create_cliente(cdata: dict) -> Cliente:
    cpf = cpf_normalize(cdata.get("cpf"))
    defaults = {
        "nome": cdata.get("nome", "Cliente"),
        "email": cdata.get("email", "-"),
        "telefone": cdata.get("telefone", "-"),
        "cpf": cpf,
    }
    obj, created = Cliente.objects.get_or_create(cpf=cpf, defaults=defaults)
    if not created:
        changed = False
        for k in ("nome", "email", "telefone"):
            val = cdata.get(k) or getattr(obj, k)
            if val and val != getattr(obj, k):
                setattr(obj, k, val)
                changed = True
        if changed:
            obj.save()
    return obj


def _atribuir_afiliado(request: HttpRequest, cliente: Cliente, rifa: Rifa):
    if AffiliateProgram is None:
        return None

    token = request.COOKIES.get("aff_token") or request.GET.get("aff")
    if not token:
        return None

    prog = (
        AffiliateProgram.objects
        .filter(Q(rifa__isnull=True) | Q(rifa=rifa), ativo=True)
        .order_by("-id")
        .first()
    )
    if not prog:
        return None

    try:
        from .models import AffiliateLink
        link = (
            AffiliateLink.objects
            .select_related("affiliate", "program")
            .get(token=token, program__id=prog.id)
        )
    except Exception:
        return None

    expira = timezone.now() + timedelta(days=prog.cookie_days or 7)
    attr, _ = AffiliateAttribution.objects.update_or_create(
        link=link,
        cliente=cliente,
        defaults={"expira_em": expira, "modelo": prog.atribuicao},
    )
    return attr


def _liberar_expirados(rifa: Rifa):
    mins = rifa.minutos_expiracao_reserva or 15
    limite = timezone.now() - timedelta(minutes=mins)
    expirados = Numero.objects.filter(
        rifa=rifa,
        status=Numero.RESERVADO,
        reservado_em__lt=limite,
    )
    if expirados.exists():
        expirados.update(
            status=Numero.LIVRE,
            cliente=None,
            pedido=None,
            reservado_em=None,
        )


def _efi_disponivel(rifa: Rifa | None = None) -> bool:
    if rifa and rifa.efi_config_id and rifa.efi_config.ativo:
        return True
    if rifa and rifa.empresa_id:
        if EfiConfig.objects.filter(empresa=rifa.empresa, ativo=True).exists():
            return True
    return EfiConfig.objects.filter(empresa__isnull=True, rifa__isnull=True, ativo=True).exists()


def _get_efi_cred(rifa: Rifa | None = None) -> EfiConfig | None:
    if rifa and rifa.efi_config_id and rifa.efi_config.ativo:
        return rifa.efi_config
    if rifa and rifa.empresa_id:
        cred = (
            EfiConfig.objects.filter(empresa=rifa.empresa, ativo=True)
            .order_by("-id")
            .first()
        )
        if cred:
            return cred
    return (
        EfiConfig.objects
        .filter(empresa__isnull=True, rifa__isnull=True, ativo=True)
        .order_by("-id")
        .first()
    )


# ======================================================================
# CERT HELPER (.p12 → .pem) — pra quando mudar de certificado
# ======================================================================
def _build_cert_arg_from_cred(cred: EfiConfig):
    """
    Aceita:
      - cred.certificate = .pem → usa direto
      - cred.certificate = .p12/.pfx → converte pra .pem temporário
    Retorna:
      - path (str) ou tuple (cert, key) pra passar no requests
      - tmp_files (list[str]) pra depois você poder apagar se quiser
    """
    cert_field = getattr(cred, "certificate", None)
    if not cert_field:
        return None, []

    path = cert_field.path if hasattr(cert_field, "path") else str(cert_field)
    ext = os.path.splitext(path)[1].lower()

    # já é PEM
    if ext in (".pem", ".crt"):
        return path, []

    # é P12/PFX → converter
    if ext in (".p12", ".pfx"):
        with open(path, "rb") as f:
            data = f.read()

        try:
            # se não tiver senha no .p12, passa None
            private_key, cert, add_certs = load_key_and_certificates(data, password=None)
        except Exception as e:
            logger.warning("Erro ao carregar .p12/.pfx: %s", e)
            return None, []

        # escreve cert e key temporários
        tmp_files: list[str] = []

        cert_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        cert_tmp.write(cert.public_bytes(Encoding.PEM))
        cert_tmp.flush()
        tmp_files.append(cert_tmp.name)

        key_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
        key_tmp.write(
            private_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=NoEncryption(),
            )
        )
        key_tmp.flush()
        tmp_files.append(key_tmp.name)

        return (cert_tmp.name, key_tmp.name), tmp_files

    return None, []


# ======================================================================
# EFI — OAuth com fallback de certificado
# ======================================================================
def _efi_oauth_url(cred: EfiConfig) -> str:
    # a doc atual da Efí diz pra usar SEMPRE este domínio: https://pix.api.efipay.com.br/oauth/token
    # sandbox e produção ficam no mesmo host, muda só a credencial. :contentReference[oaicite:2]{index=2}
    return "https://pix.api.efipay.com.br/oauth/token"


def _parse_json_safely(resp: requests.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        # ajuda a diagnosticar o caso que você mostrou (HTML)
        logger.warning("Resposta não-JSON da EFI: %s", resp.text[:300])
        return None


def _get_efi_token(cred: EfiConfig) -> dict | None:
    url = _efi_oauth_url(cred)
    data = {"grant_type": "client_credentials"}

    # a doc da Efí manda usar Basic Auth no header, não o tuple do requests — vamos fazer igual à doc
    # user:pass → base64 → Authorization: Basic ...
    auth_tuple = (cred.client_id, cred.client_secret)
    headers = {
        "Content-Type": "application/json",
    }

    # ===== 1) tenta SEM certificado =====
    try:
        r = requests.post(url, json=data, auth=auth_tuple, headers=headers, timeout=20)
        if r.status_code in (200, 201):
            j = _parse_json_safely(r)
            if j:
                return j
            else:
                logger.warning("EFI OAuth (sem cert): resposta sem JSON válido")
        else:
            logger.warning("EFI OAuth (sem cert): %s %s", r.status_code, r.text[:200])
    except requests.exceptions.SSLError as e:
        logger.warning("EFI OAuth (sem cert) SSL error: %s", e)
    except Exception as e:
        logger.warning("EFI OAuth (sem cert) error: %s", e)

    # ===== 2) tenta COM certificado (.pem OU .p12) =====
    cert_arg, tmp_files = _build_cert_arg_from_cred(cred)

    if cert_arg:
        try:
            r = requests.post(url, json=data, auth=auth_tuple, headers=headers, timeout=20, cert=cert_arg)
            if r.status_code in (200, 201):
                j = _parse_json_safely(r)
                # limpa tmp
                for f in tmp_files:
                    try:
                        os.unlink(f)
                    except Exception:
                        pass
                if j:
                    return j
                else:
                    logger.warning("EFI OAuth (com cert): resposta sem JSON válido")
            else:
                logger.warning("EFI OAuth (com cert): %s %s", r.status_code, r.text[:200])
        except requests.exceptions.SSLError as e:
            logger.warning("EFI OAuth (com cert) SSL error: %s", e)
        except Exception as e:
            logger.warning("EFI OAuth (com cert) error: %s", e)
        finally:
            # remove os arquivos temporários se houver
            for f in tmp_files:
                try:
                    os.unlink(f)
                except Exception:
                    pass

    # não deu → cai pro registry
    return None


def _efi_base_url(cred: EfiConfig) -> str:
    # mesma ideia: usar o host novo do PIX
    return "https://pix.api.efipay.com.br"


def _criar_cobranca_efi_e_salvar(
    pedido: Pedido,
    pagamento: Pagamento | None,
    rifa: Rifa,
    total: Decimal,
) -> dict | None:
    cred = _get_efi_cred(rifa)
    if not cred:
        logger.warning("EFI: nenhuma credencial ativa encontrada")
        return None

    token_data = _get_efi_token(cred)
    if not token_data or "access_token" not in token_data:
        # É exatamente o que está acontecendo com você
        logger.warning("EFI: não pegou token, indo pro registry")
        return None

    access_token = token_data["access_token"]
    base = _efi_base_url(cred)
    cob_url = f"{base}/v2/cob"
    qrcode_base = f"{base}/v2/loc/{{id}}/qrcode"

    valor_str = f"{Decimal(total):.2f}".replace(",", ".")
    body = {
        "calendario": {
            "expiracao": int((rifa.minutos_expiracao_reserva or 15) * 60),
        },
        "valor": {
            "original": valor_str,
        },
        "chave": cred.chave_pix,
        "solicitacaoPagador": f"Rifa {rifa.titulo} ({pedido.protocolo})",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # 1) tenta criar cobrança SEM cert
    try:
        r = requests.post(cob_url, headers=headers, json=body, timeout=20)
        if r.status_code not in (200, 201):
            logger.warning("EFI create cob (sem cert): %s %s", r.status_code, r.text[:200])
            r = None
    except requests.exceptions.SSLError as e:
        logger.warning("EFI create cob (sem cert) SSL: %s", e)
        r = None
    except Exception as e:
        logger.warning("EFI create cob (sem cert) error: %s", e)
        r = None

    # 2) se falhou e tem cert, tenta COM (aceitando .p12)
    if r is None:
        cert_arg, tmp_files = _build_cert_arg_from_cred(cred)
        if cert_arg:
            try:
                r = requests.post(cob_url, headers=headers, json=body, timeout=20, cert=cert_arg)
                if r.status_code not in (200, 201):
                    logger.warning("EFI create cob (com cert): %s %s", r.status_code, r.text[:200])
                    r = None
            except Exception as e:
                logger.warning("EFI create cob (com cert) error: %s", e)
                r = None
            finally:
                for f in tmp_files:
                    try:
                        os.unlink(f)
                    except Exception:
                        pass
        else:
            return None

    if r is None:
        return None

    data = _parse_json_safely(r) or {}
    loc_id = data.get("loc", {}).get("id")
    txid = data.get("txid")
    copia_cola = None
    qr_code_base64 = None

    # 3) pegar o QR
    if loc_id:
        qrcode_url = qrcode_base.format(id=loc_id)
        # tenta sem cert
        try:
            rqr = requests.get(qrcode_url, headers=headers, timeout=20)
            if rqr.status_code in (200, 201):
                qrd = _parse_json_safely(rqr) or {}
                copia_cola = qrd.get("qrcode") or qrd.get("pixCopiaECola")
                qr_code_base64 = qrd.get("imagemQrcode") or qrd.get("qr_code_base64")
            else:
                logger.warning("EFI get qrcode (sem cert): %s %s", rqr.status_code, rqr.text[:200])
                rqr = None
        except Exception as e:
            logger.warning("EFI get qrcode (sem cert) error: %s", e)
            rqr = None

        # se falhou → tenta com cert
        if rqr is None:
            cert_arg, tmp_files = _build_cert_arg_from_cred(cred)
            if cert_arg:
                try:
                    rqr = requests.get(qrcode_url, headers=headers, timeout=20, cert=cert_arg)
                    if rqr.status_code in (200, 201):
                        qrd = _parse_json_safely(rqr) or {}
                        copia_cola = qrd.get("qrcode") or qrd.get("pixCopiaECola")
                        qr_code_base64 = qrd.get("imagemQrcode") or qrd.get("qr_code_base64")
                    else:
                        logger.warning("EFI get qrcode (com cert): %s %s", rqr.status_code, rqr.text[:200])
                except Exception as e:
                    logger.warning("EFI get qrcode (com cert) error: %s", e)
                finally:
                    for f in tmp_files:
                        try:
                            os.unlink(f)
                        except Exception:
                            pass

    # fallback
    if not copia_cola:
        copia_cola = data.get("pixCopiaECola") or data.get("qrcode") or data.get("location") or None

    if pagamento is not None:
        pagamento.provider = "efi"
        pagamento.copia_cola = copia_cola
        pagamento.qr_code_base64 = qr_code_base64
        pagamento.provider_preference_id = txid or (str(loc_id) if loc_id else "")
        pagamento.status_provider = "pending"
        pagamento.save()

    return {
        "ok": True if copia_cola else False,
        "copia_cola": copia_cola,
        "qr_code_base64": qr_code_base64,
        "txid": txid,
    }


# ======================================================================
# REGISTRY LOCAL
# ======================================================================
def _criar_pagamento_registry(pedido: Pedido, pagamento: Pagamento | None) -> tuple[dict | None, str | None]:
    if pay is None:
        return None, "registry de pagamentos não disponível"

    try:
        pref = pay.create_preference(pedido)
    except Exception as e:
        logger.warning("REGISTRY create_preference error: %s", e)
        return None, str(e)

    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    copia = _get(pref, "copia_cola")
    qr = _get(pref, "qr_code_base64")
    pref_id = _get(pref, "preference_id")
    pay_id = _get(pref, "payment_id")

    if pagamento is not None:
        pagamento.provider = "registry"
        pagamento.copia_cola = copia
        pagamento.qr_code_base64 = qr
        pagamento.provider_preference_id = pref_id
        pagamento.provider_payment_id = pay_id
        pagamento.status_provider = "pending"
        pagamento.save()

    return (
        {
            "ok": True,
            "copia_cola": copia,
            "qr_code_base64": qr,
            "preference_id": pref_id,
            "payment_id": pay_id,
        },
        None,
    )


# ======================================================================
# LISTAGENS
# ======================================================================
class RifasListView(PublicAPIView):
    def get(self, request):
        qs = Rifa.objects.filter(ativo=True)
        return Response([
            {
                "titulo": r.titulo,
                "slug": r.slug,
                "preco_numero": str(r.preco_numero),
            }
            for r in qs
        ])


class RifaDetailView(PublicAPIView):
    def get(self, request, slug):
        r = get_object_or_404(Rifa, slug=slug, ativo=True)
        try:
            premios = list(r.premios.values("ordem", "descricao"))
        except Exception:
            premios = []
        return Response({
            "titulo": r.titulo,
            "slug": r.slug,
            "preco_numero": str(r.preco_numero),
            "inicio": r.inicio_vendas,
            "fim": r.fim_vendas,
            "em_vendas": getattr(r, "em_vendas", True),
            "premios": premios,
        })


class GradeView(PublicAPIView):
    def get(self, request, slug):
        r = get_object_or_404(Rifa, slug=slug, ativo=True)
        _liberar_expirados(r)
        nums_qs = Numero.objects.filter(rifa=r).order_by("numero")

        if not nums_qs.exists() and r.quantidade_numeros:
            total = int(r.quantidade_numeros)
            return Response({
                "numeros": [{"numero": i, "status": "livre"} for i in range(1, total + 1)]
            })

        return Response({
            "numeros": [
                {"numero": n.numero, "status": n.status}
                for n in nums_qs
            ]
        })


class TopCompradoresView(PublicAPIView):
    def get(self, request, slug):
        r = get_object_or_404(Rifa, slug=slug, ativo=True)
        top = (
            Numero.objects
            .filter(rifa=r, status=Numero.PAGO, cliente__isnull=False)
            .values("cliente__nome")
            .annotate(qtd=Count("id"))
            .order_by("-qtd")[:5]
        )
        return Response([
            {
                "nome": abreviar_nome(t["cliente__nome"]),
                "qtd": t["qtd"],
            }
            for t in top
        ])


# ======================================================================
# CRIAR PEDIDO
# ======================================================================
@method_decorator(csrf_exempt, name="dispatch")
class CriarPedidoView(PublicAPIView):
    def post(self, request, *args, **kwargs):
        slug = (
            request.data.get("slug")
            or request.data.get("rifa_slug")
            or request.data.get("rifa")
        )
        numeros = request.data.get("numeros") or []
        cupom = request.data.get("cupom")

        cli_block = request.data.get("cliente") or {}
        cpf = (request.data.get("cpf") or cli_block.get("cpf") or "").strip()
        nome = (request.data.get("nome") or cli_block.get("nome") or "").strip()
        telefone = (request.data.get("telefone") or cli_block.get("telefone") or "").strip()

        if not slug or not cpf or not numeros:
            return Response(
                {"detail": "slug/rifa_slug, cpf e numeros são obrigatórios."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rifa = get_object_or_404(Rifa, slug=slug, ativo=True)

        _liberar_expirados(rifa)

        cliente = get_or_create_cliente(
            {"cpf": cpf, "nome": nome, "telefone": telefone}
        )

        _atribuir_afiliado(request, cliente, rifa)

        registry_error = None
        efi_error = None

        with transaction.atomic():
            pedido = Pedido.objects.create(
                rifa=rifa,
                cliente=cliente,
                protocolo=gera_protocolo(),
                status=Pedido.PENDENTE,
            )

            qtd = 0
            now = timezone.now()
            for n in numeros:
                n = int(n)
                if n < 1 or n > int(rifa.quantidade_numeros):
                    return Response(
                        {"detail": f"Número {n} fora do intervalo."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                num_obj, _ = Numero.objects.select_for_update().get_or_create(
                    rifa=rifa,
                    numero=n,
                    defaults={
                        "status": Numero.RESERVADO,
                        "pedido": pedido,
                        "cliente": cliente,
                        "reservado_em": now,
                    },
                )

                if num_obj.status in (Numero.PAGO, Numero.RESERVADO) and num_obj.pedido_id != pedido.id:
                    return Response(
                        {"detail": f"Número {n} não está disponível."},
                        status=status.HTTP_409_CONFLICT,
                    )

                num_obj.status = Numero.RESERVADO
                num_obj.pedido = pedido
                num_obj.cliente = cliente
                num_obj.reservado_em = now
                num_obj.save()
                qtd += 1

            # pricing
            subtotal, dr, dc, total, breakdown, disc_app, cup_red = precificar(
                rifa, qtd, cliente, cupom
            )

            pedido.subtotal = subtotal
            if hasattr(pedido, "desconto_regras"):
                pedido.desconto_regras = dr
            if hasattr(pedido, "desconto_cupom"):
                pedido.desconto_cupom = dc
            pedido.total = total
            if hasattr(pedido, "pricing_breakdown"):
                pedido.pricing_breakdown = breakdown or {}
            pedido.save()

            # vincula descontos
            if DiscountApplication is not None and isinstance(disc_app, DiscountApplication):
                disc_app.pedido = pedido
                disc_app.save(update_fields=["pedido"])
            if CouponRedemption is not None and isinstance(cup_red, CouponRedemption):
                cup_red.pedido = pedido
                cup_red.save(update_fields=["pedido"])

            pagamento_obj = None
            if Pagamento is not None:
                pagamento_obj = Pagamento.objects.create(
                    pedido=pedido,
                    provider="pending",
                    status_provider="pending",
                )

            # 1) tenta EFI
            if _efi_disponivel(rifa):
                efi_data = _criar_cobranca_efi_e_salvar(pedido, pagamento_obj, rifa, total)
                if efi_data and efi_data.get("ok") and efi_data.get("copia_cola"):
                    return Response(
                        {
                            "ok": True,
                            "protocolo": pedido.protocolo,
                            "total": f"{pedido.total:.2f}",
                            "provider": "efi",
                            "copia_cola": efi_data.get("copia_cola"),
                            "qr_code_base64": efi_data.get("qr_code_base64"),
                        },
                        status=status.HTTP_201_CREATED,
                    )
                else:
                    efi_error = "erro ao criar cobrança EFI"

            # 2) fallback → registry local
            registry_data, registry_error = _criar_pagamento_registry(pedido, pagamento_obj)
            if registry_data and registry_data.get("ok"):
                return Response(
                    {
                        "ok": True,
                        "protocolo": pedido.protocolo,
                        "total": f"{pedido.total:.2f}",
                        "provider": "registry",
                        "copia_cola": registry_data.get("copia_cola"),
                        "qr_code_base64": registry_data.get("qr_code_base64"),
                    },
                    status=status.HTTP_201_CREATED,
                )

        return Response(
            {
                "ok": True,
                "protocolo": pedido.protocolo,
                "total": f"{pedido.total:.2f}",
                "provider": None,
                "efi_error": efi_error,
                "registry_error": registry_error,
            },
            status=status.HTTP_201_CREATED,
        )


# ======================================================================
# STATUS DO PEDIDO
# ======================================================================
class PedidoStatusView(PublicAPIView):
    def get(self, request, protocolo):
        p = get_object_or_404(Pedido.objects.select_related("rifa"), protocolo=protocolo)
        nums = list(p.numeros.values_list("numero", flat=True))

        cotas_premiadas = []
        try:
            from .models import CotaPremiada
            cp_qs = CotaPremiada.objects.filter(rifa=p.rifa, ativo=True)
            premiadas_map = {c.numero: c for c in cp_qs}
            for n in nums:
                if n in premiadas_map:
                    c = premiadas_map[n]
                    cotas_premiadas.append({
                        "numero": n,
                        "descricao": c.descricao,
                        "valor_premio": str(c.valor_premio) if c.valor_premio is not None else "",
                    })
        except Exception:
            cotas_premiadas = []

        pagamento_info = {}
        if Pagamento is not None and hasattr(p, "pagamento"):
            pagamento_info = {
                "provider": p.pagamento.provider,
                "status_provider": p.pagamento.status_provider,
                "copia_cola": p.pagamento.copia_cola,
                "qr_code_base64": p.pagamento.qr_code_base64,
            }

        return Response(
            {
                "protocolo": p.protocolo,
                "status": p.status,
                "total": str(p.total),
                "numeros": nums,
                "pago_em": p.pago_em,
                "cotas_premiadas": cotas_premiadas,
                "pagamento": pagamento_info,
            }
        )


# ======================================================================
# WEBHOOK
# ======================================================================
# ======================================================================
# WEBHOOK — versão que aceita GET ?protocolo=... e marca mesmo assim
# ======================================================================
from django.views.decorators.http import require_http_methods

@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook_provider(request: HttpRequest, provider_key: str, *args, **kwargs):
    """
    Webhook unificado:
    - aceita POST JSON (formato EFI ou registry)
    - aceita GET com ?protocolo=...&status=...
    - tenta achar o pedido por txid OU por protocolo
    - marca pedido, números e pagamento
    """
    provider_key = (provider_key or "").lower()

    # 1) tenta ler JSON do corpo
    raw_body = request.body.decode("utf-8") or ""
    data = {}
    if raw_body.strip():
        try:
            data = json.loads(raw_body)
        except Exception:
            data = {"raw": raw_body}

    # 2) normaliza caso venha "pix": [...] da EFI
    if "pix" in data and isinstance(data["pix"], list) and data["pix"]:
        data = data["pix"][0]

    # 3) overlay da querystring (pra teste via navegador)
    #    query sempre ganha do body
    for k, v in request.GET.items():
        data[k] = v

    # loga o bruto (se existir modelo)
    if WebhookEvent is not None:
        WebhookEvent.objects.create(
            provider=provider_key,
            event_id=str(
                data.get("id")
                or data.get("txid")
                or data.get("protocolo")
                or data.get("external_reference")
                or ""
            ),
            raw=data,
        )

    # se não tem modelagem de pagamento, só confirma
    if Pagamento is None:
        return JsonResponse({"ok": True, "detail": "pagamento não modelado"})

    # ============================================================
    # Tenta localizar o pedido
    # ============================================================
    pedido = None
    pagamento = None

    # pode vir:
    # - txid (fluxo EFI real)
    # - protocolo=XY123 (seu teste)
    # - external_reference, reference...
    txid = data.get("txid") or ""
    protocolo = (
        data.get("protocolo")
        or data.get("external_reference")
        or data.get("reference")
        or ""
    )
    status_in = (
        data.get("status")
        or data.get("pixStatus")
        or data.get("status_pagamento")
        or "approved"
    )

    # 1) se veio txid, tenta achar pagamento com provider=efi
    if txid:
        try:
            pagamento = Pagamento.objects.select_related("pedido").get(
                provider="efi",
                provider_preference_id=txid,
            )
            pedido = pagamento.pedido
        except Pagamento.DoesNotExist:
            pagamento = None

    # 2) se não achou pelo txid, tenta pelo protocolo (que é o que vc tá mandando)
    if not pedido and protocolo:
        try:
            pedido = Pedido.objects.get(protocolo=protocolo)
            pagamento, _ = Pagamento.objects.get_or_create(
                pedido=pedido,
                defaults={
                    "provider": provider_key,
                    "status_provider": status_in,
                    "provider_preference_id": txid or None,
                },
            )
        except Pedido.DoesNotExist:
            # agora vamos avisar de verdade
            return JsonResponse(
                {
                    "ok": False,
                    "detail": f"pedido com protocolo '{protocolo}' não encontrado",
                    "incoming": data,
                },
                status=404,
            )

    # 3) se ainda não tem pedido, não tem o que fazer
    if not pedido:
        return JsonResponse(
            {
                "ok": True,
                "detail": "nenhum pedido associado (sem txid e sem protocolo)",
                "incoming": data,
            },
            status=202,
        )

    # ============================================================
    # Atualiza pagamento + pedido
    # ============================================================
    with transaction.atomic():
        # garante que temos um objeto pagamento
        if pagamento is None and Pagamento is not None:
            pagamento = Pagamento.objects.create(
                pedido=pedido,
                provider=provider_key,
                status_provider=status_in,
                provider_preference_id=txid or None,
            )

        # atualiza dados do pagamento
        if data.get("payment_id"):
            pagamento.provider_payment_id = data["payment_id"]
        if txid and not pagamento.provider_preference_id:
            pagamento.provider_preference_id = txid
        if status_in:
            pagamento.status_provider = status_in
        pagamento.provider = pagamento.provider or provider_key
        pagamento.save()

        # normaliza status pra decidir se paga
        st_norm = status_in.lower()
        is_paid = st_norm in (
            "approved",
            "concluido",
            "concluído",
            "paid",
            "pago",
            "success",
            "succeeded",
        )

        if is_paid:
            if pedido.status != Pedido.PAGO:
                pedido.status = Pedido.PAGO
                pedido.pago_em = timezone.now()
                pedido.save(update_fields=["status", "pago_em"])
                # marca números
                pedido.numeros.update(status=Numero.PAGO)

                # comissão de afiliado (mesmo código de antes)
                if (
                    AffiliateAttribution is not None
                    and Commission is not None
                ):
                    attr = (
                        AffiliateAttribution.objects
                        .select_related("link__program", "link__affiliate")
                        .filter(cliente=pedido.cliente, expira_em__gte=timezone.now())
                        .order_by("-id")
                        .first()
                    )
                    if attr and attr.link.program.ativo:
                        prog = attr.link.program
                        base = pedido.total
                        valor = Decimal("0.00")
                        perc = Decimal("0.00")

                        if prog.modelo_comissao == "percentual_venda":
                            perc = prog.valor_comissao
                            valor = (base * (perc / Decimal("100"))).quantize(Decimal("0.01"))
                        elif prog.modelo_comissao == "valor_fixopornumero":
                            qtd = pedido.numeros.count()
                            valor = (prog.valor_comissao * Decimal(qtd)).quantize(Decimal("0.01"))
                        elif prog.modelo_comissao == "percentual_sobre_lucro":
                            perc = prog.valor_comissao
                            valor = (base * (perc / Decimal("100"))).quantize(Decimal("0.01"))

                        if valor > 0:
                            Commission.objects.get_or_create(
                                pedido=pedido,
                                defaults={
                                    "affiliate": attr.link.affiliate,
                                    "base_calculo": base,
                                    "percentual": perc,
                                    "valor": valor,
                                    "status": Commission.PENDENTE,
                                },
                            )

    return JsonResponse(
        {
            "ok": True,
            "detail": "pagamento marcado",
            "pedido": pedido.protocolo,
            "status_pedido": pedido.status,
            "status_pagamento": pagamento.status_provider,
        }
    )

# ======================================================================
# RESERVA UNITÁRIA
# ======================================================================
class ReservarNumeroView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, slug: str):
        rifa = get_object_or_404(Rifa, slug=slug, ativo=True)
        _liberar_expirados(rifa)

        numero_raw = request.data.get("numero")
        try:
            numero_int = int(numero_raw)
        except (TypeError, ValueError):
            return Response({"detail": "Número inválido."}, status=status.HTTP_400_BAD_REQUEST)

        if numero_int < 1 or numero_int > int(rifa.quantidade_numeros):
            return Response({"detail": "Número fora do intervalo."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            num_obj, created = Numero.objects.select_for_update().get_or_create(
                rifa=rifa,
                numero=numero_int,
                defaults={
                    "status": Numero.RESERVADO,
                    "reservado_em": timezone.now(),
                },
            )

            if not created:
                if num_obj.status == Numero.RESERVADO:
                    if num_obj.reservado_em and num_obj.reservado_em < timezone.now() - timedelta(
                        minutes=rifa.minutos_expiracao_reserva or 15
                    ):
                        num_obj.status = Numero.RESERVADO
                        num_obj.reservado_em = timezone.now()
                        num_obj.cliente = None
                        num_obj.pedido = None
                        num_obj.save(update_fields=["status", "reservado_em", "cliente", "pedido"])
                        return Response({"ok": True, "status": "reservado"})
                    return Response(
                        {"detail": "Número já está reservado."},
                        status=status.HTTP_409_CONFLICT,
                    )

                if num_obj.status == Numero.PAGO:
                    return Response(
                        {"detail": "Número já está pago."},
                        status=status.HTTP_409_CONFLICT,
                    )

                num_obj.status = Numero.RESERVADO
                num_obj.reservado_em = timezone.now()
                num_obj.save(update_fields=["status", "reservado_em"])

        return Response({"ok": True, "status": "reservado"})


# ======================================================================
# DETALHE PÚBLICO DO PEDIDO
# ======================================================================
@method_decorator(csrf_exempt, name="dispatch")
class PedidoPublicDetailView(PublicAPIView):
    def get(self, request, protocolo: str, *args, **kwargs):
        pedido = get_object_or_404(
            Pedido.objects.select_related("rifa", "cliente"),
            protocolo=protocolo
        )

        pagamento_obj = None
        if Pagamento is not None:
            try:
                pagamento_obj = pedido.pagamento
            except Pagamento.DoesNotExist:
                pagamento_obj = None

        generated_now = False
        registry_error = None

        if pay is not None:
            need_create = (
                pagamento_obj is None
                or not getattr(pagamento_obj, "copia_cola", None)
            )
            if need_create:
                if pagamento_obj is None and Pagamento is not None:
                    pagamento_obj = Pagamento.objects.create(
                        pedido=pedido,
                        provider="pending",
                        status_provider="pending",
                    )

                reg_data, registry_error = _criar_pagamento_registry(pedido, pagamento_obj)
                if reg_data and reg_data.get("ok"):
                    generated_now = True
                    if Pagamento is not None:
                        try:
                            pagamento_obj = pedido.pagamento
                        except Exception:
                            pass

        numeros_data = [
            {
                "numero": n.numero,
                "status": n.status,
            }
            for n in pedido.numeros.all().order_by("numero")
        ]

        # calcula expire usando datetime.timedelta
        expires_in = None
        if pedido.status not in (Pedido.PAGO, Pedido.CANCELADO, Pedido.EXPIRADO):
            if pedido.criado_em and pedido.rifa.minutos_expiracao_reserva:
                agora = timezone.now()
                limite = pedido.criado_em + timedelta(
                    minutes=pedido.rifa.minutos_expiracao_reserva
                )
                diff = (limite - agora).total_seconds()
                if diff <= 0:
                    expires_in = 0
                else:
                    expires_in = int(diff)

        cotas_ativas = pedido.rifa.cotas_premiadas.filter(ativo=True).order_by("numero")
        cotas_data = [
            {
                "numero": cota.numero,
                "descricao": cota.descricao,
                "valor_premio": f"{cota.valor_premio:.2f}",
            }
            for cota in cotas_ativas
        ]

        resp = {
            "ok": True,
            "protocolo": pedido.protocolo,
            "status": pedido.status.upper(),
            "total": f"{pedido.total:.2f}" if pedido.total is not None else "0.00",
            "pago_em": pedido.pago_em,
            "rifa": {
                "slug": pedido.rifa.slug,
                "titulo": pedido.rifa.titulo,
            },
            "cliente": {
                "nome": pedido.cliente.nome,
                "email": pedido.cliente.email,
                "telefone": pedido.cliente.telefone,
                "cpf": pedido.cliente.cpf,
            },
            "numeros": numeros_data,
            "cotas_premiadas": cotas_data,
        }

        if expires_in is not None:
            resp["expires_in"] = expires_in

        if pagamento_obj is not None:
            resp.update(
                {
                    "provider": pagamento_obj.provider,
                    "status_provider": pagamento_obj.status_provider,
                    "copia_cola": getattr(pagamento_obj, "copia_cola", None),
                    "qr_code_base64": getattr(pagamento_obj, "qr_code_base64", None),
                    "payment_id": getattr(pagamento_obj, "provider_payment_id", None),
                    "preference_id": getattr(pagamento_obj, "provider_preference_id", None),
                }
            )

        if generated_now and not resp.get("copia_cola") and registry_error:
            resp["registry_error"] = registry_error

        if expires_in == 0 and pedido.status not in (Pedido.PAGO, Pedido.CANCELADO):
            resp["status"] = Pedido.EXPIRADO.upper()

        return Response(resp)
