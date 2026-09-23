#!/usr/bin/env python3
"""pxtarkistus – komentorivikäyttöliittymä.

Esimerkit:

    python aja.py kartoita
    python aja.py kartoita --juuri Henkiloasiakkaiden_tuloverot
    python aja.py muuttujat --taulu Henkiloasiakkaiden_tuloverot/lopulliset/alue/tulot_102.px
    python aja.py erat --taulu Henkiloasiakkaiden_tuloverot/lopulliset/alue/tulot_102.px --haku palkka
    python aja.py tarkista --joukko kaikki --vuodet kaikki --max-erat 1000
    python aja.py tarkista --joukko henkilo_yleisesti --verovuosi 2024
    python aja.py tarkista --joukko henkilo_kaikki,henkilo_yel --tarkistukset ristiin,aikasarja
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from pxtarkistus import __version__
from pxtarkistus.inventaario import KiinnitysVirhe, jaetut_erat, tunnista_roolit
from pxtarkistus.pxclient import PxWebAsiakas
from pxtarkistus.raportti import aikaleima, kirjoita_excel, konsoliyhteenveto, tiivista_raportti
from pxtarkistus.tarkistukset import (
    Toleranssi,
    havainnot_kehykseksi,
    tarkista_aikasarja,
    tarkista_alueet,
    tarkista_erahierarkia,
    tarkista_osasummat,
    tarkista_ristiin,
)

JUURI = Path(__file__).resolve().parent


class JoukkoVirhe(RuntimeError):
    """Joukkoa ei voitu tarkistaa; muut joukot ajetaan silti."""


def lataa_asetukset(polku: Path | str) -> dict[str, Any]:
    p = Path(polku)
    if not p.is_absolute():
        p = JUURI / p
    if not p.exists():
        raise SystemExit(f"Asetustiedostoa ei löydy: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def tee_asiakas(a: dict[str, Any]) -> PxWebAsiakas:
    return PxWebAsiakas(
        palvelin=a.get("palvelin", "https://vero2.stat.fi/PXWeb/api/v1/fi"),
        tietokanta=a.get("tietokanta", "Vero"),
        valimuisti_hakemisto=JUURI / a.get("valimuisti", ".pxvalimuisti"),
        valimuisti_tuntia=float(a.get("valimuisti_tuntia", 168)),
        tauko_s=float(a.get("tauko_s", 1.0)),
        max_solut=int(a.get("max_solut", 40000)),
        aikakatkaisu_s=float(a.get("aikakatkaisu_s", 300)),
        listaus_valimuisti_tuntia=float(a.get("listaus_valimuisti_tuntia", 1)),
        ilman_paivitysaikaa_tuntia=float(a.get("ilman_paivitysaikaa_tuntia", 12)),
    )


# ----------------------------------------------------------------- komennot


def komento_kartoita(args, a: dict[str, Any]) -> int:
    asiakas = tee_asiakas(a)
    taulut = asiakas.kartoita(args.juuri or "")
    df = pd.DataFrame(
        [{"polku": t.polku, "otsikko": t.otsikko, "paivitetty": t.paivitetty} for t in taulut]
    )
    tuloste = Path(args.tuloste) if args.tuloste else JUURI / "taulut.csv"
    df.to_csv(tuloste, index=False, encoding="utf-8-sig", sep=";")
    print(f"Kartoitettu {len(df)} taulua -> {tuloste}")
    for t in taulut[:25]:
        print(f"  {t.polku}\n      {t.otsikko}")
    if len(taulut) > 25:
        print(f"  ... ja {len(taulut) - 25} muuta")
    return 0


def komento_muuttujat(args, a: dict[str, Any]) -> int:
    asiakas = tee_asiakas(a)
    meta = asiakas.metatiedot(args.taulu)
    roolit = tunnista_roolit(meta, a.get("luokitukset"))
    print(f"{meta.polku}\n{meta.otsikko}\n")
    for m in meta.muuttujat:
        rooli = (
            "aika" if roolit.aika and m.koodi == roolit.aika.koodi
            else "erä" if roolit.era and m.koodi == roolit.era.koodi
            else "tunnusluku" if roolit.tunnusluku and m.koodi == roolit.tunnusluku.koodi
            else "luokittelu"
        )
        print(f"  {m.koodi:<20} ({rooli:<10}) {len(m.arvot):>5} arvoa")
        naytteet = ", ".join(f"{k}={t}" for k, t in list(zip(m.arvot, m.arvotekstit))[:6])
        print(f"      {naytteet}")
        l = roolit.luokittelu(m.koodi)
        if l:
            print(f"      Yhteensä-arvo: {l.yhteensa or 'EI TUNNISTETTU'}")
            for o in l.ositukset:
                laatu = "päätelty" if o.paateltu else "asetuksista"
                print(f"      Ositus '{o.nimi}' ({laatu}): {len(o.koodit)} luokkaa")
    return 0


def komento_erat(args, a: dict[str, Any]) -> int:
    asiakas = tee_asiakas(a)
    meta = asiakas.metatiedot(args.taulu)
    roolit = tunnista_roolit(meta, a.get("luokitukset"))
    if not roolit.era:
        print("Taulusta ei tunnistettu erämuuttujaa")
        return 1
    haku = (args.haku or "").lower()
    n = 0
    for koodi, teksti in zip(roolit.era.arvot, roolit.era.arvotekstit):
        if haku and haku not in teksti.lower() and haku not in koodi.lower():
            continue
        print(f"  {koodi:<24} {teksti}")
        n += 1
    print(f"\n{n} erää" + (f" hakusanalla '{args.haku}'" if haku else ""))
    return 0


SUODINAVAIMET = ("otsikko", "otsikko_ei")


def otsikko_kelpaa(otsikko: str, suodin: dict[str, Any]) -> bool:
    """Taulun otsikko vs. joukon rajaus (säännölliset lausekkeet, kirjainkoko ei väliä).

    Perusjoukko luetaan otsikosta: kansiossa 19 osa tauluista on
    "Yleisesti verovelvollisten …", vaikka kansio muuten kattaa kaikki
    verovelvolliset.
    """
    import re as _re

    if suodin.get("otsikko") and not _re.search(suodin["otsikko"], otsikko, _re.IGNORECASE):
        return False
    if suodin.get("otsikko_ei") and _re.search(suodin["otsikko_ei"], otsikko, _re.IGNORECASE):
        return False
    return True


def laajenna_joukko(asiakas: PxWebAsiakas, nimi: str, joukko: dict[str, Any]) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
    """Joukon taulut: (polku, kiinnitykset).

    Joukko voi luetella taulut suoraan (`taulut`) tai kansioina (`kansiot`),
    jolloin kaikki kansion taulut alikansioineen otetaan mukaan – myös
    verovuosikohtaiset alikansiot. Taulun voi antaa sanakirjana
    {polku, kiinnita: {muuttuja: arvo}}, jolloin muuttuja rajataan arvoon
    Yhteensä-arvon sijaan. `ohita` on lista säännöllisiä lausekkeita.
    """
    import re as _re

    yleinen = {k: joukko.get(k) for k in SUODINAVAIMET if joukko.get(k)}

    def suodin(kohta: Any) -> dict[str, Any]:
        oma = {k: kohta.get(k) for k in SUODINAVAIMET if isinstance(kohta, dict) and kohta.get(k)}
        return {**yleinen, **oma}

    tulos: list[tuple[str, dict[str, str], dict[str, Any]]] = []
    for t in joukko.get("taulut", []) or []:
        if isinstance(t, dict):
            tulos.append((t["polku"], dict(t.get("kiinnita") or {}), suodin(t)))
        else:
            tulos.append((str(t), {}, suodin(None)))
    for k in joukko.get("kansiot", []) or []:
        if isinstance(k, dict):
            kiinnita = dict(k.get("kiinnita") or {})
            kansio = k["polku"]
        else:
            kiinnita, kansio = {}, str(k)
        for taulu in asiakas.kartoita(kansio):
            tulos.append((taulu.polku, kiinnita, suodin(k)))
    ohita = [_re.compile(x) for x in (joukko.get("ohita") or [])]
    nahty: set[tuple[str, str]] = set()
    siistitty = []
    for polku, kiinnita, suod in tulos:
        avain = (polku, repr(sorted(kiinnita.items())))
        if avain in nahty or any(o.search(polku) for o in ohita):
            continue
        nahty.add(avain)
        siistitty.append((polku, kiinnita, suod))
    return siistitty


def _polku_joukolle(pohja: str | None, joukko: str) -> str | None:
    if not pohja:
        return None
    return pohja.replace("{joukko}", joukko)


def aja_joukko(args, a: dict[str, Any], asiakas: PxWebAsiakas, nimi: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any]]:
    joukko = a["joukot"][nimi]
    verovuosi = str(args.verovuosi or a.get("verovuosi", "2024"))
    tunnusluvut = args.tunnusluvut.split(",") if args.tunnusluvut else a.get("tunnusluvut", ["Sum", "N"])
    valitut = [
        t.strip()
        for t in (args.tarkistukset or "ristiin,osasummat,aikasarja,erahierarkia,alueet").split(",")
    ]
    # Näkymävertailussa myös keskiarvo ja mediaani: ne eivät ole additiivisia,
    # mutta koko maan tasolla niiden pitää olla samat joka näkymässä.
    ristiin_tl = (a.get("ristiin") or {}).get("tunnusluvut") or tunnusluvut

    print(f"\n=== Joukko {nimi}: {joukko.get('kuvaus','')}")
    taulut = laajenna_joukko(asiakas, nimi, joukko)
    print(f"Luetaan {len(taulut)} taulun metatiedot ...")
    roolit = []
    ohitetut: list[str] = []
    rajatut = 0
    for polku, kiinnita, suod in taulut:
        meta = asiakas.metatiedot(polku)
        if not otsikko_kelpaa(meta.otsikko, suod):
            rajatut += 1
            continue
        try:
            r = tunnista_roolit(meta, a.get("luokitukset"), kiinnita)
        except KiinnitysVirhe as e:
            ohitetut.append(str(e))
            continue
        if not (r.era and r.tunnusluku and r.aika):
            ohitetut.append(f"{polku}: ei tunnistettua erä-, aika- tai tunnuslukumuuttujaa")
            continue
        roolit.append(r)
    if rajatut:
        print(f"  {rajatut} taulua rajattu pois otsikon perusteella (eri perusjoukko)")
    for o in ohitetut:
        print(f"  ohitettu – {o}")
    if not roolit:
        raise JoukkoVirhe(f"Joukossa {nimi} ei ole tarkistettavia tauluja")

    if args.erat_tiedosto:
        erat = [r.strip() for r in Path(args.erat_tiedosto).read_text(encoding="utf-8").splitlines() if r.strip()]
    else:
        # yhden taulun joukossa kaikki erät; muuten vähintään kahdessa taulussa olevat
        erat = jaetut_erat(roolit, vahintaan=2 if len(roolit) > 1 else 1)
    if not erat:
        raise JoukkoVirhe(
            f"Joukon {nimi} tauluilla ei ole yhtään yhteistä tilastoerää. "
            "Tarkista joukon taulut tai anna erät --erat-tiedosto-valitsimella."
        )
    max_erat = int(args.max_erat or a.get("max_erat", 80))
    if len(erat) > max_erat:
        print(f"Eriä {len(erat)}, rajataan {max_erat} kattavimpaan (--max-erat muuttaa).")
        erat = erat[:max_erat]

    tol = Toleranssi(
        absoluuttinen=float((a.get("toleranssi") or {}).get("absoluuttinen", 1.0)),
        suhteellinen=float((a.get("toleranssi") or {}).get("suhteellinen", 0.0)),
        pyoristys_per_solu=float((a.get("toleranssi") or {}).get("pyoristys_per_solu", 0.5)),
    )

    kaikki_havainnot = []
    aineistot: dict[str, pd.DataFrame] = {}

    if (args.vuodet or "").lower() == "kaikki":
        kaikki: list[str] = []
        for r in roolit:
            for v in r.aika.arvot:
                if v not in kaikki:
                    kaikki.append(v)
        ristiin_vuodet: str | list[str] = sorted(kaikki)
    else:
        ristiin_vuodet = verovuosi

    if "ristiin" in valitut and len(roolit) > 1:
        print("Tarkistus A: sama erä eri näkymissä ...")
        hav, data = tarkista_ristiin(
            asiakas, roolit, ristiin_vuodet, erat=erat, tunnusluvut=ristiin_tl,
            toleranssi=tol, vertailutaulu=joukko.get("vertailutaulu"),
        )
        kaikki_havainnot += hav
        aineistot["ristiin"] = data

    os_kuvaus = ""
    if "osasummat" in valitut:
        os_valinta = (getattr(args, "osasummavuodet", None) or "").strip()
        if os_valinta.lower() == "kaikki":
            os_vuodet: str | list[str] = sorted({v for r in roolit for v in r.aika.arvot})
        elif os_valinta:
            os_vuodet = [v.strip() for v in os_valinta.split(",") if v.strip()]
        else:
            os_vuodet = verovuosi
        if isinstance(os_vuodet, list):
            from pxtarkistus.tarkistukset import _vuosiluettelo
            os_kuvaus = _vuosiluettelo(os_vuodet)
        print(
            "Tarkistus B: osasummat vs. Yhteensä"
            + (f" ({len(os_vuodet)} vuotta)" if isinstance(os_vuodet, list) else "")
            + " ..."
        )
        hav, data = tarkista_osasummat(
            asiakas, roolit, os_vuodet, erat=erat, tunnusluvut=tunnusluvut, toleranssi=tol,
            max_luokat=int((a.get("osasummat") or {}).get("max_luokat", 2000)),
        )
        kaikki_havainnot += hav
        aineistot["osasummat"] = data

    if "aikasarja" in valitut:
        print("Tarkistus C: aikasarjan eheys ja revisiot ...")
        as_asetukset = a.get("aikasarja") or {}
        vedos = _polku_joukolle(
            args.tilannevedos or as_asetukset.get("tilannevedos", "tilannevedokset/{joukko}.json"), nimi
        )
        hav, data, _ = tarkista_aikasarja(
            asiakas, roolit, erat=erat, tunnusluvut=tunnusluvut,
            hyppy_raja=float(as_asetukset.get("hyppy_raja", 0.30)),
            vahimmaisarvo=float(as_asetukset.get("vahimmaisarvo", 1000)),
            tilannevedos=(JUURI / vedos) if vedos else None,
        )
        kaikki_havainnot += hav
        aineistot["aikasarja"] = data

    if "erahierarkia" in valitut:
        print("Tarkistus D: yläerä vs. alaerien summa ...")
        eh = a.get("erahierarkia") or {}
        saannot = _polku_joukolle(
            args.erahierarkia_saannot or eh.get("saannot", "saannot/erahierarkia_{joukko}.json"), nimi
        )
        hav, data = tarkista_erahierarkia(
            asiakas, roolit, verovuosi, erat=erat, toleranssi=tol,
            saannot_tiedosto=JUURI / saannot,
            vahimmaisvuodet=int(eh.get("vahimmaisvuodet", 3)),
        )
        kaikki_havainnot += hav
        aineistot["erahierarkia"] = data

    if "alueet" in valitut:
        print("Tarkistus E: kunnan luku vs. sen postinumeroalueiden summa ...")
        hav, data = tarkista_alueet(asiakas, roolit, erat=erat, tunnusluvut=tunnusluvut, toleranssi=tol)
        kaikki_havainnot += hav
        aineistot["alueet"] = data

    havainnot = havainnot_kehykseksi(kaikki_havainnot)
    aineistot["taulut"] = taulukooste(asiakas, roolit, havainnot)
    meta_tiedot = {
        "Joukko": f"{nimi} – {joukko.get('kuvaus','')}",
        "Verovuosi": verovuosi
        + (" (näkymävertailu koko aikasarjalta)" if isinstance(ristiin_vuodet, list) else ""),
        "Tauluja": len(roolit),
        "Rajattu otsikon perusteella": rajatut,
        "Ohitettuja tauluja": len(ohitetut),
        "Tilastoeriä": len(erat),
        "Tarkistukset": ", ".join(valitut),
        "Ohjelmaversio": __version__,
    }
    if os_kuvaus:
        meta_tiedot["Osasummat vuosilta"] = os_kuvaus
    print(konsoliyhteenveto(havainnot, meta_tiedot))
    return havainnot, aineistot, meta_tiedot


def _aika(epoch: float) -> str:
    import datetime as _dt

    t = _dt.datetime.fromtimestamp(epoch)
    return f"{t.day}.{t.month}.{t.year} klo {t.hour}.{t.minute:02d}"


def taulukooste(asiakas: PxWebAsiakas, roolit, havainnot: pd.DataFrame) -> pd.DataFrame:
    """Joukon taulut: PxWebin päivitysaika, milloin data haettiin ja havaintojen määrä.

    Näkymien väliset erot johtuvat usein siitä, että taulut on päivitetty eri
    aikaan; tästä näkee vuosikerrat yhdellä silmäyksellä.
    """
    from pxtarkistus.tarkistukset import _pvm

    rivit = []
    kohteet = havainnot[["kohde", "vakavuus"]].astype(str) if not havainnot.empty else None
    for r in roolit:
        hk = getattr(asiakas, "hakuajat", {}).get(r.polku, [])
        rivi: dict[str, Any] = {
            "taulu": r.tunniste,
            "otsikko": r.meta.otsikko,
            "paivitetty": _pvm(asiakas.paivitysaika(r.polku), kello=True)
            if hasattr(asiakas, "paivitysaika") else "",
            "data_haettu": (
                _aika(min(hk)) if hk and _aika(min(hk)) == _aika(max(hk))
                else f"{_aika(min(hk))} – {_aika(max(hk))}" if hk else "välimuistista / ei haettu"
            ),
        }
        if kohteet is not None:
            oma = kohteet[kohteet["kohde"].str.startswith(r.tunniste)]
            for v in ("virhe", "varoitus", "huomio"):
                rivi[v] = int((oma["vakavuus"] == v).sum())
        rivit.append(rivi)
    return pd.DataFrame(rivit)


def komento_tarkista(args, a: dict[str, Any]) -> int:
    asiakas = tee_asiakas(a)
    joukot = a.get("joukot", {})
    if args.joukko == "kaikki":
        nimet = list(joukot)
    else:
        nimet = [j.strip() for j in args.joukko.split(",") if j.strip()]
    tuntemattomat = [n for n in nimet if n not in joukot]
    if tuntemattomat:
        raise SystemExit(
            f"Tuntematon joukko: {', '.join(tuntemattomat)}. "
            f"Vaihtoehdot: kaikki, {', '.join(joukot)}"
        )

    tol = Toleranssi(
        absoluuttinen=float((a.get("toleranssi") or {}).get("absoluuttinen", 1.0)),
        suhteellinen=float((a.get("toleranssi") or {}).get("suhteellinen", 0.0)),
        pyoristys_per_solu=float((a.get("toleranssi") or {}).get("pyoristys_per_solu", 0.5)),
    )

    kaikki_havainnot: list[pd.DataFrame] = []
    kaikki_aineistot: dict[str, list[pd.DataFrame]] = {}
    joukkometa: list[dict[str, Any]] = []
    epaonnistuneet: list[str] = []
    for nimi in nimet:
        try:
            havainnot, aineistot, meta = aja_joukko(args, a, asiakas, nimi)
        except JoukkoVirhe as e:
            print(f"!! {e} – joukko ohitettiin")
            epaonnistuneet.append(f"{nimi}: {e}")
            continue
        if not havainnot.empty:
            havainnot.insert(0, "joukko", nimi)
            kaikki_havainnot.append(havainnot)
        for laji, df in aineistot.items():
            if df is None or df.empty:
                continue
            if len(nimet) > 1 and laji not in ("ristiin", "taulut"):
                # joukon oma raportti kirjoittaa aineiston; yhteisraporttiin
                # tarvitaan vain näkymävertailun taustaluvut
                continue
            df = df.copy()
            df.insert(0, "joukko", nimi)
            kaikki_aineistot.setdefault(laji, []).append(df)
        joukkometa.append(meta)
        if len(nimet) > 1:
            # pitkässä ajossa jokaisen joukon tulos talteen heti
            osa = (Path(args.raportti) if args.raportti else JUURI / "raportit") / (
                f"pxtarkistus_{nimi}_{args.verovuosi or a.get('verovuosi', '2024')}_{aikaleima()}.xlsx"
            )
            kirjoita_excel(osa, havainnot, aineistot, meta)
            print(f"Joukon raportti: {osa}")

    havainnot = (
        pd.concat(kaikki_havainnot, ignore_index=True) if kaikki_havainnot else pd.DataFrame()
    )
    aineistot = {k: pd.concat(v, ignore_index=True) for k, v in kaikki_aineistot.items()}
    verovuosi = str(args.verovuosi or a.get("verovuosi", "2024"))
    meta_tiedot: dict[str, Any] = {
        "Joukot": ", ".join(nimet),
        "Verovuosi": verovuosi,
        "Tauluja yhteensä": sum(m["Tauluja"] for m in joukkometa),
        "Toleranssi": (
            f"{tol.absoluuttinen:g} €/kpl, {tol.suhteellinen:.4%} vertailuarvosta, "
            f"pyöristysvara {tol.pyoristys_per_solu:g} € summattavaa solua kohden"
        ),
        "Ajettu": aikaleima(),
        "Ohjelmaversio": __version__,
    }
    for m in joukkometa:
        meta_tiedot[f"– {m['Joukko'].split(' – ')[0]}"] = (
            f"{m['Tauluja']} taulua, {m['Tilastoeriä']} erää"
            + (f", {m['Ohitettuja tauluja']} ohitettu" if m["Ohitettuja tauluja"] else "")
        )

    os_vuodet_meta = sorted({m["Osasummat vuosilta"] for m in joukkometa if m.get("Osasummat vuosilta")})
    if os_vuodet_meta:
        meta_tiedot["Osasummat vuosilta"] = "; ".join(os_vuodet_meta)
    if len(nimet) > 1:
        meta_tiedot["Koko aineisto"] = "joukkokohtaisten raporttien _data_-tiedostoissa"
    for e in epaonnistuneet:
        meta_tiedot[f"OHITETTU {e.split(':')[0]}"] = e.split(": ", 1)[1]
    if not joukkometa:
        raise SystemExit("Yhtään joukkoa ei voitu tarkistaa")

    if len(nimet) > 1:
        print()
        print(konsoliyhteenveto(havainnot, meta_tiedot))

    tunniste = nimet[0] if len(nimet) == 1 else ("kaikki" if args.joukko == "kaikki" else "useita")
    hakemisto = Path(args.raportti) if args.raportti else JUURI / "raportit"
    tiedosto = hakemisto / f"pxtarkistus_{tunniste}_{verovuosi}_{aikaleima()}.xlsx"
    kirjoita_excel(tiedosto, havainnot, aineistot, meta_tiedot, kirjoita_aineisto=len(nimet) == 1)
    print(f"\nRaportti: {tiedosto}")
    virheita = int((havainnot["vakavuus"] == "virhe").sum()) if not havainnot.empty else 0
    return 1 if virheita else 0


def komento_tiivista(args, a: dict[str, Any]) -> int:
    for polku in args.raportit:
        polku = Path(polku)
        if not polku.exists():
            print(f"Tiedostoa ei löydy: {polku}", file=sys.stderr)
            return 2
        print(f"Luetaan {polku} (suuri tiedosto voi viedä muutaman minuutin) ...")
        uusi = tiivista_raportti(polku, ilmoita=lambda t: print(t, flush=True))
        koko = uusi.stat().st_size / 1e6
        print(f"  -> {uusi} ({koko:.1f} Mt)")
        for data in sorted(uusi.parent.glob(f"{uusi.stem}_data_*.csv.gz")):
            print(f"     aineisto: {data.name} ({data.stat().st_size / 1e6:.1f} Mt)")
    return 0


# --------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    # Yhteiset valitsimet kelpaavat sekä ennen alikomentoa että sen jälkeen.
    yhteiset = argparse.ArgumentParser(add_help=False)
    yhteiset.add_argument("--asetukset", default=argparse.SUPPRESS)
    yhteiset.add_argument(
        "--vaiheittain", action="store_true", default=argparse.SUPPRESS, help="näytä lokitus"
    )

    jasennin = argparse.ArgumentParser(
        prog="pxtarkistus", description="Verohallinnon verotilastojen ristiintarkistus"
    )
    jasennin.add_argument("--asetukset", default="asetukset.yaml")
    jasennin.add_argument("--vaiheittain", action="store_true", help="näytä lokitus")
    alikomennot = jasennin.add_subparsers(dest="komento", required=True)

    k = alikomennot.add_parser(
        "kartoita", parents=[yhteiset], help="käy tietokantapuu läpi ja listaa taulut"
    )
    k.add_argument("--juuri", default="", help="aloituspolku, esim. Henkiloasiakkaiden_tuloverot")
    k.add_argument("--tuloste", help="CSV-tiedosto (oletus taulut.csv)")
    k.set_defaults(func=komento_kartoita)

    m = alikomennot.add_parser(
        "muuttujat", parents=[yhteiset], help="näytä taulun muuttujat ja tunnistetut roolit"
    )
    m.add_argument("--taulu", required=True)
    m.set_defaults(func=komento_muuttujat)

    e = alikomennot.add_parser("erat", parents=[yhteiset], help="listaa taulun tilastoerät")
    e.add_argument("--taulu", required=True)
    e.add_argument("--haku", help="hakusana erän nimestä tai koodista")
    e.set_defaults(func=komento_erat)

    ti = alikomennot.add_parser(
        "tiivista", parents=[yhteiset],
        help="muunna vanha raportti kevyeksi (aineisto erillisiin .csv.gz-tiedostoihin)",
    )
    ti.add_argument("raportit", nargs="+", help="raporttitiedosto(t)")
    ti.set_defaults(func=komento_tiivista)

    t = alikomennot.add_parser(
        "tarkista", parents=[yhteiset], help="aja tarkistukset ja kirjoita raportti"
    )
    t.add_argument("--joukko", required=True, help="joukon nimi, pilkulla erotettu lista tai 'kaikki'")
    t.add_argument("--verovuosi")
    t.add_argument(
        "--vuodet",
        help="'kaikki' vertaa näkymiä jokaiselta verovuodelta (aikasarjatarkistus hakee "
        "luvut joka tapauksessa, joten lisäkustannus on pieni)",
    )
    t.add_argument(
        "--osasummavuodet",
        help="osasummatarkistuksen vuodet: 'kaikki' tai pilkkulista (oletus: --verovuosi). "
        "Kaikki vuodet moninkertaistaa haettavan datan, joten ajo kestää tunteja.",
    )
    t.add_argument(
        "--tarkistukset", help="pilkkulista: ristiin,osasummat,aikasarja,erahierarkia,alueet"
    )
    t.add_argument("--tunnusluvut", help="esim. Sum,N")
    t.add_argument("--erat-tiedosto", help="tekstitiedosto, yksi erätunnus per rivi")
    t.add_argument("--max-erat", type=int)
    t.add_argument("--raportti", help="raporttihakemisto")
    t.add_argument("--tilannevedos", help="JSON-tiedosto revisiovertailuun")
    t.add_argument(
        "--erahierarkia-saannot", help="JSON-tiedosto vahvistetuille yläerä–alaerä-suhteille"
    )
    t.set_defaults(func=komento_tarkista)

    args = jasennin.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.vaiheittain else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    asetukset = lataa_asetukset(args.asetukset)
    return args.func(args, asetukset)


if __name__ == "__main__":
    sys.exit(main())
