"""Taulujen rooli- ja luokitustulkinta.

PxWebissä taulu on vain joukko muuttujia. Tarkistuksia varten on tiedettävä,
mikä muuttuja on aika, mikä tilastoerä, mikä tunnusluku ja mitkä ovat
luokittelumuuttujia (alue, kuntaryhmä, perhetyyppi, ...). Lisäksi on
tiedettävä, mikä luokittelumuuttujan arvoista on "Yhteensä" ja mitkä arvot
muodostavat toisensa poissulkevan osituksen.

Tämä on kriittistä: esimerkiksi Kuntaryhmityksessä arvot ovat
SSS (yhteensä), 1 (Manner-Suomi), TK1/TK2/TK3 (kaupunkimaiset, taajaan
asutut, maaseutumaiset), 2 (Ahvenanmaa) ja ZZZ (tuntematon). Jos kaikki
ei-yhteensä-arvot laskettaisiin yhteen, Manner-Suomi tulisi laskettua
kahdesti. Siksi ositukset määritellään tasoina.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .pxclient import Metatiedot, Muuttuja

LOG = logging.getLogger("pxtarkistus.inventaario")

# Muuttujakoodit, jotka tulkitaan tilastoeräksi / tunnusluvuksi / ajaksi.
ERA_KOODIT = {"Erä", "Era", "Tilastoerä", "Tiedot"}
TUNNUSLUKU_KOODIT = {"Tunnusluvut", "Tunnusluku", "Tiedot"}
AIKA_KOODIT = {"Verovuosi", "Vuosi", "Kuukausi", "Aika"}

# Yleisimmät "Yhteensä"-koodit PxWeb-luokituksissa.
YHTEENSA_KOODIT = {"SSS", "SS", "S", "000", "00", "0", "Y", "YHT", "KOKOMAA"}
YHTEENSA_TEKSTIT = ("yhteensä", "kaikki", "koko maa", "total", "kaikki yhteensä")

# Tunnusluvut, jotka ovat additiivisia (voidaan laskea yhteen luokkien yli).
ADDITIIVISET_TUNNUSLUVUT = {"Sum", "N"}


@dataclass
class Ositus:
    """Toisensa poissulkeva joukko luokkia, jonka summan pitäisi olla yhteensä.

    `paateltu` kertoo, onko ositus otettu asetuksista (False) vai päätelty
    automaattisesti (True). Päätellyn osituksen poikkeama voi johtua siitä,
    että luokitus on hierarkkinen – silloin havainto raportoidaan lievempänä
    ja kehotetaan määrittelemään tasot asetuksiin.
    """

    nimi: str
    koodit: list[str]
    paateltu: bool = False


@dataclass
class Luokittelu:
    """Luokittelumuuttuja: yhteensä-arvo ja ositukset."""

    muuttuja: Muuttuja
    yhteensa: str | None
    ositukset: list[Ositus] = field(default_factory=list)

    @property
    def koodi(self) -> str:
        return self.muuttuja.koodi


@dataclass
class Roolit:
    """Taulun muuttujien roolit."""

    meta: Metatiedot
    aika: Muuttuja | None
    era: Muuttuja | None
    tunnusluku: Muuttuja | None
    luokittelut: list[Luokittelu]
    # Muuttujat, jotka on kiinnitetty johonkin muuhun kuin Yhteensä-arvoon,
    # esim. Verovelvollisuus = Yleisesti verovelvolliset. Kiinnitetty muuttuja
    # ei ole luokittelu: sitä ei ositeta, se vain rajaa perusjoukon.
    kiinnitetyt: dict[str, str] = field(default_factory=dict)
    kiinnitystekstit: dict[str, str] = field(default_factory=dict)

    @property
    def polku(self) -> str:
        return self.meta.polku

    @property
    def tunniste(self) -> str:
        """Taulun nimi raportilla; kiinnitys näkyy, jotta rajattu taulu erottuu."""
        if not self.kiinnitystekstit:
            return self.polku
        rajaus = ", ".join(f"{k}={v}" for k, v in self.kiinnitystekstit.items())
        return f"{self.polku} [{rajaus}]"

    def luokittelu(self, koodi: str) -> Luokittelu | None:
        for l in self.luokittelut:
            if l.koodi == koodi:
                return l
        return None

    def yhteensa_valinta(self) -> dict[str, list[str]]:
        """Valinta, jossa jokainen luokittelumuuttuja on Yhteensä-arvossaan.

        Jos jollekin muuttujalle ei löydy yhteensä-arvoa, muuttuja jätetään
        pois ja se raportoidaan puutteena.
        """
        valinta: dict[str, list[str]] = {k: [v] for k, v in self.kiinnitetyt.items()}
        for l in self.luokittelut:
            if l.yhteensa is not None:
                valinta[l.koodi] = [l.yhteensa]
        return valinta

    def puuttuvat_yhteensa(self) -> list[str]:
        return [l.koodi for l in self.luokittelut if l.yhteensa is None]


def _nayttaa_eralta(m: Muuttuja) -> bool:
    if m.koodi in ERA_KOODIT:
        return True
    # Verohallinnon erätunnukset ovat muotoa HVT_TULOT_50, EVT_VEROT_10, ...
    osuu = sum(1 for a in m.arvot[:40] if re.match(r"^[A-ZÄÖ]{2,}_[A-ZÄÖ]+_\d+$", a))
    return osuu >= max(3, min(len(m.arvot), 10) // 2)


def _nayttaa_tunnusluvulta(m: Muuttuja) -> bool:
    if m.koodi in TUNNUSLUKU_KOODIT:
        return True
    return "Sum" in m.arvot and "N" in m.arvot


def yhteensa_koodi(m: Muuttuja, saannot: dict[str, Any] | None = None) -> str | None:
    """Etsii muuttujan Yhteensä-arvon: konfiguraatio > arvoteksti > koodi."""
    saannot = saannot or {}
    asetettu = (saannot.get(m.koodi) or {}).get("yhteensa")
    # Sama muuttuja voi käyttää eri tauluissa eri koodia (Tuloluokka: 'SS' tai
    # 'SSS'), joten asetuksista puuttuva koodi ei estä päättelyä.
    if asetettu and asetettu in m.arvot:
        return asetettu

    for koodi, teksti in zip(m.arvot, m.arvotekstit):
        siisti = teksti.strip().lower()
        if siisti in YHTEENSA_TEKSTIT or siisti.startswith("yhteensä"):
            return koodi
    for koodi in m.arvot:
        if koodi.upper() in YHTEENSA_KOODIT:
            return koodi
    # "Tuloluokka yhteensä" -tyyppinen: numeroimaton teksti, jossa sana yhteensä.
    # Numeroitu "2.3 ... yhteensä" on välisumma eikä kelpaa.
    for koodi, teksti in zip(m.arvot, m.arvotekstit):
        if numeropolku(teksti) is None and re.search(r"\byhteensä$", teksti.strip().lower()):
            return koodi
    return None


def numeropolku(teksti: str) -> tuple[int, ...] | None:
    """Poimii arvotekstin alusta hierarkianumeron, esim. '2.3.1 Toinen aste' -> (2,3,1)."""
    osuma = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S", teksti)
    if not osuma:
        return None
    return tuple(int(o) for o in osuma.group(1).split("."))


def _hierarkiset_ositukset(m: Muuttuja, yhteensa: str | None) -> list[Ositus]:
    """Päättelee ositukset arvotekstien hierarkianumeroinnista.

    Verohallinnon luokitukset numeroivat tasot auki arvoteksteissä, esim.
    Koulutusaste: '1. Kuolinpesä', '2. Luonnollinen henkilö',
    '2.3 Tutkinnon suorittaneita täysi-ikäisiä yhteensä', '2.3.1 Toinen aste'.
    Jokaiselta syvyydeltä muodostetaan oma ositus, jossa kunkin haaran syvin
    taso korvaa ylemmän – näin sama joukko ei tule lasketuksi kahdesti.
    """
    polut: dict[str, tuple[int, ...]] = {}
    for koodi, teksti in zip(m.arvot, m.arvotekstit):
        if koodi == yhteensa:
            continue
        p = numeropolku(teksti)
        if p:
            polut[koodi] = p
    if len(polut) < 2:
        return []

    syvin = max(len(p) for p in polut.values())
    if syvin < 2:
        return []

    tulos: list[Ositus] = []
    edellinen: list[str] | None = None
    for taso in range(1, syvin + 1):
        valitut = []
        for koodi, p in polut.items():
            if len(p) > taso:
                continue
            if len(p) < taso and any(
                muu != p and muu[: len(p)] == p for muu in polut.values()
            ):
                continue  # haara jatkuu syvemmälle, käytetään sen lapsia
            valitut.append(koodi)
        if len(valitut) >= 2 and valitut != edellinen:
            tulos.append(Ositus(nimi=f"taso {taso}", koodit=valitut, paateltu=False))
            edellinen = valitut
    return tulos


def ositukset(
    m: Muuttuja, yhteensa: str | None, saannot: dict[str, Any] | None = None
) -> list[Ositus]:
    """Muodostaa osituskandidaatit: konfiguraatiosta, hierarkianumeroinnista tai kaikista."""
    saannot = saannot or {}
    maaritys = (saannot.get(m.koodi) or {}).get("tasot")
    tulos: list[Ositus] = []
    if maaritys:
        for nimi, ehto in maaritys.items():
            varmistamaton = False
            if isinstance(ehto, str):  # säännöllinen lauseke koodille
                koodit = [a for a in m.arvot if a != yhteensa and re.match(ehto, a)]
            elif isinstance(ehto, dict):
                # {"koodi": regex, "teksti": regex, "teksti_ei": regex,
                #  "varmistamaton": true} – kaikki annetut ehdot pätevät yhtä aikaa
                varmistamaton = bool(ehto.get("varmistamaton"))
                koodit = []
                for a, t in zip(m.arvot, m.arvotekstit):
                    if a == yhteensa:
                        continue
                    if "koodi" in ehto and not re.search(ehto["koodi"], a):
                        continue
                    if "teksti" in ehto and not re.search(ehto["teksti"], t, re.IGNORECASE):
                        continue
                    if "teksti_ei" in ehto and re.search(ehto["teksti_ei"], t, re.IGNORECASE):
                        continue
                    koodit.append(a)
            else:  # eksplisiittinen lista
                koodit = [a for a in ehto if a in m.arvot and a != yhteensa]
            if len(koodit) >= 2:
                tulos.append(Ositus(nimi=nimi, koodit=koodit, paateltu=varmistamaton))
    if not tulos:
        tulos = _hierarkiset_ositukset(m, yhteensa)
    if not tulos:
        koodit = [a for a in m.arvot if a != yhteensa]
        if len(koodit) >= 2:
            tulos.append(Ositus(nimi="kaikki luokat", koodit=koodit, paateltu=True))
    return tulos


class KiinnitysVirhe(ValueError):
    """Asetuksissa pyydettyä kiinnitystä ei voi tehdä taulun muuttujiin."""


def tunnista_roolit(
    meta: Metatiedot,
    saannot: dict[str, Any] | None = None,
    kiinnitykset: dict[str, str] | None = None,
) -> Roolit:
    """Päättelee taulun muuttujien roolit.

    `kiinnitykset` = {muuttuja: arvo}, jossa arvo voi olla koodi tai arvoteksti
    (esim. {"Verovelvollisuus": "Yleisesti verovelvolliset"}).
    """
    kiinnitykset = kiinnitykset or {}
    kiinnitetyt: dict[str, str] = {}
    kiinnitystekstit: dict[str, str] = {}
    for muuttuja, arvo in kiinnitykset.items():
        m = meta.muuttuja(muuttuja)
        if m is None:
            raise KiinnitysVirhe(
                f"Taulussa {meta.polku} ei ole muuttujaa '{muuttuja}' "
                f"(muuttujat: {', '.join(meta.koodit)})"
            )
        if arvo in m.arvot:
            koodi = arvo
        else:
            koodi = m.koodi_tekstille(arvo) or next(
                (k for k, t in zip(m.arvot, m.arvotekstit) if t.strip().lower() == str(arvo).strip().lower()),
                None,
            )
        if koodi is None:
            raise KiinnitysVirhe(
                f"Taulun {meta.polku} muuttujalla '{muuttuja}' ei ole arvoa '{arvo}' "
                f"(arvot: {', '.join(m.arvotekstit[:8])})"
            )
        kiinnitetyt[muuttuja] = koodi
        kiinnitystekstit[muuttuja] = m.teksti_koodille(koodi)

    aika = era = tunnusluku = None
    muut: list[Muuttuja] = []
    for m in meta.muuttujat:
        if m.koodi in kiinnitetyt:
            continue
        if aika is None and (m.aika or m.koodi in AIKA_KOODIT):
            aika = m
        elif tunnusluku is None and _nayttaa_tunnusluvulta(m):
            tunnusluku = m
        elif era is None and _nayttaa_eralta(m):
            era = m
        else:
            muut.append(m)

    luokittelut = []
    for m in muut:
        y = yhteensa_koodi(m, saannot)
        luokittelut.append(Luokittelu(muuttuja=m, yhteensa=y, ositukset=ositukset(m, y, saannot)))
    return Roolit(
        meta=meta,
        aika=aika,
        era=era,
        tunnusluku=tunnusluku,
        luokittelut=luokittelut,
        kiinnitetyt=kiinnitetyt,
        kiinnitystekstit=kiinnitystekstit,
    )


def yhteiset_erat(roolit: Sequence[Roolit]) -> list[str]:
    """Kaikissa annetuissa tauluissa esiintyvät tilastoerien koodit."""
    joukot = [set(r.era.arvot) for r in roolit if r.era]
    if not joukot:
        return []
    yhteiset = set.intersection(*joukot)
    eka = next(r.era.arvot for r in roolit if r.era)
    return [a for a in eka if a in yhteiset]


def jaetut_erat(roolit: Sequence[Roolit], vahintaan: int = 2) -> list[str]:
    """Erät, jotka esiintyvät vähintään `vahintaan` taulussa, kattavimmat ensin.

    Leikkaus kaikkien taulujen yli ei kelpaa: esimerkiksi alue-näkymä on jaettu
    tulo-, vähennys- ja verotauluihin, joilla ei ole yhtään yhteistä erää, vaikka
    kukin niistä on vertailukelpoinen laajempien taulujen kanssa.
    """
    laskuri: dict[str, int] = {}
    jarjestys: dict[str, int] = {}
    juokseva = 0
    for r in roolit:
        if not r.era:
            continue
        for koodi in r.era.arvot:
            laskuri[koodi] = laskuri.get(koodi, 0) + 1
            if koodi not in jarjestys:
                jarjestys[koodi] = juokseva
                juokseva += 1
    kelpaavat = [k for k, n in laskuri.items() if n >= vahintaan]
    kelpaavat.sort(key=lambda k: (-laskuri[k], jarjestys[k]))
    return kelpaavat


def era_hierarkia(nimet: dict[str, str]) -> dict[str, list[str]]:
    """Johtaa tilastoerien yläerä–alaerä-suhteet erien nimien numeroinnista.

    '4.1 Palkkatulot yhteensä' on erän '4. Ansiotulot yhteensä' alaerä ja
    '4.1.1' puolestaan sen. Numerointi kertoo rakenteen, mutta EI sitä, onko
    suhde additiivinen: moni alaerä on 'josta'-tyyppinen erittely, joka on jo
    luettu mukaan muualla. Siksi tämä tuottaa vain ehdokkaita, jotka
    tarkistus vahvistaa aikasarjaa vasten ennen kuin niitä valvotaan.
    """
    polut: dict[str, tuple[int, ...]] = {}
    for koodi, teksti in nimet.items():
        p = numeropolku(teksti)
        if p:
            polut[koodi] = p
    hierarkia: dict[str, list[str]] = {}
    for koodi, p in polut.items():
        lapset = [k for k, p2 in polut.items() if len(p2) == len(p) + 1 and p2[: len(p)] == p]
        if lapset:
            hierarkia[koodi] = sorted(lapset, key=lambda k: polut[k])
    return hierarkia


def era_tekstit(roolit: Sequence[Roolit]) -> dict[str, str]:
    """Kokoaa erätunnus -> selväkielinen nimi kaikista tauluista."""
    tekstit: dict[str, str] = {}
    for r in roolit:
        if r.era:
            for koodi, teksti in zip(r.era.arvot, r.era.arvotekstit):
                vanha = tekstit.get(koodi)
                # osa tauluista (postinumero) nimeää erät ilman hierarkianumeroa;
                # numeroitu nimi kantaa rakenteen, joten se voittaa
                if vanha is None or (numeropolku(vanha) is None and numeropolku(teksti)):
                    tekstit[koodi] = teksti
    return tekstit
