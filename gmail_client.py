"""
Agente de mails — Paso 6: leer los mails de la etiqueta "agente" en Gmail
========================================================================

`conectar()` ya está hecha: es el login estándar de Google (OAuth).
La primera vez abre el navegador para que des permiso; después guarda
token.json y no vuelve a preguntar. credentials.json y token.json están
en el .gitignore: nunca se suben.

Permisos que pedimos (SCOPES): leer mails y crear borradores.
Este archivo NUNCA va a tener una función que envíe mails.

Tu parte: implementá `listar_pendientes(servicio)`.
- Tiene que buscar los mensajes que tengan la etiqueta "agente" y que
  no estén leídos (query de Gmail: "label:agente is:unread").
- Devuelve una LISTA con los IDs (str) de esos mensajes. Si no hay, [].

Leé antes:
- https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
  Solo "Query parameters" (fijate en `q`) y "Response body" (qué trae `messages`
  y qué pasa cuando no hay ninguno).
- La llamada en Python tiene esta forma (encadenada, termina en .execute()):
      servicio.users().messages().list(userId="me", ...).execute()
  Probá primero con un print() de lo que devuelve, antes de armar la lista.

Probalo con: uv run python gmail_client.py
(no usa la API de Claude: es gratis)
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
CARPETA = Path(__file__).parent
RUTA_CREDENCIALES = CARPETA / "credentials.json"
RUTA_TOKEN = CARPETA / "token.json"


def conectar():
    """Devuelve un cliente de la Gmail API autenticado con tu cuenta."""
    creds = None
    if RUTA_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(RUTA_TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(RUTA_CREDENCIALES), SCOPES
            )
            creds = flow.run_local_server(port=0)
        RUTA_TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def listar_pendientes(servicio):
    respuesta = servicio.users().messages().list(userId="me", q="label:agente is:unread").execute()
    ids = [mensaje["id"] for mensaje in respuesta.get("messages", []) ]

    return ids
     


if __name__ == "__main__":
    servicio = conectar()
    perfil = servicio.users().getProfile(userId="me").execute()
    print("Conectado como:", perfil["emailAddress"])

    ids = listar_pendientes(servicio)
    print("Mails pendientes con la etiqueta 'agente':", ids)

    assert isinstance(ids, list), "Tiene que devolver una lista (aunque esté vacía)"
    assert all(isinstance(i, str) for i in ids), "Cada elemento tiene que ser un ID (str)"
    print("¡Paso 6 completo! 🎉")
