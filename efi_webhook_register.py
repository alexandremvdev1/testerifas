# efi_webhook_register.py
import os
import json
import ssl
import requests
import certifi  # 👈 garante CA atualizada
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager


class TLS12HttpAdapter(HTTPAdapter):
    """
    Adapter que força TLS 1.2, mas agora usando um contexto COM CA válida.
    """
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        # cria contexto padrão (com CAs)
        ctx = ssl.create_default_context()
        # garante CA atualizada mesmo em container
        ctx.load_verify_locations(cafile=certifi.where())
        # força TLS 1.2 (igual você queria)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **pool_kwargs,
        )


# ================= CONFIG =================
EFI_ENV = os.getenv("EFI_ENV", "prod").lower()

# produção
EFI_PROD_CLIENT_ID = os.getenv(
    "EFI_PROD_CLIENT_ID",
    "Client_Id_56397b13dca78b3615633762fbafffa5b459429f",
)
EFI_PROD_CLIENT_SECRET = os.getenv(
    "EFI_PROD_CLIENT_SECRET",
    "Client_Secret_1aa8f2d624ed250728635afd45f6574b68aa12ee",
)

# homolog
EFI_HOMOLOG_CLIENT_ID = os.getenv(
    "EFI_HOMOLOG_CLIENT_ID",
    "Client_Id_dcae576f2187401fd3d7569df3e118b93242542e",
)
EFI_HOMOLOG_CLIENT_SECRET = os.getenv(
    "EFI_HOMOLOG_CLIENT_SECRET",
    "Client_Secret_3b55f9d544e25f9eb0a64b834e1c709eb6d24fc1",
)

# teus arquivos que JÁ estão no container
CERT_PEM = os.getenv(
    "EFI_CERT_PEM",
    "/app/certs/producao-848943-meuappacoes_cert.pem",
)
KEY_PEM = os.getenv(
    "EFI_KEY_PEM",
    "/app/certs/producao-848943-meuappacoes_key.pem",
)

WEBHOOK_URL = os.getenv(
    "EFI_WEBHOOK_URL",
    "https://rifas-online.fly.dev/api/pagamentos/webhook/efi/",
)


def get_cfg():
    if EFI_ENV == "homolog":
        return {
            "name": "HOMOLOG",
            "token_url": "https://pix-h.api.efipay.com.br/oauth/token",
            "webhook_url": "https://pix-h.api.efipay.com.br/v2/webhook",
            "client_id": EFI_HOMOLOG_CLIENT_ID,
            "client_secret": EFI_HOMOLOG_CLIENT_SECRET,
        }
    else:
        return {
            "name": "PROD",
            "token_url": "https://pix.api.efipay.com.br/oauth/token",
            "webhook_url": "https://pix.api.efipay.com.br/v2/webhook",
            "client_id": EFI_PROD_CLIENT_ID,
            "client_secret": EFI_PROD_CLIENT_SECRET,
        }


def main():
    cfg = get_cfg()

    print(f"Registrando webhook na EFI — ambiente: {cfg['name']}")
    print(f"→ URL do webhook: {WEBHOOK_URL}")
    print(f"→ usando CERT: {CERT_PEM}")
    print(f"→ usando KEY : {KEY_PEM}")

    # sessão com TLS 1.2 + CA válida
    s = requests.Session()
    s.mount("https://", TLS12HttpAdapter())

    # 1) TOKEN
    try:
        token_resp = s.post(
            cfg["token_url"],
            auth=(cfg["client_id"], cfg["client_secret"]),
            json={"grant_type": "client_credentials"},
            cert=(CERT_PEM, KEY_PEM),     # mTLS
            verify=certifi.where(),       # 👈 valida certificado da EFI
            timeout=25,
        )
    except requests.RequestException as e:
        print("ERRO ao pedir token (TLS 1.2):", repr(e))
        return

    print("TOKEN STATUS:", token_resp.status_code)
    print("TOKEN BODY:", token_resp.text)

    if token_resp.status_code != 200:
        print("⚠️ Não deu pra pegar token, parando aqui.")
        return

    access_token = token_resp.json()["access_token"]

    # 2) REGISTRA WEBHOOK
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "webhookUrl": WEBHOOK_URL,
    }

    try:
        wresp = s.put(
            cfg["webhook_url"],
            headers=headers,
            data=json.dumps(payload),
            cert=(CERT_PEM, KEY_PEM),
            verify=certifi.where(),   # 👈 também valida aqui
            timeout=25,
        )
    except requests.RequestException as e:
        print("ERRO ao registrar webhook (TLS 1.2):", repr(e))
        return

    print("WEBHOOK STATUS:", wresp.status_code)
    print("WEBHOOK BODY:", wresp.text)


if __name__ == "__main__":
    main()
