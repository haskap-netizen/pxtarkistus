"""PxWeb API v1 -asiakas Verohallinnon julkisille verotilastoille.

Palvelu: https://vero2.stat.fi/PXWeb/api/v1/fi/Vero/

API v1:ssa metatiedot haetaan GET-kutsulla taulun osoitteesta ja varsinainen
data POST-kutsulla samaan osoitteeseen. Vastaus pyydetään json-stat2-muodossa,
joka puretaan tässä pitkäksi (long-format) DataFrameksi.

Asiakas on tarkoituksella "kohtelias": pyyntöjen välissä on tauko, vastaukset
tallennetaan levyvälimuistiin ja liian suuret kyselyt paloitellaan.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
import requests

LOG = logging.getLogger("pxtarkistus.pxclient")

OLETUS_PALVELIN = "https://vero2.stat.fi/PXWeb/api/v1/fi"
OLETUS_TIETOKANTA = "Vero"


class PxWebVirhe(RuntimeError):
    """PxWeb-rajapinnan palauttama virhe tai kyselyn rakennevirhe."""


@dataclass
class Taulu:
    """Yksittäinen PxWeb-taulu tietokantapuussa."""

    polku: str  # esim. "Henkiloasiakkaiden_tuloverot/lopulliset/alue/tulot_102.px"
    otsikko: str
    paivitetty: str | None = None

    @property
    def tunnus(self) -> str:
        return self.polku

    @property
    def kansio(self) -> str:
        return self.polku.rsplit("/", 1)[0] if "/" in self.polku else ""


@dataclass
class Muuttuja:
    """Taulun muuttuja (dimensio) metatiedoista."""

    koodi: str
    teksti: str
    arvot: list[str]
    arvotekstit: list[str]
    aika: bool = False
    eliminointi: bool = False

    def teksti_koodille(self, koodi: str) -> str:
        try:
            return self.arvotekstit[self.arvot.index(koodi)]
        except (ValueError, IndexError):
            return koodi

    def koodi_tekstille(self, teksti: str) -> str | None:
        try:
            return self.arvot[self.arvotekstit.index(teksti)]
        except (ValueError, IndexError):
            return None


@dataclass
class Metatiedot:
    """Taulun metatiedot: otsikko ja muuttujat."""

    polku: str
    otsikko: str
    muuttujat: list[Muuttuja] = field(default_factory=list)

    def muuttuja(self, koodi: str) -> Muuttuja | None:
        for m in self.muuttujat:
            if m.koodi == koodi:
                return m
        return None

    @property
    def koodit(self) -> list[str]:
        return [m.koodi for m in self.muuttujat]

    def solumaara(self, valinta: dict[str, Sequence[str]]) -> int:
        n = 1
        for m in self.muuttujat:
            valitut = valinta.get(m.koodi)
            n *= len(valitut) if valitut else len(m.arvot)
        return n


class Valimuisti:
    """Yksinkertainen levyvälimuisti gzip-pakatuille JSON-vastauksille."""

    def __init__(self, hakemisto: Path | str, voimassa_tuntia: float = 24 * 7) -> None:
        self.hakemisto = Path(hakemisto)
        self.hakemisto.mkdir(parents=True, exist_ok=True)
        self.voimassa_s = voimassa_tuntia * 3600

    def _polku(self, avain: str) -> Path:
        tiiviste = hashlib.sha256(avain.encode("utf-8")).hexdigest()[:24]
        return self.hakemisto / f"{tiiviste}.json.gz"

    def hae(
        self,
        avain: str,
        *,
        max_ika_s: float | None = None,
        ei_vanhempi_kuin: float | None = None,
    ) -> Any | None:
        """Palauttaa tallennetun vastauksen tai None.

        `max_ika_s` lyhentää voimassaoloa tälle haulle (esim. kansiolistaukset).
        `ei_vanhempi_kuin` (epoch-sekunteina) hylkää vastauksen, joka on haettu
        ennen tätä hetkeä – käytetään, kun taulu on päivitetty palvelimella
        välimuistiin tallentamisen jälkeen.
        """
        p = self._polku(avain)
        if not p.exists():
            return None
        haettu = p.stat().st_mtime
        ika = time.time() - haettu
        if self.voimassa_s > 0 and ika > self.voimassa_s:
            return None
        if max_ika_s is not None and ika > max_ika_s:
            return None
        if ei_vanhempi_kuin is not None and haettu < ei_vanhempi_kuin:
            return None
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def haettu(self, avain: str) -> float | None:
        """Milloin vastaus on haettu palvelimelta (epoch), jos se on välimuistissa."""
        p = self._polku(avain)
        return p.stat().st_mtime if p.exists() else None

    def tallenna(self, avain: str, sisalto: Any) -> None:
        p = self._polku(avain)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(sisalto, f, ensure_ascii=False)


class PxWebAsiakas:
    """PxWeb API v1 -asiakas."""

    def __init__(
        self,
        palvelin: str = OLETUS_PALVELIN,
        tietokanta: str = OLETUS_TIETOKANTA,
        valimuisti_hakemisto: Path | str = ".pxvalimuisti",
        valimuisti_tuntia: float = 24 * 7,
        tauko_s: float = 1.0,
        max_solut: int = 100_000,
        aikakatkaisu_s: float = 300.0,
        listaus_valimuisti_tuntia: float = 1.0,
        ilman_paivitysaikaa_tuntia: float = 12.0,
    ) -> None:
        self.palvelin = palvelin.rstrip("/")
        self.tietokanta = tietokanta
        self.valimuisti = Valimuisti(valimuisti_hakemisto, valimuisti_tuntia)
        self.tauko_s = tauko_s
        self.max_solut = max_solut
        self.aikakatkaisu_s = aikakatkaisu_s
        # Kansiolistaus kertoo taulujen päivitysajat. Se haetaan usein, jotta
        # palvelimella päivitetyn taulun vanha vastaus ei jää välimuistista
        # käyttöön – muuten yhden ajon sisällä voisi sekoittua eri-ikäistä dataa.
        self.listaus_max_ika_s = listaus_valimuisti_tuntia * 3600
        # Taulut, joiden listauksessa ei ole päivitysaikaa: lyhyempi voimassaolo.
        self.ilman_paivitysaikaa_s = ilman_paivitysaikaa_tuntia * 3600
        self.paivitysajat: dict[str, str | None] = {}
        self.hakuajat: dict[str, list[float]] = {}
        self._viime_kutsu = 0.0
        self.istunto = requests.Session()
        self.istunto.headers.update(
            {
                "User-Agent": "pxtarkistus/1.0 (verotilastojen ristiintarkistus)",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ apu

    def _osoite(self, polku: str) -> str:
        polku = polku.strip("/")
        osat = [self.palvelin, self.tietokanta]
        if polku:
            osat.append(polku)
        return "/".join(osat)

    def _odota(self) -> None:
        kulunut = time.monotonic() - self._viime_kutsu
        if kulunut < self.tauko_s:
            time.sleep(self.tauko_s - kulunut)
        self._viime_kutsu = time.monotonic()

    def _pyynto(self, metodi: str, osoite: str, **kwargs) -> Any:
        viimeisin: Exception | None = None
        for yritys in range(4):
            self._odota()
            try:
                vastaus = self.istunto.request(
                    metodi, osoite, timeout=self.aikakatkaisu_s, **kwargs
                )
            except requests.RequestException as e:  # verkkovirhe tai aikakatkaisu
                viimeisin = e
                # Suuret taulut (postinumerot × sukupuoli) ovat palvelimelle raskaita
                # ladata; lyhyt odotus vain toistaisi saman aikakatkaisun.
                odotus = (5, 15, 45, 90)[yritys]
                LOG.warning("Verkkovirhe (%s), uusi yritys %.0f s kuluttua: %s", osoite, odotus, e)
                time.sleep(odotus)
                continue
            if vastaus.status_code == 429:  # liikaa pyyntöjä
                odotus = float(vastaus.headers.get("Retry-After", 10))
                LOG.warning("Palvelin pyytää hidastamaan, odotetaan %.0f s", odotus)
                time.sleep(odotus)
                continue
            if vastaus.status_code >= 400:
                raise PxWebVirhe(
                    f"{metodi} {osoite} -> HTTP {vastaus.status_code}: {vastaus.text[:400]}"
                )
            try:
                return vastaus.json()
            except json.JSONDecodeError as e:
                raise PxWebVirhe(f"Vastaus ei ollut JSONia: {vastaus.text[:200]}") from e
        raise PxWebVirhe(f"{metodi} {osoite} epäonnistui toistuvasti: {viimeisin}")

    # -------------------------------------------------------------- selaus

    def selaa(self, polku: str = "") -> list[dict[str, Any]]:
        """Palauttaa yhden tason sisällön: kansiot (type 'l') ja taulut (type 't')."""
        avain = f"GET|{self._osoite(polku)}"
        kasitelty = self.valimuisti.hae(avain, max_ika_s=self.listaus_max_ika_s)
        if kasitelty is None:
            kasitelty = self._pyynto("GET", self._osoite(polku))
            self.valimuisti.tallenna(avain, kasitelty)
        rivit = kasitelty if isinstance(kasitelty, list) else []
        for rivi in rivit:
            if isinstance(rivi, dict) and rivi.get("type") == "t":
                alipolku = f"{polku.strip('/')}/{rivi.get('id', '')}".strip("/")
                self.paivitysajat[alipolku] = rivi.get("updated") or None
        return rivit

    def paivitysaika(self, polku: str) -> str | None:
        """Taulun päivitysaika kansiolistauksesta (None, jos listaus ei kerro sitä)."""
        if polku not in self.paivitysajat:
            kansio = polku.rsplit("/", 1)[0] if "/" in polku else ""
            try:
                self.selaa(kansio)
            except PxWebVirhe:
                pass
            self.paivitysajat.setdefault(polku, None)
        return self.paivitysajat.get(polku)

    def _valimuistiehdot(self, polku: str) -> dict[str, float]:
        """Välimuistin rajat taululle sen päivitysajan perusteella.

        Päivitysaika on Suomen aikaa ilman aikavyöhykemerkintää. Jos vyöhyketietoa
        ei ole käytettävissä, se tulkitaan UTC:ksi, jolloin raja osuu hieman
        todellista myöhemmäksi ja vastaus haetaan mieluummin uudelleen.
        """
        p = self.paivitysaika(polku)
        if not p:
            return {"max_ika_s": self.ilman_paivitysaikaa_s}
        try:
            aika = pd.Timestamp(p)
        except (ValueError, TypeError):
            return {"max_ika_s": self.ilman_paivitysaikaa_s}
        if aika.tzinfo is None:
            try:
                aika = aika.tz_localize("Europe/Helsinki")
            except Exception:  # vyöhyketietokanta puuttuu tai kellonsiirron monitulkintainen tunti
                aika = aika.tz_localize("UTC")
        return {"ei_vanhempi_kuin": aika.timestamp()}

    def kartoita(self, juuri: str = "", syvyys: int = 12) -> list[Taulu]:
        """Käy tietokantapuun läpi rekursiivisesti ja palauttaa kaikki taulut."""
        taulut: list[Taulu] = []
        jono: list[tuple[str, int]] = [(juuri, 0)]
        while jono:
            polku, taso = jono.pop(0)
            if taso > syvyys:
                continue
            for rivi in self.selaa(polku):
                tunnus = str(rivi.get("id", ""))
                alipolku = f"{polku}/{tunnus}".strip("/")
                if rivi.get("type") == "t":
                    taulut.append(
                        Taulu(
                            polku=alipolku,
                            otsikko=str(rivi.get("text", "")),
                            paivitetty=rivi.get("updated"),
                        )
                    )
                elif rivi.get("type") == "l":
                    jono.append((alipolku, taso + 1))
        LOG.info("Kartoitettu %d taulua polusta '%s'", len(taulut), juuri or "(juuri)")
        return taulut

    # ---------------------------------------------------------- metatiedot

    def metatiedot(self, polku: str) -> Metatiedot:
        avain = f"META|{self._osoite(polku)}"
        raaka = self.valimuisti.hae(avain, **self._valimuistiehdot(polku))
        if raaka is None:
            raaka = self._pyynto("GET", self._osoite(polku))
            self.valimuisti.tallenna(avain, raaka)
        muuttujat = [
            Muuttuja(
                koodi=str(m.get("code")),
                teksti=str(m.get("text", m.get("code"))),
                arvot=[str(v) for v in m.get("values", [])],
                arvotekstit=[str(v) for v in m.get("valueTexts", [])],
                aika=bool(m.get("time", False)),
                eliminointi=bool(m.get("elimination", False)),
            )
            for m in raaka.get("variables", [])
        ]
        return Metatiedot(polku=polku, otsikko=str(raaka.get("title", "")), muuttujat=muuttujat)

    # ---------------------------------------------------------------- data

    def hae(
        self,
        polku: str,
        valinta: dict[str, Sequence[str] | str],
        *,
        meta: Metatiedot | None = None,
    ) -> pd.DataFrame:
        """Hakee taulusta datan ja palauttaa pitkän DataFramen.

        `valinta` on sanakirja muuttujakoodi -> lista arvokoodeja, tai "*"
        kaikille arvoille. Muuttujat, joita ei mainita, jätetään pois
        kyselystä (PxWeb summaa ne eliminointimuuttujien osalta itse; muista
        muuttujista palautuu tällöin kaikki arvot).
        """
        meta = meta or self.metatiedot(polku)
        taydennetty = self._taydenna_valinta(meta, valinta)
        osat = self._paloittele(meta, taydennetty)
        kehykset = [self._hae_mukautuen(polku, meta, osa) for osa in osat]
        df = pd.concat(kehykset, ignore_index=True) if kehykset else pd.DataFrame()
        return df

    def _taydenna_valinta(
        self, meta: Metatiedot, valinta: dict[str, Sequence[str] | str]
    ) -> dict[str, list[str]]:
        tulos: dict[str, list[str]] = {}
        for koodi, arvot in valinta.items():
            m = meta.muuttuja(koodi)
            if m is None:
                raise PxWebVirhe(
                    f"Taulussa {meta.polku} ei ole muuttujaa '{koodi}'. "
                    f"Muuttujat: {', '.join(meta.koodit)}"
                )
            if arvot == "*":
                tulos[koodi] = list(m.arvot)
            else:
                puuttuvat = [a for a in arvot if a not in m.arvot]
                if puuttuvat:
                    raise PxWebVirhe(
                        f"Taulussa {meta.polku} muuttujalla '{koodi}' ei ole arvoja: "
                        f"{', '.join(puuttuvat[:8])}"
                    )
                tulos[koodi] = list(arvot)
        return tulos

    def _paloittele(
        self, meta: Metatiedot, valinta: dict[str, list[str]]
    ) -> list[dict[str, list[str]]]:
        """Jakaa liian suuren kyselyn osiin suurimman muuttujan mukaan."""
        solut = 1
        for m in meta.muuttujat:
            solut *= len(valinta.get(m.koodi, m.arvot))
        if solut <= self.max_solut:
            return [valinta]

        # paloitellaan sen valitun muuttujan mukaan, jolla on eniten arvoja
        pilkottava = max(valinta, key=lambda k: len(valinta[k]))
        muut = solut / max(len(valinta[pilkottava]), 1)
        palan_koko = max(1, int(self.max_solut // max(muut, 1)))
        arvot = valinta[pilkottava]
        osat = []
        for i in range(0, len(arvot), palan_koko):
            osa = dict(valinta)
            osa[pilkottava] = arvot[i : i + palan_koko]
            osat.append(osa)
        LOG.info(
            "Kysely %s (%s solua) paloiteltu %d osaan muuttujan '%s' mukaan",
            meta.polku,
            f"{solut:,}".replace(",", " "),
            len(osat),
            pilkottava,
        )
        return osat

    def _hae_mukautuen(
        self, polku: str, meta: Metatiedot, valinta: dict[str, list[str]], syvyys: int = 0
    ) -> pd.DataFrame:
        """Hakee osan; jos palvelin torjuu kyselyn liian suurena, puolittaa sen.

        Palvelimen solurajaa ei kerrota metatiedoissa. Suurilla osilla kyselyitä
        on vähemmän – ja raskaissa tauluissa (postinumerot) juuri kyselyiden
        määrä ratkaisee ajoajan – mutta liian suuri osa torjutaan. Silloin osa
        jaetaan kahtia ja yritetään uudelleen.
        """
        try:
            return self._hae_yksi(polku, meta, valinta)
        except PxWebVirhe as e:
            torjuttu = any(f"HTTP {k}" in str(e) for k in (400, 403, 413))
            pilkottava = max(valinta, key=lambda k: len(valinta[k]))
            if not torjuttu or syvyys >= 4 or len(valinta[pilkottava]) < 2:
                raise
            puoli = len(valinta[pilkottava]) // 2
            LOG.info(
                "Palvelin torjui kyselyn (%s), puolitetaan muuttujan '%s' mukaan",
                str(e)[:60], pilkottava,
            )
            a = dict(valinta, **{pilkottava: valinta[pilkottava][:puoli]})
            b = dict(valinta, **{pilkottava: valinta[pilkottava][puoli:]})
            return pd.concat(
                [self._hae_mukautuen(polku, meta, a, syvyys + 1),
                 self._hae_mukautuen(polku, meta, b, syvyys + 1)],
                ignore_index=True,
            )

    def _hae_yksi(
        self, polku: str, meta: Metatiedot, valinta: dict[str, list[str]]
    ) -> pd.DataFrame:
        kysely = {
            "query": [
                {"code": koodi, "selection": {"filter": "item", "values": arvot}}
                for koodi, arvot in valinta.items()
            ],
            "response": {"format": "json-stat2"},
        }
        avain = "POST|" + self._osoite(polku) + "|" + json.dumps(kysely, sort_keys=True)
        raaka = self.valimuisti.hae(avain, **self._valimuistiehdot(polku))
        if raaka is None:
            raaka = self._pyynto("POST", self._osoite(polku), json=kysely)
            self.valimuisti.tallenna(avain, raaka)
            haettu = time.time()
        else:
            haettu = self.valimuisti.haettu(avain) or time.time()
        self.hakuajat.setdefault(polku, []).append(haettu)
        df = jsonstat2_dataframeksi(raaka)
        df.insert(0, "taulu", polku)
        return df


# ------------------------------------------------------------------ json-stat


# Tyhjän solun merkinnät, jotka tarkoittavat nollaa eivätkä puuttuvaa tietoa.
# Todennettu Verohallinnon aineistosta: kun osituksen ainoat tyhjät solut on
# merkitty '-', luokkien summa täsmää Yhteensä-riviin. '..' (salassapito) sen
# sijaan jättää summan vajaaksi, joten sitä ei saa tulkita nollaksi.
NOLLAMERKINNAT: tuple[str, ...] = ("-",)


def jsonstat2_dataframeksi(
    js: dict[str, Any], nollamerkinnat: Sequence[str] = NOLLAMERKINNAT
) -> pd.DataFrame:
    """Muuntaa json-stat2-vastauksen pitkäksi DataFrameksi.

    Sarakkeet: yksi per dimensio (koodi) + '<dimensio>_teksti' + 'arvo' + 'tila'.
    Solut, joiden tilamerkintä on `nollamerkinnat`-listalla, tulkitaan nolliksi.
    """
    dimensio = js.get("dimension", {})
    tunnukset = js.get("id") or dimensio.get("id") or []
    koot = js.get("size") or dimensio.get("size") or []
    if not tunnukset:
        raise PxWebVirhe("json-stat-vastauksesta puuttuu dimensiolista")

    koodilistat: list[list[str]] = []
    tekstilistat: list[list[str]] = []
    for tunnus in tunnukset:
        kategoria = dimensio.get(tunnus, {}).get("category", {})
        indeksi = kategoria.get("index", {})
        nimiot = kategoria.get("label", {})
        if isinstance(indeksi, dict):
            koodit = [k for k, _ in sorted(indeksi.items(), key=lambda kv: kv[1])]
        else:  # lista
            koodit = list(indeksi)
        koodilistat.append(koodit)
        tekstilistat.append([str(nimiot.get(k, k)) for k in koodit])

    odotettu = math.prod(koot) if koot else math.prod(len(k) for k in koodilistat)
    arvot = js.get("value", [])
    if isinstance(arvot, dict):  # harva esitys
        taydet: list[Any] = [None] * odotettu
        for k, v in arvot.items():
            taydet[int(k)] = v
        arvot = taydet
    if len(arvot) != odotettu:
        raise PxWebVirhe(
            f"json-stat: arvoja {len(arvot)}, odotettiin {odotettu} "
            f"(dimensiot {list(zip(tunnukset, koot))})"
        )

    tila = js.get("status", {})
    if isinstance(tila, list):
        tila = {str(i): v for i, v in enumerate(tila) if v is not None}

    rivit = []
    for i, yhdistelma in enumerate(itertools.product(*[range(len(k)) for k in koodilistat])):
        rivi: dict[str, Any] = {}
        for d, (tunnus, sij) in enumerate(zip(tunnukset, yhdistelma)):
            rivi[tunnus] = koodilistat[d][sij]
            rivi[f"{tunnus}_teksti"] = tekstilistat[d][sij]
        rivi["arvo"] = arvot[i]
        rivi["tila"] = tila.get(str(i))
        rivit.append(rivi)

    df = pd.DataFrame(rivit)
    if "arvo" in df:
        df["arvo"] = pd.to_numeric(df["arvo"], errors="coerce")
        if nollamerkinnat and "tila" in df:
            nolla = df["arvo"].isna() & df["tila"].isin(list(nollamerkinnat))
            df.loc[nolla, "arvo"] = 0.0
    return df


def kaikki_arvot(meta: Metatiedot, koodi: str) -> list[str]:
    m = meta.muuttuja(koodi)
    return list(m.arvot) if m else []


def yhdista(kehykset: Iterable[pd.DataFrame]) -> pd.DataFrame:
    kehykset = [k for k in kehykset if k is not None and not k.empty]
    return pd.concat(kehykset, ignore_index=True) if kehykset else pd.DataFrame()
