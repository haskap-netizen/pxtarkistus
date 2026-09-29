"""Kuittaukset: jo tutkitut havainnot pois raportin päältä.

Sama julkaisu tuottaa samat havainnot joka ajossa. Kun havainto on kerran
selvitetty – se on tiedossa oleva julkaisun piirre, korjattu tai tarkoituksella
hyväksytty – se kannattaa kuitata, jotta seuraavassa ajossa jäljelle jää vain
uusi tieto.

Kuittaus ei poista havaintoa aineistosta: se siirtyy raportin omalle
Kuitatut-välilehdelle perusteluineen ja jää pois lukumääristä sekä
paluuarvosta. Tarkistuksen tulos on siis edelleen todennettavissa.

Sääntötiedosto (YAML):

    kuittaukset:
      - tunnus: seurakuntien-paajako
        syy: >
          Yhteensä sisältää kaksi erikseen listattua seurakuntaa, joten
          luokkien summa on aina suurempi. Ei julkaisuvirhe.
        tarkistus: osasummat
        kohde: ".*seurakunta.*"
        voimassa_asti: 2027-06-30

Jokainen annettu kenttä on säännöllinen lauseke, jonka on täsmättävä koko
kenttään (re.fullmatch). Kenttä voi olla myös lista, jolloin riittää, että
yksi vaihtoehdoista täsmää. Kentät, joita ei mainita, eivät rajaa mitään.
`voimassa_asti` päättää kuittauksen automaattisesti, jottei vanha perustelu
vaimenna havaintoa ikuisesti.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

# Kentät, joilla havaintoa voi rajata. Muut avaimet ovat kirjoitusvirheitä,
# joista varoitetaan – hiljaa ohitettu ehto vaimentaisi liikaa.
EHTOKENTAT = (
    "joukko",
    "tarkistus",
    "vakavuus",
    "era",
    "era_nimi",
    "verovuosi",
    "tunnusluku",
    "kohde",
    "vertailukohta",
    "kuvaus",
)
MUUT_AVAIMET = ("tunnus", "syy", "voimassa_asti")


class KuittausVirhe(RuntimeError):
    """Kuittaustiedosto on virheellinen."""


@dataclass
class Kuittaus:
    tunnus: str
    syy: str
    ehdot: dict[str, list[re.Pattern[str]]] = field(default_factory=dict)
    voimassa_asti: dt.date | None = None
    osumat: int = 0

    @property
    def teksti(self) -> str:
        return f"{self.tunnus}: {self.syy}" if self.syy else self.tunnus

    def vanhentunut(self, paiva: dt.date | None = None) -> bool:
        return bool(self.voimassa_asti and self.voimassa_asti < (paiva or dt.date.today()))

    def osuu(self, havainnot: pd.DataFrame) -> pd.Series:
        maski = pd.Series(True, index=havainnot.index)
        for kentta, kaavat in self.ehdot.items():
            if kentta not in havainnot.columns:
                return pd.Series(False, index=havainnot.index)
            sarake = havainnot[kentta].fillna("").astype(str)
            osuma = pd.Series(False, index=havainnot.index)
            for kaava in kaavat:
                osuma |= sarake.map(lambda t, k=kaava: bool(k.fullmatch(t)))
            maski &= osuma
        return maski


def _paiva(arvo: Any, tunnus: str) -> dt.date | None:
    if arvo in (None, ""):
        return None
    if isinstance(arvo, dt.datetime):
        return arvo.date()
    if isinstance(arvo, dt.date):
        return arvo
    try:
        return dt.date.fromisoformat(str(arvo).strip()[:10])
    except ValueError as e:
        raise KuittausVirhe(
            f"Kuittauksen '{tunnus}' voimassa_asti ei ole päivämäärä (odotettu 2027-06-30): {arvo}"
        ) from e


def _kaavat(arvo: Any, tunnus: str, kentta: str) -> list[re.Pattern[str]]:
    arvot = arvo if isinstance(arvo, (list, tuple)) else [arvo]
    kaavat = []
    for a in arvot:
        try:
            kaavat.append(re.compile(str(a), re.IGNORECASE))
        except re.error as e:
            raise KuittausVirhe(
                f"Kuittauksen '{tunnus}' kentän '{kentta}' lauseke ei kelpaa: {a} ({e})"
            ) from e
    return kaavat


def lue_kuittaukset(kohta: Any, lahde: str = "asetukset") -> list[Kuittaus]:
    """Muuntaa YAML-rakenteen säännöiksi."""
    if not kohta:
        return []
    if isinstance(kohta, dict):
        kohta = kohta.get("kuittaukset") or []
    if not isinstance(kohta, list):
        raise KuittausVirhe(f"{lahde}: kuittaukset pitää olla lista")
    saannot: list[Kuittaus] = []
    for i, rivi in enumerate(kohta, start=1):
        if not isinstance(rivi, dict):
            raise KuittausVirhe(f"{lahde}: kuittaus {i} ei ole sanakirja")
        tunnus = str(rivi.get("tunnus") or f"kuittaus-{i}")
        tuntemattomat = [k for k in rivi if k not in EHTOKENTAT + MUUT_AVAIMET]
        if tuntemattomat:
            raise KuittausVirhe(
                f"{lahde}: kuittauksessa '{tunnus}' tuntematon kenttä "
                f"{', '.join(tuntemattomat)}. Käytettävissä: {', '.join(EHTOKENTAT)}"
            )
        ehdot = {k: _kaavat(rivi[k], tunnus, k) for k in EHTOKENTAT if rivi.get(k) not in (None, "")}
        if not ehdot:
            raise KuittausVirhe(
                f"{lahde}: kuittauksella '{tunnus}' ei ole yhtään ehtoa – se vaimentaisi kaiken"
            )
        syy = str(rivi.get("syy") or "").strip()
        if not syy:
            raise KuittausVirhe(f"{lahde}: kuittaukselta '{tunnus}' puuttuu syy")
        saannot.append(
            Kuittaus(
                tunnus=tunnus,
                syy=" ".join(syy.split()),
                ehdot=ehdot,
                voimassa_asti=_paiva(rivi.get("voimassa_asti"), tunnus),
            )
        )
    tunnukset = [s.tunnus for s in saannot]
    kahdesti = sorted({t for t in tunnukset if tunnukset.count(t) > 1})
    if kahdesti:
        raise KuittausVirhe(f"{lahde}: sama tunnus useammassa kuittauksessa: {', '.join(kahdesti)}")
    return saannot


def lataa_tiedostosta(polku: Path | str) -> list[Kuittaus]:
    """Lukee kuittaukset YAML-tiedostosta. Puuttuva tiedosto = ei kuittauksia."""
    import yaml

    p = Path(polku)
    if not p.exists():
        return []
    sisalto = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return lue_kuittaukset(sisalto, lahde=str(p))


def sovella(
    havainnot: pd.DataFrame, saannot: Sequence[Kuittaus], *, paiva: dt.date | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Jakaa havainnot aktiivisiin ja kuitattuihin.

    Palauttaa (jäljelle jäävät, kuitatut). Kuitatuissa on lisäsarake
    'kuittaus' perusteluineen. Osumat kertyvät sääntöihin, joten funktion voi
    kutsua joukko kerrallaan; `huomautukset` kokoaa lopuksi huomiot.
    """
    voimassa = [s for s in saannot if not s.vanhentunut(paiva)]
    if havainnot.empty or not voimassa:
        return havainnot, pd.DataFrame()

    kuittaus = pd.Series("", index=havainnot.index, dtype=object)
    for s in voimassa:
        maski = s.osuu(havainnot) & (kuittaus == "")
        osumat = int(maski.sum())
        s.osumat += osumat
        if osumat:
            kuittaus[maski] = s.teksti

    on = kuittaus != ""
    kuitatut = havainnot[on].copy()
    if not kuitatut.empty:
        kuitatut["kuittaus"] = kuittaus[on]
    return havainnot[~on].copy(), kuitatut


def huomautukset(saannot: Iterable[Kuittaus], paiva: dt.date | None = None) -> list[str]:
    """Vanhentuneet ja käyttämättömät säännöt.

    Käyttämätön sääntö tarkoittaa yleensä, että havainto on korjattu – silloin
    sääntö kannattaa poistaa, jottei se vaimenna myöhemmin jotain muuta.
    """
    tulos: list[str] = []
    for s in saannot:
        if s.vanhentunut(paiva):
            p = s.voimassa_asti
            tulos.append(
                f"{s.tunnus}: voimassaolo päättyi {p.day}.{p.month}.{p.year}, "
                "havainnot näkyvät taas raportissa"
            )
        elif not s.osumat:
            tulos.append(f"{s.tunnus}: ei osunut yhteenkään havaintoon (korjaantunut?)")
    return tulos


def yhteenveto(saannot: Iterable[Kuittaus]) -> str:
    osat = [f"{s.tunnus} {s.osumat}" for s in saannot if s.osumat]
    return ", ".join(osat)
