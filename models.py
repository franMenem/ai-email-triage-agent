from pydantic import BaseModel


class Email(BaseModel):  # Email hereda de Basemodel
    message_id: str
    sender: str
    subject: str  #| None = None si le agrego esto quiere decir que subject es opcional pero la consignda dice que es obligatorio
    body: str


class Borrador(BaseModel):
    borrador: str  # el texto del mail, listo para mandar
    notas_internas: str  # lo que Claude quiera avisarle al equipo.
