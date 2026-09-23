"""pxtarkistus – Verohallinnon julkisten verotilastojen ristiintarkistus.

Hakee taulut PxWeb-rajapinnasta (https://vero2.stat.fi/PXWeb/api/v1/fi/Vero/)
ja ajaa niille viisi tarkistusperhettä: näkymien välinen täsmäytys, taulun
sisäiset osasummat, aikasarjan eheys ja revisiot, yläerä vs. alaerien summa
sekä kunnan luku vs. sen postinumeroalueiden summa.
"""

__version__ = "1.4.0"

from .pxclient import Metatiedot, Muuttuja, PxWebAsiakas, PxWebVirhe, Taulu  # noqa: F401
from .inventaario import Roolit, tunnista_roolit  # noqa: F401
from .tarkistukset import (  # noqa: F401
    Havainto,
    Toleranssi,
    havainnot_kehykseksi,
    tarkista_aikasarja,
    tarkista_alueet,
    tarkista_erahierarkia,
    tarkista_osasummat,
    tarkista_ristiin,
)
