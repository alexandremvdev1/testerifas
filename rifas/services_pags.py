# rifas/services_pags.py
import base64
import json
import requests  # se você já usa a SDK da Efí, troca aqui
from django.utils import timezone
from decimal import Decimal

from .models import Pagamento, Pedido
from .utils_efi import get_efi_config_for_rifa


def criar_pagamento_pix_efi(pedido: Pedido) -> Pagamento:
    rifa = pedido.rifa
    cfg = get_efi_config_for_rifa(rifa)
    if not cfg:
        raise ValueError("Nenhuma configuração Efí encontrada para esta rifa.")

    # 1. pegar token OAuth da Efí
    # OBS: aqui depende da tua conta: some pedem certificado, outras não.
    # vou mostrar o padrão sem certificado, você adapta.
    auth_url = "https://api.efipay.com.br/oauth/token" if not cfg.sandbox else "https://api-sandbox.gerencianet.com.br/oauth/token"

    data = {
        "grant_type": "client_credentials"
    }
    auth = (cfg.client_id, cfg.client_secret)

    resp = requests.post(auth_url, data=data, auth=auth)
    resp.raise_for_status()
    access_token = resp.json()["access_token"]

    # 2. criar cobrança imediata
    pix_url = "https://api.efipay.com.br/v2/cob" if not cfg.sandbox else "https://api-pix.gerencianet.com.br/v2/cob"

    payload = {
        "calendario": {"expiracao": 3600},
        "devedor": {
            # se quiser usar os dados do cliente:
            # "cpf": pedido.cliente.cpf_sem_mascara,
            # "nome": pedido.cliente.nome,
        },
        "valor": {
            "original": f"{pedido.total:.2f}",
        },
        "chave": cfg.chave_pix,
        "solicitacaoPagador": f"Rifa {rifa.titulo} - {pedido.protocolo}",
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp2 = requests.post(pix_url, headers=headers, json=payload)
    resp2.raise_for_status()
    cob = resp2.json()
    txid = cob["txid"]
    loc_id = cob["loc"]["id"]

    # 3. pegar o QRCode
    qrcode_url = f"https://api.efipay.com.br/v2/loc/{loc_id}/qrcode" if not cfg.sandbox else f"https://api-pix.gerencianet.com.br/v2/loc/{loc_id}/qrcode"

    resp3 = requests.get(qrcode_url, headers=headers)
    resp3.raise_for_status()
    qrd = resp3.json()

    copia_cola = qrd["qrcode"]
    imagem_b64 = qrd.get("imagemQrcode")  # já vem base64 pronto

    pagamento, _ = Pagamento.objects.get_or_create(pedido=pedido)
    pagamento.provider = "efi"
    pagamento.efi_txid = txid
    pagamento.efi_loc_id = str(loc_id)
    pagamento.copia_cola = copia_cola
    pagamento.qr_code_base64 = imagem_b64
    pagamento.status_provider = "created"
    pagamento.save()

    return pagamento
