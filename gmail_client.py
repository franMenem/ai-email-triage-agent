"""
Cliente de Gmail: leer los mails de la etiqueta "agente" y dejar borradores.

`conectar()` es el login estándar de Google (OAuth). La primera vez abre el
navegador para que des permiso; después guarda token.json y no vuelve a
preguntar. credentials.json y token.json están en el .gitignore.

Permisos (SCOPES): leer mails y crear borradores. Este archivo NUNCA tiene
una función que envíe mails: una persona revisa cada borrador y lo manda.

Probalo con: uv run python gmail_client.py   (no usa la API de Claude)
"""

import base64
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models import Email

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


def extraer_texto(payload):
    """Devuelve el cuerpo en texto plano de un mensaje de Gmail.

    Gmail manda el cuerpo en base64 (variante URL) y, si el mail tiene HTML
    o adjuntos, partido en "parts" anidadas. Busca la primera parte text/plain.
    """
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for parte in payload.get("parts", []):
        texto = extraer_texto(parte)
        if texto:
            return texto
    return ""


def encabezados(mensaje):
    """Pasa la lista de headers [{"name": ..., "value": ...}] a un dict."""
    return {h["name"].lower(): h["value"] for h in mensaje["payload"]["headers"]}


def leer_mail(servicio, mensaje_id):
    """Trae un mensaje de Gmail y lo convierte en el `Email` que usa el agente."""
    mensaje = servicio.users().messages().get(userId="me", id=mensaje_id, format="full").execute()
    h = encabezados(mensaje)
    _, direccion = parseaddr(h.get("from", ""))  # "Mark <k@x.com>" -> "k@x.com"
    return Email(
        message_id=mensaje_id,
        sender=direccion,
        subject=h.get("subject", ""),
        body=extraer_texto(mensaje["payload"]),
    )


def crear_borrador(servicio, mensaje_id, texto):
    """Deja `texto` como borrador de respuesta, dentro del hilo del mail original."""
    original = servicio.users().messages().get(userId="me", id=mensaje_id, format="metadata").execute()
    h = encabezados(original)
    asunto = h.get("subject", "")

    respuesta = EmailMessage()
    respuesta["To"] = h.get("reply-to") or h.get("from", "")
    respuesta["Subject"] = asunto if asunto.lower().startswith("re:") else f"Re: {asunto}"
    # Estos dos headers hacen que Gmail (y el cliente) lo muestren como respuesta en el hilo
    if "message-id" in h:
        respuesta["In-Reply-To"] = h["message-id"]
        respuesta["References"] = h["message-id"]
    respuesta.set_content(texto)

    raw = base64.urlsafe_b64encode(respuesta.as_bytes()).decode()
    return servicio.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": original["threadId"]}},
    ).execute()


if __name__ == "__main__":
    servicio = conectar()
    perfil = servicio.users().getProfile(userId="me").execute()
    print("Conectado como:", perfil["emailAddress"])

    ids = listar_pendientes(servicio)
    print("Mails pendientes con la etiqueta 'agente':", ids)
    for mensaje_id in ids:
        mail = leer_mail(servicio, mensaje_id)
        print(f"- De: {mail.sender} | Asunto: {mail.subject} | {mail.body[:80]!r}")
