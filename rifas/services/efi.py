# rifas/services/efi.py
from __future__ import annotations

import tempfile

import requests
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

from django.utils import timezone

from rifas.models import Rifa, EfiConfig


class EfiError(Exception):
    """Erro genérico de integração com a Efí."""
    pass


def get_efi_config_for_rifa(rifa: Rifa) -> EfiConfig | None:
    """
    Decide qual EfiConfig usar para esta rifa.
    Ordem:
    1. a própria rifa tem uma credencial
    2. a empresa da rifa tem uma credencial
    3. uma credencial global (sem empresa e sem rifa)
    """
    # 1) credencial específica da rifa
    if rifa.efi_config_id:
        return rifa.efi_config

    # 2) credencial da empresa
    if rifa.empresa_id:
        cfg = (
            EfiConfig.objects
            .filter(empresa=rifa.empresa, ativo=True)
            .order_by("-criado_em")
            .first()
        )
        if cfg:
            return cfg

    # 3) credencial global
    cfg = (
        EfiConfig.objects
        .filter(empresa__isnull=True, rifa__isnull=True, ativo=True)
        .order_by("-criado_em")
        .first()
    )
    return cfg


def _p12_to_pem_tempfiles(p12_bytes: bytes) -> tuple[str, str]:
    """
    Recebe o conteúdo de um .p12/.pfx da Efí e devolve dois caminhos temporários:
    (caminho_do_cert.pem, caminho_da_key.pem)

    Fazemos assim pra não precisar converter manualmente toda vez que
    você trocar o certificado no admin: a cada request a gente lê o
    arquivo MAIS ATUAL do banco.
    """
    password = None  # se teu .p12 tiver senha, coloca aqui

    key, cert, extra = load_key_and_certificates(p12_bytes, password)

    cert_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")

    # escreve o cert
    cert_tmp.write(cert.public_bytes(Encoding.PEM))
    cert_tmp.flush()

    # escreve a chave
    key_tmp.write(
        key.private_bytes(
            Encoding.PEM,
            PrivateFormat.TraditionalOpenSSL,
            NoEncryption(),
        )
    )
    key_tmp.flush()

    return cert_tmp.name, key_tmp.name


def _prepare_cert_files(cfg: EfiConfig) -> tuple[str | None, str | None]:
    """
    Lê o arquivo que está no FileField (cfg.certificate) e
    transforma em arquivos .pem temporários.
    Se não tiver certificado, devolve (None, None).
    """
    if not cfg.certificate:
        return None, None

    # abre o arquivo QUE ESTÁ NO BANCO agora
    with cfg.certificate.open("rb") as f:
        p12_bytes = f.read()

    cert_path, key_path = _p12_to_pem_tempfiles(p12_bytes)
    return cert_path, key_path


def get_oauth_token(rifa: Rifa) -> str:
    """
    Faz o POST /oauth/token na Efí usando os dados da rifa.
    Se não conseguir, lança EfiError.
    """
    cfg = get_efi_config_for_rifa(rifa)
    if not cfg:
        raise EfiError("Nenhuma configuração Efí encontrada para esta rifa.")

    # escolhe o endpoint certo
    if cfg.sandbox:
        url = "https://api-sandbox.efipay.com.br/oauth/token"
    else:
        url = "https://api.efipay.com.br/oauth/token"

    payload = {
        "grant_type": "client_credentials"
    }

    # se no admin você subir OUTRO certificado, aqui ele lê o novo
    cert_path, key_path = _prepare_cert_files(cfg)

    try:
        resp = requests.post(
            url,
            json=payload,
            auth=(cfg.client_id, cfg.client_secret),
            cert=(cert_path, key_path) if cert_path and key_path else None,
            timeout=20,
        )
    except requests.exceptions.SSLError as e:
        # esse era exatamente o erro que apareceu no teu log
        raise EfiError(f"Erro de SSL com certificado da Efí: {e}")

    # às vezes a Efí devolve HTML de erro → dá ValueError no .json()
    try:
        data = resp.json()
    except ValueError:
        raise EfiError(f"Efí não devolveu JSON válido: {resp.status_code} {resp.text}")

    if resp.status_code != 200:
        raise EfiError(f"Erro ao pegar token na Efí: {resp.status_code} {data}")

    return data["access_token"]
