"""Tarkistussäännöt.

Viisi perhettä:

A. ristiin       – sama tilastoerä eri näkymissä: koko maan tason arvon
                   (summa, lukumäärä, keskiarvo, mediaani) pitäisi olla sama
                   riippumatta siitä, minkä luokittelun mukaan taulu on julkaistu.
B. osasummat     – taulun sisäinen eheys: osituksen luokkien summa = Yhteensä,
                   ja lukumäärä x keskiarvo ~ kokonaissumma.
C. aikasarja     – aikasarjan eheys (puuttuvat arvot, epäuskottavat hypyt,
                   etumerkin vaihdot) sekä revisiot aiempaan tilannevedokseen
                   verrattuna.
D. erahierarkia  – yläerä vs. alaerien summa, vain aikasarjasta vahvistetuille
                   suhteille.
E. alueet        – kunnan luku aluenäkymässä vs. sen postinumeroalueiden summa.

Jokainen tarkistus palauttaa listan Havainto-olioita. Havainto ei ole
automaattisesti virhe: monelle poikkeamalle on tilastollinen syy (eri
perusjoukko, tietosuojasyistä piilotettu solu, luokituksen muutos). Siksi
havainnot luokitellaan vakavuuden mukaan ja raportille tulee mukaan kaikki
vertailun osatekijät, jotta poikkeaman voi arvioida itse.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from .inventaario import (
    ADDITIIVISET_TUNNUSLUVUT,
    Roolit,
    era_hierarkia,
    era_tekstit,
    jaetut_erat,
)
from .pxclient import PxWebAsiakas, PxWebVirhe

LOG = logging.getLogger("pxtarkistus.tarkistukset")

VAKAVUUDET = ("virhe", "varoitus", "huomio", "ok")

# Tilannevedoksen tulkintaversio. Kasvatetaan aina, kun se miten rajapinnan
# vastaus luetaan luvuiksi muuttuu – esimerkiksi kun '-' alettiin tulkita
# nollaksi puuttuvan sijaan. Vanhalla versiolla kirjoitettua vedosta ei
# verrata uuteen, koska ero johtuisi tulkinnasta eikä julkaistusta luvusta.
TULKINTAVERSIO = 2

# PxWebin tyhjän solun merkinnät
TILASELITTEET = {
    "..": "salassapidon vuoksi puuttuva",
    ".": "tieto ei sovellu",
    "...": "tietoa ei ole saatu",
    "-": "ei havaintoja",
}
TUNNUSLUKUJARJESTYS = {"Sum": 0, "N": 1, "Mean": 2, "P50": 3}


@dataclass
class Toleranssi:
    """Milloin ero on merkittävä."""

    # PxWeb julkaisee eurosummat kokonaisina euroina, eli jokainen solu on
    # pyöristetty erikseen. Kun n solua lasketaan yhteen, summa voi siksi
    # poiketa julkaistusta kokonaissummasta noin 0,5 euroa solua kohden.
    # Tämä on pyöristystä, ei virhe – siksi toleranssi kasvaa summattavien
    # solujen määrän mukana. Lukumääriin (N) pyöristystä ei sovelleta, koska
    # ne ovat tarkkoja kokonaislukuja.
    absoluuttinen: float = 1.0  # euroa tai kappaletta
    suhteellinen: float = 0.0  # osuus vertailuarvosta
    pyoristys_per_solu: float = 0.5  # euroa summattavaa solua kohden

    def raja(self, vertailuarvo: float, solut: int = 1, pyoristyva: bool = True) -> float:
        raja = max(self.absoluuttinen, abs(vertailuarvo) * self.suhteellinen)
        if pyoristyva:
            raja = max(raja, self.pyoristys_per_solu * max(solut, 1))
        return raja

    def merkittava(
        self, ero: float, vertailuarvo: float, solut: int = 1, pyoristyva: bool = True
    ) -> bool:
        return abs(ero) > self.raja(vertailuarvo, solut, pyoristyva)


@dataclass
class Havainto:
    tarkistus: str
    vakavuus: str
    era: str = ""
    era_nimi: str = ""
    verovuosi: str = ""
    tunnusluku: str = ""
    kohde: str = ""  # taulu tai muuttuja, jota havainto koskee
    vertailukohta: str = ""  # mihin verrattiin
    odotettu: float | None = None
    saatu: float | None = None
    ero: float | None = None
    ero_pros: float | None = None
    kuvaus: str = ""

    def rivina(self) -> dict[str, Any]:
        return asdict(self)


def _ero_pros(ero: float | None, vertailuarvo: float | None) -> float | None:
    if ero is None or vertailuarvo in (None, 0) or (vertailuarvo is not None and math.isnan(vertailuarvo)):
        return None
    return 100.0 * ero / vertailuarvo


def _vuosiluettelo(vuodet: Iterable[str]) -> str:
    """['2018','2019','2020','2022'] -> '2018–2020, 2022'."""
    luvut = sorted({int(v) for v in vuodet if str(v).isdigit()})
    muut = sorted({str(v) for v in vuodet if not str(v).isdigit()})
    osat: list[str] = []
    i = 0
    while i < len(luvut):
        j = i
        while j + 1 < len(luvut) and luvut[j + 1] == luvut[j] + 1:
            j += 1
        osat.append(str(luvut[i]) if i == j else f"{luvut[i]}–{luvut[j]}")
        i = j + 1
    return ", ".join(osat + muut)


def _pvm(p: Any, kello: bool = False) -> str:
    """'2026-09-08T13:29:03' -> '8.9.2026' tai '8.9.2026 klo 13.29'."""
    if p is None or (isinstance(p, float) and math.isnan(p)) or p == "":
        return "ei päivitysaikaa"
    try:
        t = pd.Timestamp(p)
    except (ValueError, TypeError):
        return str(p)
    teksti = f"{t.day}.{t.month}.{t.year}"
    return teksti + (f" klo {t.hour}.{t.minute:02d}" if kello else "")


def _tila(v: Any) -> str | None:
    """Solun tilamerkintä merkkijonona tai None (pandas voi muuttaa Nonen NaN:ksi)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    t = str(v).strip()
    return t if t and t.lower() not in ("nan", "none", "<na>") else None


def _paivitysvali(ajat: Iterable[Any]) -> str:
    """Taulujen päivitysajat tiiviisti: '8.9.2026–21.9.2026; 3 ilman päivitysaikaa'."""
    tunnetut: list[pd.Timestamp] = []
    ilman = 0
    for a in ajat:
        if a is None or a == "" or (isinstance(a, float) and math.isnan(a)):
            ilman += 1
            continue
        try:
            tunnetut.append(pd.Timestamp(a))
        except (ValueError, TypeError):
            ilman += 1
    if not tunnetut:
        return "ei päivitysaikaa" if ilman <= 1 else f"ei päivitysaikaa ({ilman} taulua)"
    eka, vika = min(tunnetut), max(tunnetut)
    teksti = _pvm(eka) if eka.date() == vika.date() else f"{_pvm(eka)}–{_pvm(vika)}"
    if ilman:
        teksti += f"; {ilman} {'taulu' if ilman == 1 else 'taulua'} ilman päivitysaikaa"
    return teksti


def _luku(x: float) -> str:
    """1234567.0 -> '1 234 567' (desimaalit vain, jos niitä on)."""
    if float(x).is_integer():
        return f"{x:,.0f}".replace(",", " ")
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _ristiriita_ilman_enemmistoa(
    ryhma: pd.DataFrame, era: str, tunnusluku: str, nimet: dict[str, str], toleranssi: "Toleranssi"
) -> "Havainto | None":
    """Näkymät ovat eri mieltä, eikä mikään arvo ole enemmistönä."""
    pienin, suurin = float(ryhma["arvo"].min()), float(ryhma["arvo"].max())
    ero = suurin - pienin
    if not toleranssi.merkittava(ero, pienin, solut=2, pyoristyva=(tunnusluku != "N")):
        return None
    ryhmat = ryhma.groupby("arvo", sort=True)["taulu"].apply(lambda s: sorted(map(str, s)))
    ajat = dict(zip(ryhma["taulu"].astype(str), ryhma.get("paivitetty", pd.Series([None] * len(ryhma)))))
    osat = []
    for arvo, taulut in ryhmat.items():
        lista = ", ".join(taulut[:4]) + (f" (+{len(taulut) - 4} muuta)" if len(taulut) > 4 else "")
        osat.append(
            f"{lista} = {_luku(float(arvo))} (päivitetty {_paivitysvali(ajat.get(t) for t in taulut)})"
        )
    jako = " vs ".join(str(len(t)) for t in ryhmat)
    pros = _ero_pros(ero, pienin)
    return Havainto(
        tarkistus="ristiin",
        vakavuus="virhe" if abs(pros or 0) > 1 else "varoitus",
        era=era,
        era_nimi=nimet.get(era, ""),
        verovuosi=str(ryhma["verovuosi"].iloc[0]),
        tunnusluku=tunnusluku,
        kohde="; ".join(sorted(map(str, ryhma["taulu"]))),
        vertailukohta=f"ei enemmistöä ({jako})",
        odotettu=pienin,
        saatu=suurin,
        ero=ero,
        ero_pros=pros,
        kuvaus=(
            "Näkymät ovat eri mieltä, eikä mikään arvo ole enemmistönä, joten "
            "poikkeavaa näkymää ei voi päätellä: " + "; ".join(osat) + ". "
            "Vertailuarvo on pienin ja havaittu arvo suurin näistä."
        ),
    )


def _arvot_sanakirjaksi(
    df: pd.DataFrame, era_koodi: str, aika_koodi: str, tunnusluku_koodi: str
) -> dict[tuple[str, str, str], float | None]:
    """(erä, vuosi, tunnusluku) -> arvo."""
    tulos: dict[tuple[str, str, str], float | None] = {}
    if df.empty:
        return tulos
    for rivi in df.itertuples(index=False):
        avain = (
            str(getattr(rivi, era_koodi, "")),
            str(getattr(rivi, aika_koodi, "")),
            str(getattr(rivi, tunnusluku_koodi, "")),
        )
        arvo = getattr(rivi, "arvo", None)
        tulos[avain] = None if arvo is None or (isinstance(arvo, float) and math.isnan(arvo)) else float(arvo)
    return tulos


def _turvallinen_nimi(df: pd.DataFrame, koodi: str) -> str:
    """itertuples muuttaa ei-sallitut merkit; käytetään sen sijaan sarakeindeksiä."""
    return koodi


# --------------------------------------------------------------------- A


def tarkista_ristiin(
    asiakas: PxWebAsiakas,
    roolit: Sequence[Roolit],
    verovuosi: str | Sequence[str],
    *,
    erat: Sequence[str] | None = None,
    tunnusluvut: Sequence[str] = ("Sum", "N"),
    toleranssi: Toleranssi | None = None,
    vertailutaulu: str | None = None,
) -> tuple[list[Havainto], pd.DataFrame]:
    """Vertaa saman tilastoerän koko maan arvoa eri näkymien välillä.

    `verovuosi` voi olla yksi vuosi tai lista vuosia. Koko aikasarjan
    vertaaminen ei maksa juuri mitään, koska aikasarjatarkistus hakee samat
    luvut joka tapauksessa ja välimuisti palvelee ne – ja vasta se paljastaa
    tapaukset, joissa arvo on julkaistu vain osassa näkymiä.
    """
    toleranssi = toleranssi or Toleranssi()
    vuodet = [verovuosi] if isinstance(verovuosi, str) else list(verovuosi)
    roolit = [r for r in roolit if r.era and r.tunnusluku and r.aika]
    if len(roolit) < 2:
        return ([Havainto("ristiin", "huomio", kuvaus="Vertailuun tarvitaan vähintään kaksi taulua")], pd.DataFrame())

    kohde_erat = list(erat) if erat else jaetut_erat(roolit)
    nimet = era_tekstit(roolit)
    havainnot: list[Havainto] = []
    kerays: list[dict[str, Any]] = []

    for r in roolit:
        puuttuvat = r.puuttuvat_yhteensa()
        if puuttuvat:
            havainnot.append(
                Havainto(
                    "ristiin",
                    "varoitus",
                    kohde=r.tunniste,
                    kuvaus=(
                        "Luokittelumuuttujille ei löytynyt Yhteensä-arvoa: "
                        + ", ".join(puuttuvat)
                        + ". Taulu jätettiin vertailun ulkopuolelle."
                    ),
                )
            )
            continue

        omat_erat = [e for e in kohde_erat if e in r.era.arvot]
        omat_tunnusluvut = [t for t in tunnusluvut if t in r.tunnusluku.arvot]
        if not omat_erat or not omat_tunnusluvut:
            continue
        omat_vuodet = [v for v in vuodet if v in r.aika.arvot]
        if not omat_vuodet:
            continue
        valinta: dict[str, Any] = dict(r.yhteensa_valinta())
        valinta[r.era.koodi] = omat_erat
        valinta[r.aika.koodi] = omat_vuodet
        valinta[r.tunnusluku.koodi] = omat_tunnusluvut
        try:
            df = asiakas.hae(r.polku, valinta, meta=r.meta)
        except PxWebVirhe as e:
            havainnot.append(
                Havainto("ristiin", "varoitus", kohde=r.tunniste, kuvaus=f"Datahaku epäonnistui: {e}")
            )
            continue
        if df.empty:
            continue
        paivitetty = asiakas.paivitysaika(r.polku) if hasattr(asiakas, "paivitysaika") else None
        for _, rivi in df.iterrows():
            tila = rivi.get("tila")
            kerays.append(
                {
                    "taulu": r.tunniste,
                    "otsikko": r.meta.otsikko,
                    "era": str(rivi[r.era.koodi]),
                    "era_nimi": str(rivi.get(f"{r.era.koodi}_teksti", "")),
                    "verovuosi": str(rivi[r.aika.koodi]),
                    "tunnusluku": str(rivi[r.tunnusluku.koodi]),
                    "arvo": rivi["arvo"],
                    "tila": _tila(tila),
                    "paivitetty": paivitetty,
                }
            )

    pitka = pd.DataFrame(kerays)
    if pitka.empty:
        havainnot.append(Havainto("ristiin", "varoitus", kuvaus="Vertailtavaa dataa ei saatu haettua"))
        return havainnot, pitka

    for (era, vuosi, tunnusluku), ryhma in pitka.groupby(
        ["era", "verovuosi", "tunnusluku"], sort=False
    ):
        ryhma = ryhma.dropna(subset=["arvo"])
        if len(ryhma) < 2:
            continue
        # Enemmistön arvo on vertailukohta: jos neljä näkymää viidestä on
        # yhtä mieltä, poikkeava on se viides – ei päinvastoin. Tasatilanteessa
        # käytetään asetuksissa nimettyä vertailutaulua, muuten mediaania.
        laskurit = ryhma["arvo"].value_counts()
        paras = int(laskurit.iloc[0])
        ehdokkaat = [a for a, n in laskurit.items() if n == paras]
        if len(ehdokkaat) > 1 and vertailutaulu and (ryhma["taulu"] == vertailutaulu).any():
            perusarvo = float(ryhma.loc[ryhma["taulu"] == vertailutaulu, "arvo"].iloc[0])
        elif len(ehdokkaat) > 1:
            # Ei enemmistöä (esim. kaksi näkymää ja kaksi eri arvoa): poikkeavaa
            # näkymää ei voi päätellä, joten ristiriita on yksi havainto, jossa
            # kaikkien näkymien arvot näkyvät rinnakkain.
            h = _ristiriita_ilman_enemmistoa(ryhma, str(era), str(tunnusluku), nimet, toleranssi)
            if h is not None:
                havainnot.append(h)
            continue
        else:
            perusarvo = float(ehdokkaat[0])
        samaa_mielta = int((ryhma["arvo"] == perusarvo).sum())
        for _, rivi in ryhma.iterrows():
            if float(rivi["arvo"]) == perusarvo:
                continue
            ero = float(rivi["arvo"]) - perusarvo
            # kaksi erikseen pyöristettyä kokonaissummaa -> sallitaan ~1 euro
            if not toleranssi.merkittava(
                ero, perusarvo, solut=2, pyoristyva=(str(tunnusluku) != "N")
            ):
                continue
            havainnot.append(
                Havainto(
                    tarkistus="ristiin",
                    vakavuus="virhe" if abs(_ero_pros(ero, perusarvo) or 0) > 1 else "varoitus",
                    era=era,
                    era_nimi=nimet.get(era, ""),
                    verovuosi=str(rivi["verovuosi"]),
                    tunnusluku=tunnusluku,
                    kohde=str(rivi["taulu"]),
                    vertailukohta=f"{samaa_mielta} muuta näkymää",
                    odotettu=perusarvo,
                    saatu=float(rivi["arvo"]),
                    ero=ero,
                    ero_pros=_ero_pros(ero, perusarvo),
                    kuvaus=(
                        f"Saman erän koko maan arvo poikkeaa {samaa_mielta} muusta "
                        "näkymästä, jotka ovat keskenään yhtä mieltä. Tämän taulun "
                        f"päivitysaika: {_pvm(rivi.get('paivitetty'), kello=True)}; enemmistön "
                        f"taulujen: {_paivitysvali(ryhma.loc[ryhma['arvo'] == perusarvo, 'paivitetty'])}."
                    ),
                )
            )

    # Erät, jotka puuttuvat osasta tauluja, vaikka taulun erävalikoima ne tuntee.
    # Jos arvo puuttuu kaikista tauluista, kyse on rakenteesta eikä puutteesta:
    # esimerkiksi lukumääräerälle ei ole olemassa euromääräistä summaa.
    # Kootaan taulu x erä -riveiksi: sama peitto toistuu tunnusluvuittain ja
    # usein monena vuonna, eikä jokainen solu ansaitse omaa riviään.
    puuttuvat: dict[tuple[str, str], dict[str, Any]] = {}
    for (era, vuosi, tunnusluku), ryhma in pitka.groupby(
        ["era", "verovuosi", "tunnusluku"], sort=False
    ):
        julkaistu = int(ryhma["arvo"].notna().sum())
        if julkaistu == 0:
            continue
        for _, rivi in ryhma[ryhma["arvo"].isna()].iterrows():
            d = puuttuvat.setdefault(
                (str(rivi["taulu"]), str(era)),
                {"vuodet": set(), "tl": set(), "tilat": set(), "muut": 0,
                 "paivitetty": rivi.get("paivitetty")},
            )
            d["vuodet"].add(str(vuosi))
            d["tl"].add(str(tunnusluku))
            if _tila(rivi.get("tila")):
                d["tilat"].add(_tila(rivi.get("tila")))
            d["muut"] = max(d["muut"], julkaistu)
    for (taulu, era), d in puuttuvat.items():
        tilat = sorted(d["tilat"])
        merkinta = (
            ", ".join(f"merkintä '{t}' = {TILASELITTEET.get(t, 'tuntematon merkintä')}" for t in tilat)
            if tilat else "tyhjä solu ilman merkintää"
        )
        huom = (
            " Jos kyse on toissijaisesta peitosta, muissa näkymissä julkaistu summa voi "
            "paljastaa peitetyn solun erotuksena."
            if ".." in tilat else ""
        )
        havainnot.append(
            Havainto(
                tarkistus="ristiin",
                vakavuus="varoitus",
                era=era,
                era_nimi=nimet.get(era, ""),
                verovuosi=_vuosiluettelo(d["vuodet"]),
                tunnusluku=",".join(sorted(d["tl"], key=lambda t: (TUNNUSLUKUJARJESTYS.get(t, 9), t))),
                kohde=taulu,
                vertailukohta=f"julkaistu {d['muut']} muussa näkymässä",
                kuvaus=(
                    f"Koko maan arvo puuttuu tästä näkymästä ({merkinta}), vaikka se on "
                    f"julkaistu enimmillään {d['muut']} muussa näkymässä samalle erälle ja "
                    f"vuodelle.{huom}"
                ),
            )
        )
    return havainnot, pitka


# --------------------------------------------------------------------- B


def tarkista_osasummat(
    asiakas: PxWebAsiakas,
    roolit: Sequence[Roolit],
    verovuosi: str | Sequence[str],
    *,
    erat: Sequence[str] | None = None,
    tunnusluvut: Sequence[str] = ("Sum", "N"),
    toleranssi: Toleranssi | None = None,
    tarkista_keskiarvo: bool = True,
    max_luokat: int = 2000,
) -> tuple[list[Havainto], pd.DataFrame]:
    """Vertaa osituksen luokkien summaa taulun Yhteensä-riviin.

    `verovuosi` voi olla yksi vuosi tai lista vuosia; kaikki vuodet haetaan
    samalla kyselyllä, ja koottavat huomiot (peitetyt luokat, päätellyt
    ositukset) kootaan vuosien yli yhdeksi riviksi.
    """
    toleranssi = toleranssi or Toleranssi()
    vuodet = [verovuosi] if isinstance(verovuosi, str) else [str(v) for v in verovuosi]
    havainnot: list[Havainto] = []
    kerays: list[dict[str, Any]] = []
    nimet = era_tekstit(roolit)
    # (kohde, tunnusluku) -> montako tarkistusta jäi tekemättä piilotettujen
    # luokkien takia; nämä kootaan yhdeksi riviksi erien sijaan
    ohitetut: dict[tuple[str, str, str], int] = {}
    ohitetut_vuodet: dict[tuple[str, str, str], set[str]] = {}
    # päätellyn (varmistamattoman) osituksen poikkeamat kootaan: jos luokitus
    # on päällekkäinen, poikkeaa lähes jokainen erä, eikä se kerro erästä mitään
    epavarmat: dict[tuple[str, str], list[float]] = {}
    epavarmat_vuodet: dict[tuple[str, str], set[str]] = {}

    for r in roolit:
        if not (r.era and r.aika and r.tunnusluku):
            continue
        additiiviset = [t for t in tunnusluvut if t in ADDITIIVISET_TUNNUSLUVUT and t in r.tunnusluku.arvot]
        if not additiiviset:
            continue
        omat_erat = [e for e in (erat or r.era.arvot) if e in r.era.arvot]
        if not omat_erat:
            continue
        omat_vuodet = [v for v in vuodet if v in r.aika.arvot]
        if not omat_vuodet and isinstance(verovuosi, str) and r.aika.arvot:
            # Verovuosikohtainen taulu (esim. vain 2018): tarkistetaan sen oma
            # uusin vuosi, ettei vanhojen vuosien tauluja ohiteta kokonaan.
            omat_vuodet = [sorted(r.aika.arvot)[-1]]
        if not omat_vuodet:
            continue

        for kohde in r.luokittelut:
            if kohde.yhteensa is None or not kohde.ositukset:
                havainnot.append(
                    Havainto(
                        "osasummat",
                        "huomio",
                        kohde=f"{r.tunniste} :: {kohde.koodi}",
                        kuvaus="Muuttujalle ei tunnistettu Yhteensä-arvoa tai ositusta – ei tarkistettu",
                    )
                )
                continue
            # muut luokittelut kiinnitetään yhteensä-arvoonsa
            muut_ok = all(l.yhteensa is not None for l in r.luokittelut if l.koodi != kohde.koodi)
            if not muut_ok:
                havainnot.append(
                    Havainto(
                        "osasummat",
                        "huomio",
                        kohde=f"{r.tunniste} :: {kohde.koodi}",
                        kuvaus="Toiselta luokittelumuuttujalta puuttuu Yhteensä-arvo – ei tarkistettu",
                    )
                )
                continue

            for ositus in kohde.ositukset:
                if len(ositus.koodit) > max_luokat:
                    havainnot.append(
                        Havainto(
                            "osasummat",
                            "huomio",
                            kohde=f"{r.tunniste} :: {kohde.koodi} [{ositus.nimi}]",
                            kuvaus=(
                                f"Ositus ohitettiin: {len(ositus.koodit)} luokkaa ylittää "
                                f"rajan {max_luokat} (asetus osasummat.max_luokat)"
                            ),
                        )
                    )
                    continue
                valinta: dict[str, Any] = {k: [v] for k, v in r.kiinnitetyt.items()}
                valinta.update(
                    {l.koodi: [l.yhteensa] for l in r.luokittelut if l.koodi != kohde.koodi}
                )
                valinta[kohde.koodi] = [kohde.yhteensa] + ositus.koodit
                valinta[r.era.koodi] = omat_erat
                valinta[r.aika.koodi] = omat_vuodet
                valinta[r.tunnusluku.koodi] = additiiviset
                try:
                    df = asiakas.hae(r.polku, valinta, meta=r.meta)
                except PxWebVirhe as e:
                    havainnot.append(
                        Havainto(
                            "osasummat",
                            "varoitus",
                            kohde=f"{r.tunniste} :: {kohde.koodi}",
                            kuvaus=f"Datahaku epäonnistui: {e}",
                        )
                    )
                    continue
                if df.empty:
                    continue

                for (era, vuosi, tunnusluku), ryhma in df.groupby(
                    [r.era.koodi, r.aika.koodi, r.tunnusluku.koodi], sort=False
                ):
                    vuosi = str(vuosi)
                    yht_rivit = ryhma[ryhma[kohde.koodi] == kohde.yhteensa]["arvo"]
                    osat = ryhma[ryhma[kohde.koodi] != kohde.yhteensa]["arvo"]
                    if yht_rivit.empty or yht_rivit.isna().all():
                        continue
                    yhteensa_arvo = float(yht_rivit.iloc[0])
                    puuttuvia = int(osat.isna().sum())
                    # PxWeb erottaa syyn: '..' = salassapidon vuoksi puuttuva,
                    # '.' = tieto ei sovellu. Tieto on olennainen sen
                    # arvioimiseen, onko vertailu ylipäätään mielekäs.
                    tilat = ""
                    if puuttuvia and "tila" in ryhma.columns:
                        koodit = sorted(
                            {
                                str(t)
                                for t in ryhma.loc[
                                    (ryhma[kohde.koodi] != kohde.yhteensa) & ryhma["arvo"].isna(),
                                    "tila",
                                ]
                                if t and str(t) != "nan"
                            }
                        )
                        tilat = ", ".join(koodit)
                    osasumma = float(osat.sum(skipna=True))
                    ero = osasumma - yhteensa_arvo
                    pyoristyva = str(tunnusluku) != "N"
                    kerays.append(
                        {
                            "taulu": r.tunniste,
                            "muuttuja": kohde.koodi,
                            "ositus": ositus.nimi,
                            "era": str(era),
                            "era_nimi": nimet.get(str(era), ""),
                            "verovuosi": vuosi,
                            "tunnusluku": str(tunnusluku),
                            "yhteensa": yhteensa_arvo,
                            "osasumma": osasumma,
                            "ero": ero,
                            "puuttuvia_luokkia": puuttuvia,
                            "puuttuvien_syy": tilat,
                        }
                    )
                    if not toleranssi.merkittava(
                        ero, yhteensa_arvo, solut=len(ositus.koodit), pyoristyva=pyoristyva
                    ):
                        continue
                    if puuttuvia:
                        # osa luokista on piilotettu tietosuojasyistä -> summaa ei
                        # voi verrata. Ei näyttö virheestä, joten ei omaa riviä
                        # jokaisesta erästä vaan yksi koottu havainto lopuksi.
                        avain = (
                            f"{r.tunniste} :: {kohde.koodi} [{ositus.nimi}]",
                            str(tunnusluku),
                            tilat,
                        )
                        ohitetut[avain] = ohitetut.get(avain, 0) + 1
                        ohitetut_vuodet.setdefault(avain, set()).add(vuosi)
                        continue
                    if ositus.paateltu:
                        avain2 = (f"{r.tunniste} :: {kohde.koodi} [{ositus.nimi}]", str(tunnusluku))
                        epavarmat.setdefault(avain2, []).append(
                            osasumma / yhteensa_arvo if yhteensa_arvo else float("nan")
                        )
                        epavarmat_vuodet.setdefault(avain2, set()).add(vuosi)
                        continue
                    vakavuus = "virhe"
                    lisa = ""
                    if ositus.paateltu:
                        lisa += (
                            " – ositus on päätelty automaattisesti; jos luokitus on"
                            f" hierarkkinen, määrittele tasot asetuksiin muuttujalle {kohde.koodi}"
                        )
                    havainnot.append(
                        Havainto(
                            tarkistus="osasummat",
                            vakavuus=vakavuus,
                            era=str(era),
                            era_nimi=nimet.get(str(era), ""),
                            verovuosi=vuosi,
                            tunnusluku=str(tunnusluku),
                            kohde=f"{r.tunniste} :: {kohde.koodi} [{ositus.nimi}]",
                            vertailukohta=f"{kohde.koodi}={kohde.yhteensa}",
                            odotettu=yhteensa_arvo,
                            saatu=osasumma,
                            ero=ero,
                            ero_pros=_ero_pros(ero, yhteensa_arvo),
                            kuvaus="Luokkien summa ei täsmää Yhteensä-arvoon" + lisa,
                        )
                    )

        # N x Mean ~ Sum
        if tarkista_keskiarvo and {"Sum", "N", "Mean"} <= set(r.tunnusluku.arvot):
            valinta = dict(r.yhteensa_valinta())
            if not r.puuttuvat_yhteensa():
                valinta[r.era.koodi] = omat_erat
                valinta[r.aika.koodi] = omat_vuodet
                valinta[r.tunnusluku.koodi] = ["Sum", "N", "Mean"]
                try:
                    df = asiakas.hae(r.polku, valinta, meta=r.meta)
                except PxWebVirhe:
                    df = pd.DataFrame()
                ryhmat = df.groupby([r.era.koodi, r.aika.koodi], sort=False) if not df.empty else []
                for (era, vuosi), ryhma in ryhmat:
                    arvot = {
                        str(rivi[r.tunnusluku.koodi]): rivi["arvo"] for _, rivi in ryhma.iterrows()
                    }
                    s, n, ka = arvot.get("Sum"), arvot.get("N"), arvot.get("Mean")
                    if None in (s, n, ka) or any(pd.isna(x) for x in (s, n, ka)) or not n:
                        continue
                    laskettu = float(n) * float(ka)
                    ero = laskettu - float(s)
                    # keskiarvo on pyöristetty, joten sallitaan n euron heitto
                    if abs(ero) > max(abs(float(n)) * 0.5 + 1.0, abs(float(s)) * 1e-4):
                        havainnot.append(
                            Havainto(
                                tarkistus="osasummat",
                                vakavuus="varoitus",
                                era=str(era),
                                era_nimi=nimet.get(str(era), ""),
                                verovuosi=str(vuosi),
                                tunnusluku="N x Mean vs Sum",
                                kohde=r.tunniste,
                                odotettu=float(s),
                                saatu=laskettu,
                                ero=ero,
                                ero_pros=_ero_pros(ero, float(s)),
                                kuvaus="Lukumäärä x keskiarvo ei vastaa kokonaissummaa",
                            )
                        )

    for (kohde, tunnusluku), suhteet in epavarmat.items():
        kelvot = [x for x in suhteet if not math.isnan(x)]
        mediaani = sorted(kelvot)[len(kelvot) // 2] if kelvot else float("nan")
        havainnot.append(
            Havainto(
                tarkistus="osasummat",
                vakavuus="huomio",
                verovuosi=_vuosiluettelo(epavarmat_vuodet.get((kohde, tunnusluku), vuodet)),
                tunnusluku=tunnusluku,
                kohde=kohde,
                kuvaus=(
                    f"{len(suhteet)} erässä luokkien summa poikkesi Yhteensä-rivistä "
                    f"(luokkien summa / Yhteensä, mediaani {mediaani:.3f}). Ositus on päätelty "
                    "eikä varmistettu: luokitus on todennäköisesti hierarkkinen tai "
                    "päällekkäinen. Määrittele tasot asetuksiin, jos haluat tarkistuksen."
                ),
            )
        )

    for (kohde, tunnusluku, tilat), n in sorted(ohitetut.items(), key=lambda kv: -kv[1]):
        selite = {
            "..": "tieto salassapidon vuoksi puuttuva",
            ".": "tieto ei sovellu",
            "...": "tietoa ei ole saatu",
        }
        syy = (
            " ".join(f"'{t}' = {selite.get(t, 'tuntematon merkintä')};" for t in tilat.split(", "))
            if tilat
            else "tyhjä solu ilman merkintää"
        )
        havainnot.append(
            Havainto(
                tarkistus="osasummat",
                vakavuus="huomio",
                verovuosi=_vuosiluettelo(ohitetut_vuodet.get((kohde, tunnusluku, tilat), vuodet)),
                tunnusluku=tunnusluku,
                kohde=kohde,
                kuvaus=(
                    f"{n} erän osasummaa ei voitu verrata Yhteensä-riviin, koska osa "
                    f"luokista on tyhjiä ({syy.rstrip(';')}). Luokkakohtaiset luvut raportin "
                    "aineistotiedostossa …_data_osasummat.csv.gz."
                ),
            )
        )

    return havainnot, pd.DataFrame(kerays)


# --------------------------------------------------------------------- C


def tarkista_aikasarja(
    asiakas: PxWebAsiakas,
    roolit: Sequence[Roolit],
    *,
    erat: Sequence[str] | None = None,
    tunnusluvut: Sequence[str] = ("Sum", "N"),
    hyppy_raja: float = 0.30,
    vahimmaisarvo: float = 1000.0,
    tilannevedos: Path | str | None = None,
) -> tuple[list[Havainto], pd.DataFrame, dict[str, Any]]:
    """Tarkistaa aikasarjan eheyden ja vertaa aiempaan tilannevedokseen."""
    havainnot: list[Havainto] = []
    kerays: list[dict[str, Any]] = []
    nimet = era_tekstit(roolit)
    uusi_vedos: dict[str, Any] = {}

    vanha_vedos: dict[str, Any] = {}
    vedos_kirjoitettu: str | None = None
    vedos_kelpaa = True
    tunnisteet = {r.tunniste: r.polku for r in roolit}
    if tilannevedos and Path(tilannevedos).exists():
        try:
            raaka = json.loads(Path(tilannevedos).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raaka = {}
            LOG.warning("Tilannevedosta ei voitu lukea: %s", tilannevedos)
        if isinstance(raaka, dict) and raaka.get("versio") == TULKINTAVERSIO:
            vanha_vedos = raaka.get("arvot", {})
            vedos_kirjoitettu = raaka.get("kirjoitettu")
        elif raaka:
            vedos_kelpaa = False
            havainnot.append(
                Havainto(
                    tarkistus="revisio",
                    vakavuus="huomio",
                    kohde=str(tilannevedos),
                    kuvaus=(
                        "Tilannevedos on kirjoitettu eri tulkintaversiolla, joten "
                        "revisiovertailu ohitettiin tältä ajolta. Vedos kirjoitettiin "
                        "uudelleen; seuraava ajo vertaa taas normaalisti."
                    ),
                )
            )

    for r in roolit:
        if not (r.era and r.aika and r.tunnusluku) or r.puuttuvat_yhteensa():
            continue
        omat_erat = [e for e in (erat or r.era.arvot) if e in r.era.arvot]
        omat_tunnusluvut = [t for t in tunnusluvut if t in r.tunnusluku.arvot]
        if not omat_erat or not omat_tunnusluvut:
            continue
        valinta: dict[str, Any] = dict(r.yhteensa_valinta())
        valinta[r.era.koodi] = omat_erat
        valinta[r.aika.koodi] = list(r.aika.arvot)
        valinta[r.tunnusluku.koodi] = omat_tunnusluvut
        try:
            df = asiakas.hae(r.polku, valinta, meta=r.meta)
        except PxWebVirhe as e:
            havainnot.append(
                Havainto("aikasarja", "varoitus", kohde=r.tunniste, kuvaus=f"Datahaku epäonnistui: {e}")
            )
            continue
        if df.empty:
            continue

        for (era, tunnusluku), ryhma in df.groupby([r.era.koodi, r.tunnusluku.koodi], sort=False):
            ryhma = ryhma.sort_values(r.aika.koodi)
            vuodet = [str(v) for v in ryhma[r.aika.koodi]]
            arvot = [None if pd.isna(a) else float(a) for a in ryhma["arvo"]]
            tilat = (
                [None if pd.isna(t) else str(t) for t in ryhma["tila"]]
                if "tila" in ryhma.columns
                else [None] * len(arvot)
            )
            for vuosi, arvo in zip(vuodet, arvot):
                kerays.append(
                    {
                        "taulu": r.tunniste,
                        "era": str(era),
                        "era_nimi": nimet.get(str(era), ""),
                        "verovuosi": vuosi,
                        "tunnusluku": str(tunnusluku),
                        "arvo": arvo,
                    }
                )
                uusi_vedos[f"{r.tunniste}|{era}|{vuosi}|{tunnusluku}"] = arvo

            # puuttuvat arvot sarjan sisällä
            eka = next((i for i, a in enumerate(arvot) if a is not None), None)
            vika = next((len(arvot) - 1 - i for i, a in enumerate(reversed(arvot)) if a is not None), None)
            if eka is not None and vika is not None:
                for i in range(eka, vika + 1):
                    if arvot[i] is None:
                        havainnot.append(
                            Havainto(
                                "aikasarja",
                                "varoitus",
                                era=str(era),
                                era_nimi=nimet.get(str(era), ""),
                                verovuosi=vuodet[i],
                                tunnusluku=str(tunnusluku),
                                kohde=r.tunniste,
                                kuvaus=(
                                    "Arvo puuttuu keskeltä aikasarjaa"
                                    + (
                                        f" (merkintä '{tilat[i]}'"
                                        + (
                                            " = salassapidon vuoksi puuttuva)"
                                            if tilat[i] == ".."
                                            else " = ei sovellu)"
                                            if tilat[i] == "."
                                            else ")"
                                        )
                                        if tilat[i]
                                        else " (ei merkintää)"
                                    )
                                ),
                            )
                        )

            # hypyt ja etumerkin vaihdot
            for i in range(1, len(arvot)):
                a, b = arvot[i - 1], arvot[i]
                if a is None or b is None or abs(a) < vahimmaisarvo:
                    continue
                muutos = (b - a) / abs(a)
                if abs(muutos) > hyppy_raja:
                    kuvaus = (
                        "Sarja putoaa nollaan: ei yhtään havaintoa (merkintä '-') – "
                        "tarkista päättyikö erä"
                        if b == 0 and tilat[i] == "-"
                        else f"Vuosimuutos yli {hyppy_raja:.0%} – tarkista luokitus- tai säädösmuutos"
                    )
                    havainnot.append(
                        Havainto(
                            "aikasarja",
                            "huomio",
                            era=str(era),
                            era_nimi=nimet.get(str(era), ""),
                            verovuosi=vuodet[i],
                            tunnusluku=str(tunnusluku),
                            kohde=r.tunniste,
                            vertailukohta=f"verovuosi {vuodet[i-1]}",
                            odotettu=a,
                            saatu=b,
                            ero=b - a,
                            ero_pros=100.0 * muutos,
                            kuvaus=kuvaus,
                        )
                    )
                if a * b < 0:
                    havainnot.append(
                        Havainto(
                            "aikasarja",
                            "varoitus",
                            era=str(era),
                            era_nimi=nimet.get(str(era), ""),
                            verovuosi=vuodet[i],
                            tunnusluku=str(tunnusluku),
                            kohde=r.tunniste,
                            odotettu=a,
                            saatu=b,
                            ero=b - a,
                            kuvaus="Arvon etumerkki vaihtui edelliseen vuoteen nähden",
                        )
                    )

    # revisiot
    if vanha_vedos:
        for avain, uusi in uusi_vedos.items():
            if avain not in vanha_vedos:
                continue
            vanha = vanha_vedos[avain]
            if vanha is None and uusi is None:
                continue
            if vanha is None or uusi is None or abs(float(uusi) - float(vanha)) > 0.5:
                taulu, era, vuosi, tunnusluku = avain.split("|")
                ero = None
                if vanha is not None and uusi is not None:
                    ero = float(uusi) - float(vanha)
                polku = tunnisteet.get(taulu, taulu)
                paiv = asiakas.paivitysaika(polku) if hasattr(asiakas, "paivitysaika") else None
                if not paiv:
                    ajoitus = (
                        "Taulun listauksessa ei ole päivitysaikaa, joten muutoshetkeä ei "
                        "voi todentaa rajapinnasta."
                    )
                elif vedos_kirjoitettu and pd.Timestamp(paiv) > pd.Timestamp(vedos_kirjoitettu):
                    ajoitus = f"Taulu on päivitetty {_pvm(paiv, kello=True)}, eli vedoksen jälkeen."
                else:
                    ajoitus = (
                        f"Taulun päivitysaika {_pvm(paiv, kello=True)} on ennen vedosta – "
                        "vedoksen arvo on voinut tulla vanhasta välimuistista."
                    )
                havainnot.append(
                    Havainto(
                        tarkistus="revisio",
                        vakavuus="huomio",
                        era=era,
                        era_nimi=nimet.get(era, ""),
                        verovuosi=vuosi,
                        tunnusluku=tunnusluku,
                        kohde=taulu,
                        vertailukohta=(
                            f"tilannevedos {_pvm(vedos_kirjoitettu, kello=True)}"
                            if vedos_kirjoitettu else "edellinen tilannevedos"
                        ),
                        odotettu=None if vanha is None else float(vanha),
                        saatu=None if uusi is None else float(uusi),
                        ero=ero,
                        ero_pros=_ero_pros(ero, None if vanha is None else float(vanha)),
                        kuvaus=f"Aiemmin julkaistu arvo on muuttunut. {ajoitus}",
                    )
                )

    if tilannevedos:
        Path(tilannevedos).parent.mkdir(parents=True, exist_ok=True)
        Path(tilannevedos).write_text(
            json.dumps(
                {
                    "versio": TULKINTAVERSIO,
                    "kirjoitettu": pd.Timestamp.now().isoformat(timespec="seconds"),
                    "arvot": uusi_vedos,
                },
                ensure_ascii=False,
                indent=0,
            ),
            encoding="utf-8",
        )

    return _yhdista_samat(havainnot), pd.DataFrame(kerays), uusi_vedos


def _yhdista_samat(havainnot: list[Havainto]) -> list[Havainto]:
    """Kokoaa saman sarjan identtiset havainnot yhdeksi riviksi.

    Sama tilastoerä esiintyy monessa näkymässä, ja koko maan aikasarja on
    niissä sama. Ilman tätä yksi vuosihyppy raportoitaisiin viidesti.
    Näkymien yhtäpitävyyden todistaa ristiintarkistus, ei toisto.
    """
    koottu: dict[tuple, Havainto] = {}
    lisat: dict[tuple, int] = {}
    for h in havainnot:
        avain = (h.tarkistus, h.era, h.verovuosi, h.tunnusluku, h.odotettu, h.saatu, h.kuvaus)
        if avain in koottu:
            lisat[avain] = lisat.get(avain, 0) + 1
        else:
            koottu[avain] = h
    for avain, n in lisat.items():
        h = koottu[avain]
        h.kohde = f"{h.kohde} (+ {n} muuta taulua, sama sarja)"
    return list(koottu.values())


# --------------------------------------------------------------------- E


def valitse_aluetaulut(
    erat: Sequence[str], vuosi: str, alue_taulut: Sequence[Roolit]
) -> list[tuple[Roolit, list[str]]]:
    """Jokaiselle erälle ensimmäinen aluetaulu, jossa on sekä erä että vuosi.

    Aluekansiossa on sekä kaikkien vuosien tauluja että verovuosikohtaisia
    tauluja; erä pitää hakea taulusta, jossa kyseinen vuosi todella on.
    """
    jako: dict[int, tuple[Roolit, list[str]]] = {}
    for e in erat:
        for a in alue_taulut:
            if e in a.era.arvot and vuosi in a.aika.arvot:
                jako.setdefault(id(a), (a, []))[1].append(e)
                break
    return list(jako.values())


def tarkista_alueet(
    asiakas: PxWebAsiakas,
    roolit: Sequence[Roolit],
    *,
    erat: Sequence[str] | None = None,
    tunnusluvut: Sequence[str] = ("Sum", "N"),
    toleranssi: Toleranssi | None = None,
    alue_muuttuja: str = "Alue",
    posti_muuttuja: str = "Kuntapostinumero",
    kunta_malli: str = r"^[0-9]{3}$",
    systemaattinen_raja: int = 30,
    vain_perustaulut: bool = True,
) -> tuple[list[Havainto], pd.DataFrame]:
    """Vertaa kunnan lukua aluenäkymässä sen postinumeroalueiden summaan.

    Aluenäkymä julkaisee kunnan luvun suoraan (Alue = '005'), postinumeronäkymä
    kunnan postinumeroalueittain (Kuntapostinumero = '005_62710', '005_ZZZPL',
    '005_MUUKN'). Saman kunnan luvun pitää olla sama kumpaa reittiä tahansa.
    Kuntanumerointi on molemmissa sama, joten kytkentä tehdään koodin
    alkuosasta. Jos kunnan jokin postinumeroalue on peitetty ('..'), kuntaa ei
    voi verrata ja se ohitetaan.

    Postinumerotaulut ovat palvelimen raskaimpia, joten kukin haetaan yhdellä
    kyselyllä kaikille erille. `vain_perustaulut` rajaa vertailun tauluihin,
    joissa ei ole muita luokittelumuuttujia (esim. sukupuolittaisen taulun
    Yhteensä on jo tarkistettu näkymävertailussa).
    """
    import re as _re

    toleranssi = toleranssi or Toleranssi()
    havainnot: list[Havainto] = []
    kerays: list[dict[str, Any]] = []
    nimet = era_tekstit(roolit)

    def kelpaa(r: Roolit) -> bool:
        return bool(r.era and r.aika and r.tunnusluku) and not r.puuttuvat_yhteensa()

    alue_taulut = [r for r in roolit if kelpaa(r) and r.luokittelu(alue_muuttuja)]
    posti_taulut = [
        r for r in roolit
        if kelpaa(r) and r.luokittelu(posti_muuttuja)
        and (not vain_perustaulut or len(r.luokittelut) == 1)
    ]
    if not alue_taulut or not posti_taulut:
        return havainnot, pd.DataFrame()

    ohitetut: dict[tuple[str, str], int] = {}
    for p in posti_taulut:
        pl = p.luokittelu(posti_muuttuja)
        posti_koodit = [k for k in pl.muuttuja.arvot if k != pl.yhteensa and "_" in k]
        if not posti_koodit:
            continue
        for vuosi in p.aika.arvot:
            p_erat = [e for e in p.era.arvot if not erat or e in erat]
            jako = valitse_aluetaulut(p_erat, vuosi, alue_taulut)
            if not jako:
                continue
            tl = [t for t in tunnusluvut if t in ADDITIIVISET_TUNNUSLUVUT and t in p.tunnusluku.arvot]
            kaikki_erat = [e for _, es in jako for e in es]

            # yksi kysely postinumerotaulusta kaikille erille
            vp: dict[str, Any] = {k: v for k, v in p.yhteensa_valinta().items() if k != posti_muuttuja}
            vp.update({posti_muuttuja: posti_koodit, p.era.koodi: kaikki_erat,
                       p.aika.koodi: [vuosi], p.tunnusluku.koodi: tl})
            try:
                dfp = asiakas.hae(p.polku, vp, meta=p.meta)
            except PxWebVirhe as e:
                havainnot.append(Havainto("alueet", "varoitus", kohde=p.tunniste,
                                          kuvaus=f"Datahaku epäonnistui: {e}"))
                continue
            if dfp.empty:
                continue
            dfp = dfp.assign(kunta=dfp[posti_muuttuja].astype(str).str.split("_").str[0])
            posti = (
                dfp.groupby(["kunta", p.era.koodi, p.tunnusluku.koodi], sort=False)["arvo"]
                .agg(summa=lambda s: float(s.sum(skipna=True)),
                     puuttuvia=lambda s: int(s.isna().sum()), soluja="size")
                .reset_index()
                .rename(columns={p.era.koodi: "era", p.tunnusluku.koodi: "tunnusluku"})
            )

            for a, a_erat in jako:
                al = a.luokittelu(alue_muuttuja)
                kunnat = [k for k in al.muuttuja.arvot if k != al.yhteensa and _re.match(kunta_malli, k)]
                a_tl = [t for t in tl if t in a.tunnusluku.arvot]
                if not kunnat or not a_tl:
                    continue
                va: dict[str, Any] = {k: v for k, v in a.yhteensa_valinta().items() if k != alue_muuttuja}
                va.update({alue_muuttuja: kunnat, a.era.koodi: a_erat,
                           a.aika.koodi: [vuosi], a.tunnusluku.koodi: a_tl})
                try:
                    dfa = asiakas.hae(a.polku, va, meta=a.meta)
                except PxWebVirhe as e:
                    havainnot.append(Havainto("alueet", "varoitus", kohde=a.tunniste,
                                              kuvaus=f"Datahaku epäonnistui: {e}"))
                    continue
                alue = dfa[[alue_muuttuja, a.era.koodi, a.tunnusluku.koodi, "arvo"]].rename(
                    columns={alue_muuttuja: "kunta", a.era.koodi: "era",
                             a.tunnusluku.koodi: "tunnusluku", "arvo": "alueen_arvo"}
                )
                yhd = alue.merge(posti, on=["kunta", "era", "tunnusluku"], how="left")
                pari = f"{p.tunniste} vs {a.tunniste}"

                poikkeamat: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for rivi in yhd.itertuples(index=False):
                    if pd.isna(rivi.soluja):
                        if not pd.isna(rivi.alueen_arvo) and rivi.alueen_arvo:
                            avain = (pari, "kunnalla ei postinumeroalueita postinumeronäkymässä")
                            ohitetut[avain] = ohitetut.get(avain, 0) + 1
                        continue
                    if pd.isna(rivi.alueen_arvo) or rivi.puuttuvia:
                        avain = (pari, "kunnan luku tai jokin sen postinumeroalue on peitetty")
                        ohitetut[avain] = ohitetut.get(avain, 0) + 1
                        continue
                    kunnan_nimi = al.muuttuja.teksti_koodille(str(rivi.kunta))
                    ero = float(rivi.summa) - float(rivi.alueen_arvo)
                    kerays.append({
                        "pari": pari, "kunta": rivi.kunta, "kunnan_nimi": kunnan_nimi,
                        "era": rivi.era, "era_nimi": nimet.get(str(rivi.era), ""),
                        "verovuosi": vuosi, "tunnusluku": rivi.tunnusluku,
                        "alueen_arvo": float(rivi.alueen_arvo),
                        "postinumeroiden_summa": float(rivi.summa), "ero": ero,
                    })
                    # pyöristysvara: jokainen postinumeroalue + kunnan oma luku
                    if not toleranssi.merkittava(
                        ero, float(rivi.alueen_arvo), solut=int(rivi.soluja) + 1,
                        pyoristyva=str(rivi.tunnusluku) != "N",
                    ):
                        continue
                    poikkeamat.setdefault((str(rivi.era), str(rivi.tunnusluku)), []).append(
                        {"kunta": str(rivi.kunta), "nimi": kunnan_nimi, "alue": float(rivi.alueen_arvo),
                         "summa": float(rivi.summa), "ero": ero}
                    )

                for (era, tunnus), lista in poikkeamat.items():
                    if len(lista) > systemaattinen_raja:
                        havainnot.append(
                            Havainto(
                                tarkistus="alueet", vakavuus="varoitus", era=era,
                                era_nimi=nimet.get(era, ""), verovuosi=vuosi, tunnusluku=tunnus,
                                kohde=pari, vertailukohta=f"{len(lista)} kuntaa",
                                ero=sum(x["ero"] for x in lista),
                                kuvaus=(
                                    f"Postinumeroalueiden summa poikkeaa kunnan luvusta {len(lista)} "
                                    "kunnassa. Näin laaja ero viittaa näkymien erilaiseen rajaukseen "
                                    "eikä yksittäisen kunnan virheeseen. Kuntakohtaiset erot "
                                    "raportin aineistotiedostossa …_data_alueet.csv.gz."
                                ),
                            )
                        )
                        continue
                    for x in lista:
                        havainnot.append(
                            Havainto(
                                tarkistus="alueet", vakavuus="virhe", era=era,
                                era_nimi=nimet.get(era, ""), verovuosi=vuosi, tunnusluku=tunnus,
                                kohde=f"{x['kunta']} {x['nimi']}: {pari}",
                                vertailukohta="kunnan luku aluenäkymässä",
                                odotettu=x["alue"], saatu=x["summa"], ero=x["ero"],
                                ero_pros=_ero_pros(x["ero"], x["alue"]),
                                kuvaus="Kunnan postinumeroalueiden summa ei täsmää kunnan lukuun aluenäkymässä",
                            )
                        )

    for (pari, syy), n in ohitetut.items():
        havainnot.append(Havainto("alueet", "huomio", kohde=pari, kuvaus=f"{n} vertailua ohitettiin: {syy}"))
    return havainnot, pd.DataFrame(kerays)


# --------------------------------------------------------------------- D


def tarkista_erahierarkia(
    asiakas: PxWebAsiakas,
    roolit: Sequence[Roolit],
    verovuosi: str,
    *,
    erat: Sequence[str] | None = None,
    toleranssi: Toleranssi | None = None,
    saannot_tiedosto: Path | str | None = None,
    vahimmaisvuodet: int = 3,
    yksittaisen_vahimmaisvuodet: int = 5,
) -> tuple[list[Havainto], pd.DataFrame]:
    """Vertaa yläerää alaeriensä summaan – vain vahvistetuille suhteille.

    Erien nimien numerointi kertoo, mitkä erät ovat toistensa alaeriä, mutta ei
    sitä onko suhde additiivinen: moni alaerä on 'josta'-erittely. Siksi jokainen
    ehdokassuhde kalibroidaan historiaa vasten: se otetaan valvontaan vasta kun
    yläerä on ollut alaeriensä summa jokaisena vuonna, jolta molemmat on
    julkaistu (vähintään `vahimmaisvuodet` vuotta), tarkasteltava verovuosi pois
    lukien. Vahvistetut suhteet tallennetaan sääntötiedostoon, jota voi myös
    korjata käsin kuvaustiedostojen laskentasääntöjen perusteella.

    Suhde, joka on täsmännyt jokaisena vuonna yhtä aiempaa vuotta lukuun
    ottamatta (vähintään `yksittaisen_vahimmaisvuodet` vertailukelpoista
    vuotta), ei vahvistu, mutta sen ainoa poikkeava vuosi raportoidaan
    varoituksena: se on todennäköisemmin kyseisen vuoden virhe kuin
    ei-additiivinen suhde.

    Tarkistus tehdään vain eurosummille. Lukumäärät eivät ole additiivisia:
    sama henkilö voi kuulua useaan alaerään, jolloin summa ylittää yläerän.
    """
    toleranssi = toleranssi or Toleranssi()
    havainnot: list[Havainto] = []
    nimet = era_tekstit(roolit)
    hierarkia = era_hierarkia(nimet)
    if erat:
        sallitut = set(erat)
        hierarkia = {
            y: [l for l in ls if l in sallitut]
            for y, ls in hierarkia.items()
            if y in sallitut and all(l in sallitut for l in ls)
        }
        hierarkia = {y: ls for y, ls in hierarkia.items() if ls}
    if not hierarkia:
        return [], pd.DataFrame()

    # jokaiselle erälle yksi lähdetaulu; näkymien yhtäpitävyys tarkistetaan erikseen
    lahde: dict[str, Roolit] = {}
    for r in roolit:
        if not (r.era and r.aika and r.tunnusluku) or r.puuttuvat_yhteensa():
            continue
        if "Sum" not in r.tunnusluku.arvot:
            continue
        for koodi in r.era.arvot:
            lahde.setdefault(koodi, r)

    tarvittavat: dict[str, set[str]] = {}
    for yla, lapset in hierarkia.items():
        for koodi in [yla, *lapset]:
            r = lahde.get(koodi)
            if r:
                tarvittavat.setdefault(r.polku, set()).add(koodi)

    arvot: dict[tuple[str, str], float | None] = {}
    for polku, koodit in tarvittavat.items():
        r = next(x for x in roolit if x.polku == polku)
        valinta: dict[str, Any] = dict(r.yhteensa_valinta())
        valinta[r.era.koodi] = sorted(koodit)
        valinta[r.aika.koodi] = list(r.aika.arvot)
        valinta[r.tunnusluku.koodi] = ["Sum"]
        try:
            df = asiakas.hae(polku, valinta, meta=r.meta)
        except PxWebVirhe as e:
            havainnot.append(
                Havainto("erahierarkia", "varoitus", kohde=polku, kuvaus=f"Datahaku epäonnistui: {e}")
            )
            continue
        for _, rivi in df.iterrows():
            a = rivi["arvo"]
            arvot[(str(rivi[r.era.koodi]), str(rivi[r.aika.koodi]))] = (
                None if pd.isna(a) else float(a)
            )

    vanhat: dict[str, Any] = {}
    if saannot_tiedosto and Path(saannot_tiedosto).exists():
        try:
            vanhat = json.loads(Path(saannot_tiedosto).read_text(encoding="utf-8")).get(
                "suhteet", {}
            )
        except json.JSONDecodeError:
            LOG.warning("Sääntötiedostoa ei voitu lukea: %s", saannot_tiedosto)

    vuodet = sorted({v for _, v in arvot})
    kerays: list[dict[str, Any]] = []
    vahvistetut: dict[str, Any] = {}

    for yla, lapset in hierarkia.items():
        def vertaa(vuosi: str) -> tuple[float, float, float] | None:
            y = arvot.get((yla, vuosi))
            ls = [arvot.get((l, vuosi)) for l in lapset]
            if y is None or any(x is None for x in ls):
                return None
            if abs(y) < 1:  # triviaali nollavuosi ei kerro additiivisuudesta
                return None
            s = float(sum(ls))  # type: ignore[arg-type]
            return y, s, s - y

        kalibrointi = [(v, vertaa(v)) for v in vuodet if v != verovuosi]
        kelpaavat = [(v, t) for v, t in kalibrointi if t]
        osuu = [
            (v, t)
            for v, t in kelpaavat
            if not toleranssi.merkittava(t[2], t[0], solut=len(lapset))
        ]
        aiemmin_vahvistettu = yla in vanhat and vanhat[yla].get("lapset") == lapset
        additiivinen = aiemmin_vahvistettu or (
            len(kelpaavat) >= vahimmaisvuodet and len(osuu) == len(kelpaavat)
        )
        # kaikki vuodet (myös tarkasteltava): täsmääkö suhde yhtä vuotta lukuun ottamatta?
        kaikki_kelpaavat = [(v, t) for v, t in ((v, vertaa(v)) for v in vuodet) if t]
        poikkeavat = [
            (v, t) for v, t in kaikki_kelpaavat
            if toleranssi.merkittava(t[2], t[0], solut=len(lapset))
        ]
        yksi_poikkeama = (
            not additiivinen
            and len(kaikki_kelpaavat) >= yksittaisen_vahimmaisvuodet
            and len(poikkeavat) == 1
        )

        kerays.append(
            {
                "ylaera": yla,
                "ylaeran_nimi": nimet.get(yla, ""),
                "alaeria": len(lapset),
                "alaerat": ", ".join(lapset),
                "vuosia_vertailussa": len(kelpaavat),
                "vuosia_tasmaa": len(osuu),
                "tila": (
                    "vahvistettu" if additiivinen
                    else f"yksi poikkeava vuosi ({poikkeavat[0][0]})" if yksi_poikkeama
                    else "ei additiivinen"
                ),
            }
        )
        if yksi_poikkeama:
            vuosi, (ylaarvo, summa, ero) = poikkeavat[0]
            muut = len(kaikki_kelpaavat) - 1
            havainnot.append(
                Havainto(
                    tarkistus="erahierarkia",
                    vakavuus="varoitus",
                    era=yla,
                    era_nimi=nimet.get(yla, ""),
                    verovuosi=vuosi,
                    tunnusluku="Sum",
                    kohde=lahde[yla].polku if yla in lahde else "",
                    vertailukohta=f"{len(lapset)} alaerän summa",
                    odotettu=ylaarvo,
                    saatu=summa,
                    ero=ero,
                    ero_pros=_ero_pros(ero, ylaarvo),
                    kuvaus=(
                        f"Alaerien summa poikkeaa yläerästä vain tänä vuonna; muina {muut} "
                        "vuonna suhde on täsmännyt. Todennäköisesti tämän vuoden virhe – "
                        "ellei vuodelle ole ollut erillistä laskentasääntöä."
                    ),
                )
            )
            continue
        if not additiivinen:
            continue

        vahvistetut[yla] = {
            "nimi": nimet.get(yla, ""),
            "lapset": lapset,
            "vahvistettu_vuosilta": [v for v, _ in kelpaavat],
        }
        for vuosi in vuodet:
            t = vertaa(vuosi)
            if not t:
                continue
            ylaarvo, summa, ero = t
            if not toleranssi.merkittava(ero, ylaarvo, solut=len(lapset)):
                continue
            havainnot.append(
                Havainto(
                    tarkistus="erahierarkia",
                    vakavuus="virhe" if vuosi == verovuosi else "varoitus",
                    era=yla,
                    era_nimi=nimet.get(yla, ""),
                    verovuosi=vuosi,
                    tunnusluku="Sum",
                    kohde=lahde[yla].polku if yla in lahde else "",
                    vertailukohta=f"{len(lapset)} alaerän summa",
                    odotettu=ylaarvo,
                    saatu=summa,
                    ero=ero,
                    ero_pros=_ero_pros(ero, ylaarvo),
                    kuvaus=(
                        "Alaerien summa ei täsmää yläerään, vaikka suhde on pitänyt "
                        f"{len(osuu)} muuna vuonna"
                    ),
                )
            )

    if saannot_tiedosto:
        Path(saannot_tiedosto).parent.mkdir(parents=True, exist_ok=True)
        Path(saannot_tiedosto).write_text(
            json.dumps(
                {
                    "kuvaus": (
                        "Aikasarjaa vasten vahvistetut yläerä–alaerä-suhteet. "
                        "Voit lisätä tai poistaa suhteita käsin; tiedostossa olevia "
                        "valvotaan jatkossa ilman uutta kalibrointia."
                    ),
                    "paivitetty": pd.Timestamp.now().isoformat(timespec="seconds"),
                    "suhteet": vahvistetut,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    return havainnot, pd.DataFrame(kerays)


def havainnot_kehykseksi(havainnot: Iterable[Havainto]) -> pd.DataFrame:
    rivit = [h.rivina() for h in havainnot]
    df = pd.DataFrame(rivit)
    if df.empty:
        return df
    jarjestys = {v: i for i, v in enumerate(VAKAVUUDET)}
    df["_j"] = df["vakavuus"].map(jarjestys).fillna(99)
    df = df.sort_values(["_j", "tarkistus", "era"]).drop(columns="_j").reset_index(drop=True)
    return df
