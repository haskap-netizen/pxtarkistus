"""Kahden ajon havaintojen vertailu: mikä on uutta, mikä korjaantunut.

Tarkistus tuottaa joka ajossa kymmeniä tuhansia rivejä, joista valtaosa on
samoja kuin edellisellä kerralla. Olennainen kysymys on, mikä muuttui: tuliko
uusia poikkeamia, katosiko vanhoja (korjaus julkaisuun) ja kasvoiko jonkin
eron suuruus.

Havainnot tunnistetaan avaimella (joukko, tarkistus, erä, verovuosi,
tunnusluku, kohde). Sama avain kahdessa raportissa = sama havainto, vaikka
luvut olisivat muuttuneet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .raportti import SARAKENIMET, _avain, _muotoile_taulukko

AVAINSARAKKEET = ("joukko", "tarkistus", "era", "verovuosi", "tunnusluku", "kohde")
# Ero, jota pienempää muutosta ei raportoida muuttuneena (pyöristys).
MUUTOSRAJA = 0.5


def lue_havainnot(tiedosto: Path | str) -> pd.DataFrame:
    """Lukee raportin Havainnot-välilehden ja palauttaa sisäiset sarakenimet."""
    tiedosto = Path(tiedosto)
    if not tiedosto.exists():
        raise FileNotFoundError(f"Raporttia ei löydy: {tiedosto}")
    kaannetty = {v: k for k, v in SARAKENIMET.items()}
    try:
        df = pd.read_excel(tiedosto, sheet_name="Havainnot")
    except ValueError as e:  # välilehteä ei ole
        raise ValueError(f"{tiedosto.name}: Havainnot-välilehteä ei löydy") from e
    df = df.rename(columns=kaannetty)
    if "tarkistus" not in df.columns:
        raise ValueError(f"{tiedosto.name}: Havainnot-välilehdellä ei ole havaintorivejä")
    if "ero_pros" in df.columns:
        # Excel tallentaa prosentin murtolukuna; sama korjaus kuin tiivistyksessä
        df["ero_pros"] = pd.to_numeric(df["ero_pros"], errors="coerce") * 100
    return df


def _avaimet(df: pd.DataFrame) -> pd.Series:
    osat = []
    for s in AVAINSARAKKEET:
        sarake = df[s] if s in df.columns else pd.Series([""] * len(df), index=df.index)
        osat.append(sarake.map(_avain))
    return pd.Series(["|".join(o) for o in zip(*osat)], index=df.index) if osat else pd.Series(dtype=str)


def vertaa(vanha: pd.DataFrame, uusi: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Palauttaa uudet, poistuneet ja määrältään muuttuneet havainnot."""
    va, ua = _avaimet(vanha), _avaimet(uusi)
    vanhat, uudet_avaimet = set(va), set(ua)

    tuttu = pd.Series([a in vanhat for a in ua], index=uusi.index)
    uudet = uusi[~tuttu].copy()
    poistuneet = vanha[[a not in uudet_avaimet for a in va]].copy()

    yhteiset = uusi[tuttu].copy()
    muuttuneet = pd.DataFrame()
    if not yhteiset.empty and "ero" in yhteiset.columns and "ero" in vanha.columns:
        vanhat_erot = dict(zip(va, pd.to_numeric(vanha["ero"], errors="coerce")))
        yhteiset["ero_aiemmin"] = [vanhat_erot.get(a) for a in ua[tuttu]]
        nyt = pd.to_numeric(yhteiset["ero"], errors="coerce")
        ennen = pd.to_numeric(yhteiset["ero_aiemmin"], errors="coerce")
        muutos = (nyt - ennen).abs()
        muuttuneet = yhteiset[muutos > MUUTOSRAJA].copy()
        if not muuttuneet.empty:
            muuttuneet["eron_muutos"] = (
                pd.to_numeric(muuttuneet["ero"], errors="coerce")
                - pd.to_numeric(muuttuneet["ero_aiemmin"], errors="coerce")
            )
    return {"uudet": uudet, "poistuneet": poistuneet, "muuttuneet": muuttuneet}


def kirjoita_vertailu(
    tiedosto: Path | str, tulokset: Mapping[str, pd.DataFrame], meta: Mapping[str, Any]
) -> Path:
    """Kirjoittaa vertailun Exceliksi (yksi välilehti per muutoslaji)."""
    tiedosto = Path(tiedosto)
    tiedosto.parent.mkdir(parents=True, exist_ok=True)
    otsikot = {"uudet": "Uudet", "poistuneet": "Poistuneet", "muuttuneet": "Muuttuneet"}
    with pd.ExcelWriter(tiedosto, engine="openpyxl") as kirjoittaja:
        pd.DataFrame([{"Tieto": k, "Arvo": v} for k, v in meta.items()]).to_excel(
            kirjoittaja, sheet_name="Yhteenveto", index=False
        )
        muotoiltavat: list[tuple[str, pd.DataFrame]] = []
        for laji, nimi in otsikot.items():
            df = tulokset.get(laji)
            if df is None or df.empty:
                pd.DataFrame([{"Tulos": f"Ei {nimi.lower()} havaintoja"}]).to_excel(
                    kirjoittaja, sheet_name=nimi, index=False
                )
            else:
                osa = df.head(200_000)
                osa.rename(columns=SARAKENIMET).to_excel(kirjoittaja, sheet_name=nimi, index=False)
                muotoiltavat.append((nimi, osa))
        kirja = kirjoittaja.book
        for nimi, osa in muotoiltavat:
            _muotoile_taulukko(
                kirja[nimi], osa, "vakavuus" if "vakavuus" in osa.columns else None
            )
    return tiedosto


def konsolivertailu(tulokset: Mapping[str, pd.DataFrame], nayta: int = 10) -> str:
    """Tiivis tekstiyhteenveto muutoksista."""
    rivit = ["=" * 78, "PXTARKISTUS – ajojen vertailu", "=" * 78]
    for laji, otsikko in (
        ("uudet", "Uusia havaintoja"),
        ("poistuneet", "Poistuneita (korjattu tai ei enää julkaistu)"),
        ("muuttuneet", "Eron suuruus muuttunut"),
    ):
        df = tulokset.get(laji, pd.DataFrame())
        rivit.append(f"  {otsikko:<46} {len(df):>7}")
    rivit.append("-" * 78)
    uudet = tulokset.get("uudet", pd.DataFrame())
    if not uudet.empty:
        vakavat = uudet[uudet.get("vakavuus", pd.Series(dtype=str)).isin(["virhe", "varoitus"])]
        naytettavat = (vakavat if not vakavat.empty else uudet).head(nayta)
        rivit.append(f"  Uudet havainnot (enintään {nayta}):")
        for _, h in naytettavat.iterrows():
            nimi = str(h.get("era_nimi") or h.get("era") or "")[:38]
            rivit.append(
                f"   [{str(h.get('vakavuus','')):<8}] {nimi:<38} "
                f"{str(h.get('verovuosi','')):<6}{str(h.get('tunnusluku','')):<6}"
            )
            rivit.append(f"              {h.get('kohde','')}")
    rivit.append("=" * 78)
    return "\n".join(rivit)


__all__ = [
    "AVAINSARAKKEET",
    "konsolivertailu",
    "kirjoita_vertailu",
    "lue_havainnot",
    "vertaa",
]
