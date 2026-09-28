#!/usr/bin/env python3
"""Tuottaa kansion `esimerkki/` sisällön simuloidusta aineistosta.

Esimerkkiraportti näyttää, miltä tulos näyttää, ilman että kenenkään tarvitsee
ajaa tarkistusta oikeaa rajapintaa vasten. Aineisto on testisimulaattorin, joten
jokainen havainto on istutettu tarkoituksella ja tiedetään oikeaksi.

    python testit/tee_esimerkki.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(JUURI))

from pxtarkistus import __version__  # noqa: E402
from pxtarkistus.inventaario import tunnista_roolit  # noqa: E402
from pxtarkistus.kuittaukset import lue_kuittaukset, sovella  # noqa: E402
from pxtarkistus.raportti import aikaleima, kirjoita_excel  # noqa: E402
from pxtarkistus.tarkistukset import (  # noqa: E402
    havainnot_kehykseksi,
    tarkista_aikasarja,
    tarkista_alueet,
    tarkista_erahierarkia,
    tarkista_osasummat,
    tarkista_ristiin,
)
from testit.simulaattori import SimuloituAsiakas  # noqa: E402

LUOKITUKSET = {
    "Alue": {"yhteensa": "000", "tasot": {"maakunnat": "^[0-9]{2}$", "kunnat": "^[0-9]{3}$"}},
    "Perhetyyppi": {"yhteensa": "SS"},
    "Kuntapostinumero": {
        "yhteensa": "SSS",
        "tasot": {"kunnat": "^[0-9]{3}$", "postinumeroalueet": "^[0-9]{3}_"},
    },
}
TAULUT = ["testi/alue.px", "testi/perhetyyppi.px", "testi/postinum.px"]

# Esimerkissä yksi havainto on kuitattu, jotta Kuitatut-välilehti näkyy.
KUITTAUKSET = {
    "kuittaukset": [
        {
            "tunnus": "esimerkki-aikasarjahyppy",
            "syy": "Esimerkki kuittauksesta: hyppy on selvitetty luokitusmuutokseksi.",
            "tarkistus": "aikasarja",
            "era": "HVT_VEROT_10",
            "verovuosi": "2021",
        }
    ]
}


def main() -> int:
    kohde = JUURI / "esimerkki"
    kohde.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        asiakas = SimuloituAsiakas(valimuisti_hakemisto=Path(d) / "vm")
        roolit = [tunnista_roolit(asiakas.metatiedot(p), LUOKITUKSET) for p in TAULUT]
        vertailtavat = [r for r in roolit if r.polku != "testi/postinum.px"]

        havainnot: list = []
        aineistot = {}
        h, aineistot["ristiin"] = tarkista_ristiin(
            asiakas, vertailtavat, sorted({v for r in vertailtavat for v in r.aika.arvot})
        )
        havainnot += h
        h, aineistot["osasummat"] = tarkista_osasummat(asiakas, roolit, "2024")
        havainnot += h
        h, aineistot["aikasarja"], _ = tarkista_aikasarja(
            asiakas, vertailtavat, tilannevedos=Path(d) / "vedos.json"
        )
        havainnot += h
        h, aineistot["erahierarkia"] = tarkista_erahierarkia(
            asiakas, vertailtavat, "2024", saannot_tiedosto=Path(d) / "saannot.json"
        )
        havainnot += h
        h, aineistot["alueet"] = tarkista_alueet(asiakas, roolit)
        havainnot += h

        kehys = havainnot_kehykseksi(havainnot)
        kehys.insert(0, "joukko", "esimerkki")
        saannot = lue_kuittaukset(KUITTAUKSET, lahde="tee_esimerkki.py")
        kehys, kuitatut = sovella(kehys, saannot)
        meta = {
            "Joukko": "esimerkki – simuloitu aineisto, istutetut virheet",
            "Verovuosi": "2024",
            "Tauluja": len(roolit),
            "Tarkistukset": "ristiin, osasummat, aikasarja, erahierarkia, alueet",
            "Kuitattuja havaintoja": f"{len(kuitatut)} (Kuitatut-välilehti)",
            "Ajettu": aikaleima(),
            "Ohjelmaversio": __version__,
        }
        tiedosto = kirjoita_excel(
            kohde / "esimerkkiraportti.xlsx", kehys, aineistot, meta, kuitatut=kuitatut
        )
    print(f"{tiedosto}  ({tiedosto.stat().st_size / 1000:.0f} kt)")
    for d2 in sorted(kohde.glob("esimerkkiraportti_data_*.csv.gz")):
        print(f"  {d2.name}  ({d2.stat().st_size / 1000:.0f} kt)")
    print(f"Havaintoja {len(kehys)}, kuitattuja {len(kuitatut)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
