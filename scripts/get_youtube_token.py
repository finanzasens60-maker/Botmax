"""Ejecútalo UNA sola vez en tu computador para obtener el YT_REFRESH_TOKEN.

1. Descarga el archivo client_secret.json de Google Cloud (ver README, paso 3).
2. Ponlo en esta misma carpeta del repo.
3. Ejecuta:
       pip install google-auth-oauthlib
       python scripts/get_youtube_token.py
4. Se abre el navegador: entra con la cuenta de Google DUEÑA del canal y acepta.
5. Copia los 3 valores que se imprimen y pégalos como "Secrets" en GitHub.
"""
import glob
import json

from google_auth_oauthlib.flow import InstalledAppFlow

files = glob.glob("client_secret*.json")
if not files:
    raise SystemExit("No encuentro client_secret*.json en esta carpeta. Descárgalo de Google Cloud primero.")

flow = InstalledAppFlow.from_client_secrets_file(
    files[0], scopes=["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
info = json.load(open(files[0]))
client = info.get("installed") or info.get("web")

print("\nCopia estos 3 valores en GitHub -> Settings -> Secrets and variables -> Actions:\n")
print(f"YT_CLIENT_ID      = {client['client_id']}")
print(f"YT_CLIENT_SECRET  = {client['client_secret']}")
print(f"YT_REFRESH_TOKEN  = {creds.refresh_token}")
