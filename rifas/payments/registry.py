# rifas/payments/registry.py — somente EFI (com fallback local)
from __future__ import annotations

import base64
import io
import hashlib
import json

from dataclasses import dataclass

# se tiver qrcode instalado
import qrcode

# tenta pegar o modelo de credencial EFI, se existir
try:
    from rifas.models import EfiConfig
except Exception:
    EfiConfig = None

# se quiser usar requests direto daqui
try:
    import requests
except Exception:
    requests = None


@dataclass
class PreferenceOut:
    preference_id: str
    payment_id: str | None
    qr_code_base64: str | None
    copia_cola: str | None


def _local_pix_payload(pedido) -> str:
    chave = "chave-pix@exemplo.com"
    msg = f"PED-{pedido.protocolo}|VAL:{pedido.total}|RIFA:{pedido.rifa.slug}"
    copia = (
        f"0002012636PIXBR00014BR.GOV.BCB.PIX0114{chave}"
        f"520400005303986540{pedido.total:0>2}5802BR5920RIFAS ONLINE6009ARAGUAINA"
        f"6214{hashlib.md5(msg.encode()).hexdigest()}6304"
    )
    return copia


def _qr_base64_from_text(text: str) -> str:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _get_efi_token(cred: "EfiConfig") -> dict | None:
    if requests is None:
        return None

    url = "https://api.efipay.com.br/v1/authorize"
    payload = {
        "client_id": cred.client_id,
        "client_secret": cred.client_secret,
        "grant_type": "client_credentials",
    }
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code in (200, 201):
            return r.json()
    except Exception:
        return None
    return None


def _create_efi_charge(pedido) -> PreferenceOut:
    # 1) se não tiver modelo de credencial → cai direto pro local
    if EfiConfig is None:
        copia = _local_pix_payload(pedido)
        qr = _qr_base64_from_text(copia)
        return PreferenceOut(
            preference_id=f"efi_{pedido.protocolo}",
            payment_id=None,
            qr_code_base64=qr,
            copia_cola=copia,
        )

    # tenta pegar da rifa primeiro
    cred = None
    if getattr(pedido.rifa, "efi_config_id", None):
        cred = pedido.rifa.efi_config

    if cred is None and pedido.rifa.empresa_id:
        cred = (
            EfiConfig.objects
            .filter(empresa=pedido.rifa.empresa, ativo=True)
            .order_by("-id")
            .first()
        )

    if cred is None:
        cred = (
            EfiConfig.objects
            .filter(empresa__isnull=True, rifa__isnull=True, ativo=True)
            .order_by("-id")
            .first()
        )

    if not cred:
        copia = _local_pix_payload(pedido)
        qr = _qr_base64_from_text(copia)
        return PreferenceOut(
            preference_id=f"efi_{pedido.protocolo}",
            payment_id=None,
            qr_code_base64=qr,
            copia_cola=copia,
        )

    token_data = _get_efi_token(cred)
    if not token_data or "access_token" not in token_data or requests is None:
        copia = _local_pix_payload(pedido)
        qr = _qr_base64_from_text(copia)
        return PreferenceOut(
            preference_id=f"efi_{pedido.protocolo}",
            payment_id=None,
            qr_code_base64=qr,
            copia_cola=copia,
        )

    access_token = token_data["access_token"]

    valor_str = f"{pedido.total:.2f}".replace(",", ".")
    expiracao = int((getattr(pedido.rifa, "minutos_expiracao_reserva", 15) or 15) * 60)

    body = {
        "calendario": {"expiracao": expiracao},
        "valor": {"original": valor_str},
        "chave": cred.chave_pix,
        "solicitacaoPagador": f"Rifa {pedido.rifa.titulo} ({pedido.protocolo})",
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    pix_url = "https://api.efipay.com.br/v2/cob"

    try:
        r = requests.post(pix_url, json=body, headers=headers, timeout=15)
    except Exception:
        copia = _local_pix_payload(pedido)
        qr = _qr_base64_from_text(copia)
        return PreferenceOut(
            preference_id=f"efi_{pedido.protocolo}",
            payment_id=None,
            qr_code_base64=qr,
            copia_cola=copia,
        )

    if r.status_code not in (200, 201):
        copia = _local_pix_payload(pedido)
        qr = _qr_base64_from_text(copia)
        return PreferenceOut(
            preference_id=f"efi_{pedido.protocolo}",
            payment_id=None,
            qr_code_base64=qr,
            copia_cola=copia,
        )

    data = r.json()

    copia_cola = (
        data.get("pixCopiaECola")
        or data.get("location")
        or _local_pix_payload(pedido)
    )
    qr_code_base64 = (
        data.get("qr_code_base64")
        or data.get("qrcodeBase64")
    )
    if not qr_code_base64:
        qr_code_base64 = _qr_base64_from_text(copia_cola)

    txid = data.get("txid") or str(data.get("loc", {}).get("id", "")) or pedido.protocolo

    return PreferenceOut(
        preference_id=txid,
        payment_id=None,
        qr_code_base64=qr_code_base64,
        copia_cola=copia_cola,
    )


def _parse_efi_webhook(request) -> dict:
    try:
      payload = json.loads(request.body.decode() or "{}")
    except Exception:
      payload = {}

    status_efi = payload.get("status") or payload.get("pixStatus") or "approved"
    txid = payload.get("txid") or ""
    return {
        "event_id": txid or hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        "payment_status": status_efi,
        "preference_id": txid,
        "payment_id": None,
    }


class Provider:
    def __init__(self, key: str):
        self.key = key

    def create_preference(self, pedido):
        if self.key == "efi":
            return _create_efi_charge(pedido)
        raise NotImplementedError("Provider não suportado")

    def parse_webhook(self, request):
        if self.key == "efi":
            return _parse_efi_webhook(request)
        raise NotImplementedError("Provider não suportado")


current = Provider("efi")
