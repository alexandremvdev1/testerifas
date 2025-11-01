# rifas/efi.py
from __future__ import annotations
from decimal import Decimal
import requests
from django.utils import timezone

from .models import EfiCredential

def efi_disponivel() -> bool:
    return EfiCredential is not None and EfiCredential.objects.filter(ativo=True).exists()

def efi_get_token(cred: "EfiCredential") -> dict | None:
    try:
        url = cred.auth_url or "https://api.efipay.com.br/v1/authorize"
        payload = {
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
            "grant_type": "client_credentials",
        }
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code in (200, 201):
            return r.json()
    except Exception:
        return None
    return None

def efi_criar_pix(pedido, total: Decimal, expiracao_seg: int = 900) -> dict | None:
    """
    Cria uma cobrança PIX na EFI e devolve:
    {
      "copia_cola": "...",
      "qr_code_base64": "...",
      "txid": "..."
    }
    """
    cred = EfiCredential.objects.filter(ativo=True).order_by("-id").first()
    if not cred:
        return None

    token_data = efi_get_token(cred)
    if not token_data or "access_token" not in token_data:
        return None

    access_token = token_data["access_token"]
    cob_url = cred.pix_url or "https://api.efipay.com.br/v2/cob"

    valor_str = f"{Decimal(total):.2f}".replace(",", ".")

    body = {
        "calendario": {"expiracao": expiracao_seg},
        "valor": {"original": valor_str},
        "chave": cred.pix_key,
        "solicitacaoPagador": f"Rifa {pedido.rifa.titulo} ({pedido.protocolo})",
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(cob_url, headers=headers, json=body, timeout=15)
    except Exception:
        return None

    if r.status_code not in (200, 201):
        return None

    data = r.json()
    loc_id = data.get("loc", {}).get("id")
    txid = data.get("txid")

    copia_cola = None
    qr_code_base64 = None

    # pega o QRCode
    if loc_id:
        qrcode_url = f"https://api.efipay.com.br/v2/loc/{loc_id}/qrcode"
        try:
            rqr = requests.get(qrcode_url, headers=headers, timeout=15)
            if rqr.status_code in (200, 201):
                qrd = rqr.json()
                copia_cola = qrd.get("qrcode") or qrd.get("pixCopiaECola")
                qr_code_base64 = qrd.get("imagemQrcode")
        except Exception:
            pass

    # fallback
    if not copia_cola:
        copia_cola = data.get("pixCopiaECola")

    if not copia_cola:
        return None

    return {
        "copia_cola": copia_cola,
        "qr_code_base64": qr_code_base64,
        "txid": txid,
    }
