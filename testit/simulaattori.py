"""Simuloitu PxWeb-palvelin testejä varten.

Rakentaa oikean muotoiset metatieto- ja json-stat2-vastaukset, joihin on
istutettu tunnettuja virheitä. Näin tarkistuslogiikan voi todentaa ilman
yhteyttä vero2.stat.fi:hin.
"""

from __future__ import annotations

import itertools
from typing import Any

from pxtarkistus.pxclient import PxWebAsiakas

VUODET = ["2020", "2021", "2022", "2023", "2024"]
ERAT = ["HVT_TULOT_50", "HVT_TULOT_80", "HVT_VEROT_10", "HVT_TULOT_600", "HVT_TULOT_610", "HVT_TULOT_620"]
ERANIMET = {
    "HVT_TULOT_50": "2. Tulot yhteensä",
    "HVT_TULOT_80": "2.1 Palkkatulot yhteensä",
    "HVT_VEROT_10": "3. Verot ja maksut yhteensä",
    # additiivinen kolmikko erähierarkiatarkistusta varten: 5 = 5.1 + 5.2
    "HVT_TULOT_600": "5. Pääomatulot yhteensä",
    "HVT_TULOT_610": "5.1 Vuokratulot",
    "HVT_TULOT_620": "5.2 Luovutusvoitot",
}

# erähierarkia rikkoutuu vain kohdevuonna 2024 (yläerä liian suuri)
VIRHE_HIERARKIA = ("HVT_TULOT_600", "2024", 25_000_000.0)

# kuntataso: kunnan 006 postinumeroalueiden summa ylittää aluenäkymän luvun
VIRHE_ALUE = ("HVT_TULOT_80", "2024", "Sum", "006_02100", 5_000.0)

TAULUT: dict[str, dict[str, Any]] = {
    "testi/alue.px": {
        "title": "T.1 Testitaulu alueittain",
        "variables": [
            {
                "code": "Alue",
                "text": "Alue",
                "values": ["000", "01", "02", "005", "006"],
                "valueTexts": ["Yhteensä", "Uusimaa", "Varsinais-Suomi", "Alajärvi", "Espoo"],
                "elimination": True,
            },
            {"code": "Verovuosi", "text": "Verovuosi", "values": VUODET, "valueTexts": VUODET, "time": True},
            {
                "code": "Erä",
                "text": "Erä",
                "values": ERAT,
                "valueTexts": [ERANIMET[e] for e in ERAT],
                "elimination": True,
            },
            {
                "code": "Tunnusluvut",
                "text": "Tunnusluvut",
                "values": ["Sum", "N", "Mean"],
                "valueTexts": ["Summa, euroa", "Lukumäärä", "Keskiarvo, euroa"],
                "elimination": True,
            },
        ],
    },
    "testi/postinum.px": {
        "title": "T.3 Testitaulu kunnittain ja postinumeroittain (2024)",
        "variables": [
            {"code": "Verovuosi", "text": "Verovuosi", "values": ["2024"], "valueTexts": ["2024"], "time": True},
            {
                "code": "Erä",
                "text": "Erä",
                "values": ERAT,
                "valueTexts": [ERANIMET[e].split(" ", 1)[1] for e in ERAT],  # ilman numerointia
                "elimination": True,
            },
            {
                "code": "Kuntapostinumero",
                "text": "Kunta ja postinumero",
                "values": ["SSS", "005_62710", "005_ZZZPL", "006_02100", "006_MUUKN"],
                "valueTexts": ["Koko maa", "Alajärvi 62710", "Alajärvi, postinumero tuntematon",
                               "Espoo 02100", "Espoo, muu"],
                "elimination": True,
            },
            {
                "code": "Tunnusluvut",
                "text": "Tunnusluvut",
                "values": ["Sum", "N"],
                "valueTexts": ["Summa, euroa", "Lukumäärä"],
                "elimination": True,
            },
        ],
    },
    "testi/perhetyyppi.px": {
        "title": "T.2 Testitaulu perhetyypeittäin",
        "variables": [
            {"code": "Verovuosi", "text": "Verovuosi", "values": VUODET, "valueTexts": VUODET, "time": True},
            {
                "code": "Erä",
                "text": "Erä",
                "values": ERAT,
                "valueTexts": [ERANIMET[e] for e in ERAT],
                "elimination": True,
            },
            {
                "code": "Perhetyyppi",
                "text": "Perhetyyppi",
                "values": ["SS", "1", "2"],
                "valueTexts": ["Yhteensä", "Yksin asuvat", "Lapsiperheet"],
                "elimination": True,
            },
            {
                "code": "Tunnusluvut",
                "text": "Tunnusluvut",
                "values": ["Sum", "N"],
                "valueTexts": ["Summa, euroa", "Lukumäärä"],
                "elimination": True,
            },
        ],
    },
}


def _perusarvo(era: str, vuosi: str, tunnusluku: str) -> float:
    i = ERAT.index(era)
    y = int(vuosi) - 2020
    if tunnusluku == "N":
        return float(2_000_000 + 100_000 * i + 25_000 * y)
    return float(40_000_000_000 + 3_000_000_000 * i + 1_500_000_000 * y)


# ------------------------------------------------------------ istutetut virheet
# 1) ristiin:   perhetyyppitaulun kokonaissumma poikkeaa 2024 / HVT_TULOT_80
# 2) osasummat: aluetaulun maakuntien summa ei täsmää 2024 / HVT_VEROT_10
# 3) aikasarja: HVT_TULOT_50 hyppää 2022 ja puuttuu 2021 (aluetaulussa)
VIRHE_RISTIIN = ("testi/perhetyyppi.px", "HVT_TULOT_80", "2024", "Sum", 7_500_000.0)
VIRHE_OSASUMMA = ("testi/alue.px", "HVT_VEROT_10", "2024", "Sum", -12_340.0)
VIRHE_HYPPY = ("testi/alue.px", "HVT_TULOT_50", "2022", "Sum")
VIRHE_PUUTTUU = ("testi/alue.px", "HVT_VEROT_10", "2021", "Sum")


def arvo(taulu: str, era: str, vuosi: str, tunnusluku: str, luokka: dict[str, str]) -> float | None:
    """Palauttaa yhden solun arvon simuloidusta aineistosta."""
    if era == "HVT_TULOT_600":  # yläerä = alaerien summa
        kokonais = _perusarvo("HVT_TULOT_610", vuosi, tunnusluku) + _perusarvo(
            "HVT_TULOT_620", vuosi, tunnusluku
        )
        if (era, vuosi) == VIRHE_HIERARKIA[:2] and tunnusluku == "Sum":
            kokonais += VIRHE_HIERARKIA[2]
    else:
        kokonais = _perusarvo(era, vuosi, tunnusluku)

    if (taulu, era, vuosi, tunnusluku) == VIRHE_PUUTTUU and luokka.get("Alue") == "000":
        return None
    if (taulu, era, vuosi, tunnusluku) == VIRHE_HYPPY and luokka.get("Alue") == "000":
        kokonais *= 1.8  # epäuskottava vuosihyppy

    if taulu == "testi/postinum.px":
        koodi = luokka.get("Kuntapostinumero", "SSS")
        if koodi == "SSS":
            return kokonais
        k1 = round(kokonais / 3)  # samat kuntaluvut kuin aluetaulussa
        kunta = k1 if koodi.startswith("005") else kokonais - k1
        osa1 = round(kunta * 0.6)
        v = float(osa1 if koodi.endswith(("62710", "02100")) else kunta - osa1)
        if (era, vuosi, tunnusluku) == VIRHE_ALUE[:3] and koodi == VIRHE_ALUE[3]:
            v += VIRHE_ALUE[4]
        return v

    if taulu == "testi/alue.px":
        if tunnusluku == "Mean":
            summa = arvo(taulu, era, vuosi, "Sum", luokka)
            lkm = arvo(taulu, era, vuosi, "N", luokka)
            if summa is None or not lkm:
                return None
            return round(summa / lkm, 0)
        alue = luokka.get("Alue", "000")
        m1 = kokonais * 0.4
        m2 = kokonais - m1
        if (taulu, era, vuosi, tunnusluku) == VIRHE_OSASUMMA[:4]:
            m2 += VIRHE_OSASUMMA[4]  # maakuntien summa ei enää täsmää
        k1 = round(kokonais / 3)
        k2 = kokonais - k1
        return {"000": kokonais, "01": m1, "02": m2, "005": float(k1), "006": float(k2)}[alue]

    # perhetyyppitaulu
    if (taulu, era, vuosi, tunnusluku) == VIRHE_RISTIIN[:4]:
        kokonais += VIRHE_RISTIIN[4]
    p1 = round(kokonais / 4)
    p2 = kokonais - p1
    return {"SS": kokonais, "1": float(p1), "2": float(p2)}[luokka.get("Perhetyyppi", "SS")]


class SimuloituAsiakas(PxWebAsiakas):
    """PxWebAsiakas, joka vastaa itse ilman verkkoyhteyttä."""

    def __init__(self, valimuisti_hakemisto, **kwargs):
        super().__init__(
            valimuisti_hakemisto=valimuisti_hakemisto,
            valimuisti_tuntia=0,  # ei välimuistia testeissä
            tauko_s=0.0,
            **kwargs,
        )
        self.kutsut: list[tuple[str, str]] = []

    def _pyynto(self, metodi: str, osoite: str, **kwargs) -> Any:
        polku = osoite.split("/Vero/", 1)[-1]
        self.kutsut.append((metodi, polku))
        if metodi == "GET":
            if polku in TAULUT:
                return TAULUT[polku]
            return [{"id": t.split("/")[-1], "type": "t", "text": TAULUT[t]["title"]} for t in TAULUT]
        kysely = kwargs["json"]["query"]
        return self._vastaus(polku, kysely)

    def _vastaus(self, polku: str, kysely: list[dict[str, Any]]) -> dict[str, Any]:
        maaritys = TAULUT[polku]
        valinta = {q["code"]: list(q["selection"]["values"]) for q in kysely}
        # kyselystä puuttuvat muuttujat: eliminointimuuttujat jätetään pois,
        # jolloin PxWeb palauttaa niiden yhteensä-arvon -> simuloidaan samoin
        jarjestys, koot, koodit, nimiot = [], [], {}, {}
        oletukset: dict[str, str] = {}
        for m in maaritys["variables"]:
            c = m["code"]
            if c in valinta:
                jarjestys.append(c)
                koot.append(len(valinta[c]))
                koodit[c] = valinta[c]
                nimiot[c] = {
                    k: m["valueTexts"][m["values"].index(k)] for k in valinta[c]
                }
            else:
                oletukset[c] = m["values"][0]  # ensimmäinen arvo on Yhteensä

        arvot: list[float | None] = []
        for yhdistelma in itertools.product(*[koodit[c] for c in jarjestys]):
            rivi = dict(zip(jarjestys, yhdistelma))
            rivi_taydennetty = {**oletukset, **rivi}
            arvot.append(
                arvo(
                    polku,
                    rivi_taydennetty["Erä"],
                    rivi_taydennetty["Verovuosi"],
                    rivi_taydennetty["Tunnusluvut"],
                    rivi_taydennetty,
                )
            )

        return {
            "class": "dataset",
            "label": maaritys["title"],
            "id": jarjestys,
            "size": koot,
            "dimension": {
                c: {
                    "label": c,
                    "category": {
                        "index": {k: i for i, k in enumerate(koodit[c])},
                        "label": nimiot[c],
                    },
                }
                for c in jarjestys
            },
            "value": arvot,
        }
