# efi_webhook_register.py
import json
import requests

# ----------------------------------------------------
# QUAL AMBIENTE?  "prod"  ou  "homolog"
# ----------------------------------------------------
ENV = "prod"   # troque para "homolog" quando quiser testar

# ----------------------------------------------------
# CERTS (precisa só quando o endpoint da EFI exige mTLS)
# você já tem esses arquivos aí na pasta do conversor
# ----------------------------------------------------
CERT_PEM = r"C:\Users\ALEXANDRE\Desktop\conversor-p12-efi-main\producao-848943-meuappacoes_cert.pem"
KEY_PEM  = r"C:\Users\ALEXANDRE\Desktop\conversor-p12-efi-main\producao-848943-meuappacoes_key.pem"

# ----------------------------------------------------
# DADOS DA SUA CONTA
# ----------------------------------------------------
# chave pix da sua conta (aquela que você mandou)
CHAVE_PIX = "b70e7b3a-4bea-4d87-98a7-3a4699c09492"

# seu endpoint público do Django (ngrok)
DJANGO_WEBHOOK_URL = "https://overoptimistic-marsha-bunchily.ngrok-free.dev/api/pagamentos/webhook/efi/"

CONFIG = {
    "prod": {
        "client_id": "Client_Id_56397b13dca78b3615633762fbafffa5b459429f",
        "client_secret": "Client_Secret_1aa8f2d624ed250728635afd45f6574b68aa12ee",
        "token_url": "https://pix.api.efipay.com.br/oauth/token",
        "webhook_url": f"https://pix.api.efipay.com.br/v2/webhook/{CHAVE_PIX}",
        # ⚠️ PRODUÇÃO: você já conseguiu token antes, então a gente
        # reaproveita esse token aqui e nem tenta buscar outro
        "last_working_token": (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJ0eXBlIjoiYWNjZXNzX3Rva2VuIiwiY2xpZW50SWQiOiJDbGllbnRfSWRfNTYzOTdiMTNkY2E3OGIzNjE1NjMzNzYyZmJhZmZmYTViNDU5NDI5ZiIsImFjY291bnQiOjg0ODk0MywiYWNjb3VudF9jb2RlIjoiYmI3ZGY2YmVkOTA0MmNhNjFjNmUwZDE1MGFlOWQ4NDEiLCJzY29wZXMiOlsiY29iLnJlYWQiLCJjb2Iud3JpdGUiLCJwaXguc2VuZCIsIndlYmhvb2sucmVhZCIsIndlYmhvb2sud3JpdGUiXSwiZXhwaXJlc0luIjozNjAwLCJjb25maWd1cmF0aW9uIjp7Ing1dCNTMjU2IjoiZ2dQclVKZWFJai9DaVVuVTNRckFPL2NBWG1kL3FNMHNiRld4bSs0cGVCcz0ifSwiaWF0IjoxNzYyMDM4MjE2LCJleHAiOjE3NjIwNDE4MTZ9."
            "STZ-Ad2uc54i9yhaQRUEPCawIi4NBliObu_vQbtIweQ"
        ),
        "need_mtls": True,  # o PUT do webhook exige mTLS → vai dar 400 no ngrok
    },
    "homolog": {
        "client_id": "Client_Id_dcae576f2187401fd3d7569df3e118b93242542e",
        "client_secret": "Client_Secret_3b55f9d544e25f9eb0a64b834e1c709eb6d24fc1",
        "token_url": "https://pix-h.api.efipay.com.br/oauth/token",
        "webhook_url": f"https://pix-h.api.efipay.com.br/v2/webhook/{CHAVE_PIX}",
        "last_working_token": None,  # aqui vc ainda não conseguiu
        "need_mtls": True,
    },
}


def get_token(cfg: dict) -> str:
    """
    - PROD: usa primeiro o token que a gente já sabe que funcionou
    - HOMOLOG: tenta pegar via mTLS; se o servidor fechar, mostra msg bonita
    """
    if cfg.get("last_working_token"):
        print("USANDO TOKEN SALVO (produção)")
        return cfg["last_working_token"]

    print("BUSCANDO TOKEN NA EFI...")
    try:
        resp = requests.post(
            cfg["token_url"],
            auth=(cfg["client_id"], cfg["client_secret"]),
            json={"grant_type": "client_credentials"},
            # homolog tem pedido mTLS -> manda o cert
            cert=(CERT_PEM, KEY_PEM),
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        print("ERRO ao pedir token:", e)
        print("⚠ Homolog costuma fechar a conexão se o mTLS não bater certinho.")
        raise

    print("TOKEN STATUS:", resp.status_code)
    print("TOKEN BODY:", resp.text)

    resp.raise_for_status()
    return resp.json()["access_token"]


def register_webhook(cfg: dict, access_token: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"webhookUrl": DJANGO_WEBHOOK_URL}

    try:
        resp = requests.put(
            cfg["webhook_url"],
            headers=headers,
            data=json.dumps(payload),
            # o erro 400 que você está vendo é justamente porque AQUI a EFI
            # exige mTLS e o seu ngrok não tem → mas vamos mandar mesmo assim
            cert=(CERT_PEM, KEY_PEM),
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        print("ERRO ao registrar webhook:", e)
        return

    print("WEBHOOK STATUS:", resp.status_code)
    print("WEBHOOK BODY:", resp.text)


def main():
    cfg = CONFIG[ENV]
    token = get_token(cfg)
    register_webhook(cfg, token)


if __name__ == "__main__":
    main()
