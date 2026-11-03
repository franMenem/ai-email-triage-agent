"""
Perfiles del agente: cada uno define con qué prompt responde y qué tools puede usar.

Para sumar un rubro nuevo se agrega un perfil acá; drafter.py no se toca.
"""

from dataclasses import dataclass, field

from prompts import PROMPT_FREELANCE, SYSTEM_PROMPT
from tools import TOOL_BUSCAR_PEDIDO


@dataclass(frozen=True)
class Perfil:
    nombre: str
    prompt: str
    tools: list = field(default_factory=list)


PERFILES = {
    # Demo: atención al cliente de una tienda online, con consulta de pedidos
    "tienda": Perfil("tienda", SYSTEM_PROMPT, [TOOL_BUSCAR_PEDIDO]),
    # Uso diario: respuestas a los clientes de Fran, sin tools (todavía)
    "freelance": Perfil("freelance", PROMPT_FREELANCE),
}
