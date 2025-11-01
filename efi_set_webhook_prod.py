# efi_set_webhook.py
import requests
import json
from pathlib import Path

# ==== DADOS QUE VOCÊ ME PASSOU ====
CLIENT_ID = "Client_Id_56397b13dca78b3615633762fbafffa5b459429f"
CLIENT_SECRET = "Client_Secret_1aa8f2d624ed250728635afd45f6574b68aa12ee"
CHAVE_PIX = "b70e7b3a-4bea-4d87-98a7-3a4699c09492"

# seu endpoint público (ngrok -> Django)
WEBHOOK_URL = "https://overoptimistic-marsha-bunchily.ngrok-free.dev/api/pagamentos/webhook/efi/"

# ==== PRODUÇÃO EFI ====
TOKEN_URL = "https://pix.api.efipay.com.br/oauth/token"
WEBHOOK_URL_EFI = f"https://pix.api.efipay.com.br/v2/webhook/{CHAVE_PIX}"

# ==== CAMINHOS DOS CERTS (PRODUÇÃO) ====
# você criou esses dois lá na pasta conversor
CERT_FILE = r"C:\Users\ALEXANDRE\Desktop\conversor-p12-efi-main\producao-848943-meuappacoes_cert.pem"
KEY_FILE = r"C:\Users\ALEXANDRE\Desktop\conversor-p12-efi-main\producao-848943-meuappacoes_key.pem"

# opcional: garantir que existe
for f in (CERT_FILE, KEY_FILE):
    if not Path(f).exists():
        raise SystemExit(f"ATENÇÃO: arquivo não encontrado: {f}")

# =============== 1) PEGAR TOKEN (PRODUÇÃO, COM CERT) ===============
try:
    token_resp = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        json={"grant_type": "client_credentials"},
        cert=(CERT_FILE, KEY_FILE),  # 👈 AQUI VAI O mTLS
        timeout=20,
    )
except Exception as e:
    print("ERRO ao chamar /oauth/token:", e)
    raise SystemExit(1)

print("TOKEN STATUS:", token_resp.status_code)
print("TOKEN BODY:", token_resp.text)

if token_resp.status_code != 200:
    raise SystemExit("Não consegui token na PRODUÇÃO. Veja o BODY acima.")

access_token = token_resp.json()["access_token"]

# =============== 2) REGISTRAR WEBHOOK (PRODUÇÃO, COM CERT) ===============
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json",
}
payload = {
    "webhookUrl": WEBHOOK_URL
}

try:
    wh_resp = requests.put(
        WEBHOOK_URL_EFI,
        headers=headers,
        data=json.dumps(payload),
        cert=(CERT_FILE, KEY_FILE),  # 👈 de novo o mTLS
        timeout=20,
    )
except Exception as e:
    print("ERRO ao chamar PUT /v2/webhook/{chave}: ", e)
    raise SystemExit(1)

print("WEBHOOK STATUS:", wh_resp.status_code)
print("WEBHOOK BODY:", wh_resp.text)
