"""Configs estáticas de creators que se siembran en la BD al iniciar."""
from .marcelo_gullo import MARCELO_GULLO
from .jose_ballesteros import JOSE_BALLESTEROS
from .gonzalo_rodriguez import GONZALO_RODRIGUEZ
from .peregrinos_en_distopia import PEREGRINOS_EN_DISTOPIA
from .placeholders import PLACEHOLDERS

ALL_CREATOR_CONFIGS = [
    MARCELO_GULLO,
    JOSE_BALLESTEROS,
    GONZALO_RODRIGUEZ,
    PEREGRINOS_EN_DISTOPIA,
    *PLACEHOLDERS,
]
