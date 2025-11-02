import os
import json
import requests

# --------------------------------------------------
# CONFIGURAÇÕES
# --------------------------------------------------
# escolha aqui: "prod" ou "homolog"
ENV = os.environ.get("EFI_ENV", "prod").lower()  # padrão: produção

# URL pública do seu Django no Fly
WEBHOOK_URL = os.environ.get(
    "EFI_WEBHOOK_URL",
    "https://rifas-online.fly.dev/api/pagamentos/webhook/efi/"
)

# credenciais produção
PROD_CLIENT_ID = os.environ.get(
    "EFI_CLIENT_ID",
    "Client_Id_56397b13dca78b3615633762fbafffa5b459429f"
)
PROD_CLIENT_SECRET = os.environ.get(
    "EFI_CLIENT_SECRET",
    "Client_Secret_1aa8f2d624ed250728635afd45f6574b68aa12ee"
)

# credenciais homolog
H_CLIENT_ID = os.environ.get(
    "EFI_H_CLIENT_ID",
    "Client_Id_dcae576f2187401fd3d7569df3e118b93242542e"
)
H_CLIENT_SECRET = os.environ.get(
    "EFI_H_CLIENT_SECRET",
    "Client_Secret_3b55f9d544e25f9eb0a64b834e1c709eb6d24fc1"
)

# endpoints
PROD_TOKEN_URL = "https://pix.api.efipay.com.br/oauth/token"
PROD_WEBHOOK_URL = "https://pix.api.efipay.com.br/v2/webhook"
H_TOKEN_URL = "https://pix-h.api.efipay.com.br/oauth/token"
H_WEBHOOK_URL = "https://pix-h.api.efipay.com.br/v2/webhook"

# a chave pix da sua conta
PIX_CHAVE = os.environ.get(
    "PIX_CHAVE",
    "b70e7b3a-4bea-4d87-98a7-3a4699c09492"
)


def get_cfg():
    if ENV == "homolog":
        return {
            "token_url": H_TOKEN_URL,
            "webhook_url": f"{H_WEBHOOK_URL}/{PIX_CHAVE}",
            "client_id": H_CLIENT_ID,
            "client_secret": H_CLIENT_SECRET,
            "name": "HOMOLOG",
        }
    else:
        return {
            "token_url": PROD_TOKEN_URL,
            "webhook_url": f"{PROD_WEBHOOK_URL}/{PIX_CHAVE}",
            "client_id": PROD_CLIENT_ID,
            "client_secret": PROD_CLIENT_SECRET,
            "name": "PROD",
        }


def main():
    cfg = get_cfg()
    print(f"Registrando webhook na EFI — ambiente: {cfg['name']}")
    print(f"→ URL do seu webhook: {WEBHOOK_URL}")

    # 1) pega token
    try:
        token_resp = requests.post(
            cfg["token_url"],
            auth=(cfg["client_id"], cfg["client_secret"]),
            json={"grant_type": "client_credentials"},
            timeout=20,
        )
    except requests.RequestException as e:
        print("ERRO ao pedir token:", repr(e))
        return

    print("TOKEN STATUS:", token_resp.status_code)
    print("TOKEN BODY:", token_resp.text)

    if token_resp.status_code != 200:
        print("Não consegui token, parando.")
        return

    token_data = token_resp.json()
    access_token = token_data["access_token"]

    # 2) registra webhook
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "webhookUrl": WEBHOOK_URL
    }

    try:
        wh_resp = requests.put(
            cfg["webhook_url"],
            headers=headers,
            data=json.dumps(payload),
            timeout=20,
        )
    except requests.RequestException as e:
        print("ERRO ao registrar webhook:", repr(e))
        return

    print("WEBHOOK STATUS:", wh_resp.status_code)
    print("WEBHOOK BODY:", wh_resp.text)


if __name__ == "__main__":
    main()
