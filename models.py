from pydantic import BaseModel
from typing import Literal


class Email(BaseModel):  # Email hereda de Basemodel
    message_id: str
    sender: str
    subject: str  #| None = None si le agrego esto quiere decir que subject es opcional pero la consignda dice que es obligatorio
    body: str


class Borrador(BaseModel):
    borrador: str  # el texto del mail, listo para mandar
    notas_internas: str  # lo que Claude quiera avisarle al equipo.
    intencion: Literal[
        "estado_pedido", "devolucion", "reclamo", "consulta_general", "otro"
    ]  # la intención del usuario
    urgencia: Literal["alta", "media", "baja"]  # la urgencia de respuesta del mail
