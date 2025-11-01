# efi_set_webhook_homolog.py
import requests
import json

# ====== CREDENCIAIS DE HOMOLOGAÇÃO ======
CLIENT_ID = "Client_Id_dcae576f2187401fd3d7569df3e118b93242542e"
CLIENT_SECRET = "Client_Secret_3b55f9d544e25f9eb0a64b834e1c709eb6d24fc1"

# sua chave pix
CHAVE_PIX = "b70e7b3a-4bea-4d87-98a7-3a4699c09492"

# URL pública do seu Django (ngrok)
WEBHOOK_URL = "https://overoptimistic-marsha-bunchily.ngrok-free.dev/api/pagamentos/webhook/efi/"

TOKEN_URL = "https://pix-h.api.efipay.com.br/oauth/token"
WEBHOOK_URL_EFI = f"https://pix-h.api.efipay.com.br/v2/webhook/{CHAVE_PIX}"

def main():
    # 1) pega token na homolog
    resp = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        json={"grant_type": "client_credentials"},
        timeout=15,
    )
    print("TOKEN STATUS:", resp.status_code)
    print("TOKEN BODY:", resp.text)
    resp.raise_for_status()
    access_token = resp.json()["access_token"]

    # 2) registra webhook
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"webhookUrl": WEBHOOK_URL}

    resp2 = requests.put(
        WEBHOOK_URL_EFI,
        headers=headers,
        data=json.dumps(payload),
        timeout=15,
    )
    print("WEBHOOK STATUS:", resp2.status_code)
    print("WEBHOOK BODY:", resp2.text)

if __name__ == "__main__":
    main()
