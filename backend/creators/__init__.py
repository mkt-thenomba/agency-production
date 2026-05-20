"""Configs estáticas de creators que se siembran en la BD al iniciar."""
from .marcelo_gullo import MARCELO_GULLO
from .raices_europa import RAICES_EUROPA
from .jose_ballesteros import JOSE_BALLESTEROS
from .gonzalo_rodriguez import GONZALO_RODRIGUEZ
from .placeholders import PLACEHOLDERS

ALL_CREATOR_CONFIGS = [
    MARCELO_GULLO,
    RAICES_EUROPA,
    JOSE_BALLESTEROS,
    GONZALO_RODRIGUEZ,
    *PLACEHOLDERS,
]
