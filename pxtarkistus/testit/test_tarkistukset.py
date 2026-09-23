"""Tarkistuslogiikan todennus simuloidulla aineistolla.

Aja: python testit/test_tarkistukset.py   (tai pytest testit/)

Simulaattoriin on istutettu neljä tunnettua virhettä. Testit varmistavat,
että jokainen löytyy – ja ettei virheettömistä kohdista synny vääriä
hälytyksiä.
"""

from __future__ import annotations

import json
import sys
import tempfile

import pandas as pd
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(JUURI))

from pxtarkistus.inventaario import tunnista_roolit  # noqa: E402
from pxtarkistus.raportti import kirjoita_excel, konsoliyhteenveto  # noqa: E402
from pxtarkistus.tarkistukset import (  # noqa: E402
    Toleranssi,
    havainnot_kehykseksi,
    tarkista_aikasarja,
    tarkista_erahierarkia,
    tarkista_osasummat,
    tarkista_ristiin,
)
from testit.simulaattori import (  # noqa: E402
    SimuloituAsiakas,
    VIRHE_OSASUMMA,
    VIRHE_RISTIIN,
)

LUOKITUKSET = {
    "Alue": {"yhteensa": "000", "tasot": {"maakunnat": "^[0-9]{2}$", "kunnat": "^[0-9]{3}$"}},
    "Perhetyyppi": {"yhteensa": "SS"},
}
TAULUT = ["testi/alue.px", "testi/perhetyyppi.px"]


def _roolit(asiakas):
    return [tunnista_roolit(asiakas.metatiedot(p), LUOKITUKSET) for p in TAULUT]


def _asiakas(tmp: Path) -> SimuloituAsiakas:
    return SimuloituAsiakas(valimuisti_hakemisto=tmp / "valimuisti")


def test_roolit_tunnistetaan():
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        roolit = _roolit(asiakas)
        alue = roolit[0]
        assert alue.aika.koodi == "Verovuosi"
        assert alue.era.koodi == "Erä"
        assert alue.tunnusluku.koodi == "Tunnusluvut"
        assert [l.koodi for l in alue.luokittelut] == ["Alue"]
        l = alue.luokittelu("Alue")
        assert l.yhteensa == "000"
        assert {o.nimi for o in l.ositukset} == {"maakunnat", "kunnat"}
        assert all(not o.paateltu for o in l.ositukset)
        print("OK  roolit ja luokitukset tunnistetaan oikein")


def test_ristiin_loytaa_poikkeaman():
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        havainnot, data = tarkista_ristiin(
            asiakas,
            _roolit(asiakas),
            "2024",
            tunnusluvut=["Sum", "N"],
            toleranssi=Toleranssi(),
            vertailutaulu="testi/alue.px",
        )
        poikkeamat = [h for h in havainnot if h.tarkistus == "ristiin" and h.ero]
        assert len(poikkeamat) == 1, f"odotettiin 1 poikkeamaa, saatiin {len(poikkeamat)}"
        h = poikkeamat[0]
        assert h.era == VIRHE_RISTIIN[1] and h.tunnusluku == "Sum"
        assert abs(h.ero - VIRHE_RISTIIN[4]) < 1.0
        assert h.kohde == "testi/perhetyyppi.px"
        # muut erät ja N-tunnusluku eivät saa hälyttää
        assert not data.empty
        print(f"OK  ristiintarkistus löysi istutetun {VIRHE_RISTIIN[4]:,.0f} euron eron".replace(",", " "))


def test_ristiin_koko_aikasarjalta():
    """Monivuotinen vertailu löytää saman yhden poikkeaman eikä keksi muita."""
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        from testit.simulaattori import VUODET

        havainnot, data = tarkista_ristiin(
            asiakas,
            _roolit(asiakas),
            VUODET,
            tunnusluvut=["Sum", "N"],
            vertailutaulu="testi/alue.px",
        )
        poikkeamat = {(h.era, h.verovuosi) for h in havainnot if h.ero}
        # 2024: istutettu näkymien välinen ero.
        # 2022: istutettu aikasarjahyppy tehtiin vain aluetauluun, joten se
        #       näkyy myös näkymien välisenä erona – juuri niin kuin pitääkin.
        assert poikkeamat == {("HVT_TULOT_80", "2024"), ("HVT_TULOT_50", "2022")}, poikkeamat
        # aluetaulusta puuttuva 2021-arvo on julkaistu perhetyyppitaulussa
        puuttuvat = [h for h in havainnot if "puuttuu tästä näkymästä" in h.kuvaus]
        assert any(
            h.verovuosi == "2021" and h.kohde == "testi/alue.px" and h.era == "HVT_VEROT_10"
            for h in puuttuvat
        ), puuttuvat
        # sama puute kootaan yhdeksi riviksi taulua ja erää kohden, merkintä kerrotaan
        assert len(puuttuvat) == 1 and puuttuvat[0].tunnusluku == "Sum", puuttuvat
        assert "ilman merkintää" in puuttuvat[0].kuvaus and "nan" not in puuttuvat[0].kuvaus
        assert set(data.verovuosi) == set(VUODET)
        # Ilman vertailutaulua 2022:n ero on 1 vs 1 -tilanne: kumpaakaan
        # näkymää ei syytetä, vaan ristiriita on yksi havainto molempine arvoineen.
        havainnot2, _ = tarkista_ristiin(asiakas, _roolit(asiakas), VUODET, tunnusluvut=["Sum", "N"])
        tasan = [h for h in havainnot2 if h.ero and (h.era, h.verovuosi) == ("HVT_TULOT_50", "2022")]
        assert len(tasan) == 1, tasan
        assert tasan[0].vertailukohta == "ei enemmistöä (1 vs 1)", tasan[0]
        assert "testi/alue.px" in tasan[0].kohde and "testi/perhetyyppi.px" in tasan[0].kohde
        assert tasan[0].saatu > tasan[0].odotettu and "=" in tasan[0].kuvaus
        # enemmistötilanne syyttää yhä poikkeavaa: kun postinumerotaulun
        # Yhteensä tunnistetaan, 2024:ssä on kaksi näkymää vs. yksi
        luok = {**LUOKITUKSET, "Kuntapostinumero": {"yhteensa": "SSS"}}
        roolit3 = [tunnista_roolit(asiakas.metatiedot(p), luok) for p in TAULUT + ["testi/postinum.px"]]
        havainnot3, _ = tarkista_ristiin(asiakas, roolit3, ["2024"], tunnusluvut=["Sum"])
        enem = [h for h in havainnot3 if h.ero and (h.era, h.verovuosi) == ("HVT_TULOT_80", "2024")]
        assert [(h.kohde, h.vertailukohta) for h in enem] == [("testi/perhetyyppi.px", "2 muuta näkymää")], enem
        print("OK  ristiintarkistus kattaa koko aikasarjan ja löytää yksipuolisen tyhjän solun")
        print("OK  tasatilanteessa (1 vs 1) ei syytetä kumpaakaan näkymää")


def test_osasummat_loytaa_vain_vaaran_osituksen():
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        havainnot, data = tarkista_osasummat(
            asiakas, _roolit(asiakas), "2024", tunnusluvut=["Sum", "N"], toleranssi=Toleranssi()
        )
        virheet = [h for h in havainnot if h.vakavuus == "virhe"]
        assert len(virheet) == 1, f"odotettiin 1 virhettä, saatiin {len(virheet)}: {virheet}"
        h = virheet[0]
        assert h.era == VIRHE_OSASUMMA[1]
        assert "[maakunnat]" in h.kohde, h.kohde
        assert abs(h.ero - VIRHE_OSASUMMA[4]) < 1.0
        # kuntaositus samassa taulussa täsmää -> ei havaintoa
        assert not any("[kunnat]" in x.kohde for x in virheet)
        assert not data.empty
        # koko aikasarja yhdellä kyselyllä: sama yksi virhe, oikea vuosi, data joka vuodelta
        from testit.simulaattori import VUODET

        havainnot2, data2 = tarkista_osasummat(
            asiakas, _roolit(asiakas), VUODET, tunnusluvut=["Sum", "N"], toleranssi=Toleranssi()
        )
        virheet2 = sorted({(h.era, h.verovuosi) for h in havainnot2 if h.vakavuus == "virhe"})
        # 2022:n aikasarjahyppy istutettiin vain Yhteensä-riviin (Alue=000),
        # joten alueiden summa ei täsmää siihen – oikea löydös sekin
        assert virheet2 == [("HVT_TULOT_50", "2022"), (VIRHE_OSASUMMA[1], VIRHE_OSASUMMA[2])], virheet2
        assert set(data2.verovuosi) == set(VUODET), set(data2.verovuosi)
        print("OK  osasummatarkistus löysi maakuntatason eron eikä hälyttänyt kuntatasosta")
        print("OK  osasummatarkistus koko aikasarjalta löytää saman virheen oikealta vuodelta")


def test_aikasarja_loytaa_hypyn_puuttuvan_ja_revision():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        asiakas = _asiakas(tmp)
        vedos = tmp / "vedos.json"
        # ensimmäinen ajo kirjoittaa tilannevedoksen
        havainnot, data, uusi = tarkista_aikasarja(
            asiakas, _roolit(asiakas), tunnusluvut=["Sum"], tilannevedos=vedos
        )
        hypyt = [h for h in havainnot if "Vuosimuutos" in h.kuvaus]
        puuttuvat = [h for h in havainnot if "puuttuu keskeltä" in h.kuvaus]
        assert any(h.era == "HVT_TULOT_50" and h.verovuosi == "2022" for h in hypyt), hypyt
        assert any(h.era == "HVT_VEROT_10" and h.verovuosi == "2021" for h in puuttuvat), puuttuvat
        assert not [h for h in havainnot if h.tarkistus == "revisio"]

        # muutetaan vedosta -> toisen ajon pitää havaita revisio
        vanha = json.loads(vedos.read_text(encoding="utf-8"))
        avain = "testi/alue.px|HVT_TULOT_80|2023|Sum"
        vanha["arvot"][avain] = vanha["arvot"][avain] - 1_000_000
        vedos.write_text(json.dumps(vanha, ensure_ascii=False), encoding="utf-8")

        havainnot2, _, _ = tarkista_aikasarja(
            asiakas, _roolit(asiakas), tunnusluvut=["Sum"], tilannevedos=vedos
        )
        revisiot = [h for h in havainnot2 if h.tarkistus == "revisio"]
        assert len(revisiot) == 1, revisiot
        assert revisiot[0].era == "HVT_TULOT_80" and revisiot[0].verovuosi == "2023"
        assert abs(revisiot[0].ero - 1_000_000) < 1.0
        # muutoksen ajoitus: simulaattorin listauksessa ei ole päivitysaikaa
        assert "ei ole päivitysaikaa" in revisiot[0].kuvaus, revisiot[0].kuvaus
        assert revisiot[0].vertailukohta.startswith("tilannevedos "), revisiot[0].vertailukohta
        assert not data.empty
        print("OK  aikasarjatarkistus löysi hypyn, puuttuvan arvon ja revision")


def test_vanha_tilannevedos_ei_tuota_valerevisioita():
    """Eri tulkintaversiolla kirjoitettua vedosta ei saa verrata uuteen."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        asiakas = _asiakas(tmp)
        vedos = tmp / "vanha.json"
        # vanha litteä muoto ilman versiotietoa, arvot tahallaan pielessä
        vedos.write_text(
            json.dumps({"testi/alue.px|HVT_TULOT_80|2023|Sum": 1.0}), encoding="utf-8"
        )
        havainnot, _, _ = tarkista_aikasarja(
            asiakas, _roolit(asiakas), tunnusluvut=["Sum"], tilannevedos=vedos
        )
        revisiot = [h for h in havainnot if h.tarkistus == "revisio"]
        assert len(revisiot) == 1 and "eri tulkintaversiolla" in revisiot[0].kuvaus, revisiot
        # vedos on kirjoitettu uudelleen nykymuotoon
        uusi = json.loads(vedos.read_text(encoding="utf-8"))
        assert uusi["versio"] >= 2 and "arvot" in uusi
        print("OK  vanhentunut tilannevedos ei tuota valerevisioita")


def test_erahierarkia_vahvistetaan_ja_rikkoutuminen_loytyy():
    """Additiivinen suhde opitaan historiasta ja sen rikkoutuminen löytyy."""
    from testit.simulaattori import VIRHE_HIERARKIA

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        asiakas = _asiakas(tmp)
        saannot = tmp / "erahierarkia.json"
        havainnot, data = tarkista_erahierarkia(
            asiakas, _roolit(asiakas), "2024", saannot_tiedosto=saannot, vahimmaisvuodet=3
        )
        tilat = dict(zip(data.ylaera, data.tila))
        # 5 = 5.1 + 5.2 pitää joka vuosi paitsi kohdevuonna -> vahvistetaan
        assert tilat["HVT_TULOT_600"] == "vahvistettu", tilat
        # 2 vs 2.1 ei ole additiivinen (alaerä on osa yläerää) -> ei valvontaan
        assert tilat["HVT_TULOT_50"] == "ei additiivinen", tilat

        virheet = [h for h in havainnot if h.vakavuus == "virhe"]
        assert len(virheet) == 1, virheet
        h = virheet[0]
        assert h.era == VIRHE_HIERARKIA[0] and h.verovuosi == VIRHE_HIERARKIA[1]
        assert abs(h.ero + VIRHE_HIERARKIA[2]) < 1.0, h.ero  # summa jää yläerästä

        # sääntötiedosto kirjoitettiin ja sisältää vain vahvistetun suhteen
        tallennetut = json.loads(saannot.read_text(encoding="utf-8"))["suhteet"]
        assert list(tallennetut) == ["HVT_TULOT_600"]
        assert tallennetut["HVT_TULOT_600"]["lapset"] == ["HVT_TULOT_610", "HVT_TULOT_620"]

        # Sama virhe menneenä vuonna: kun tarkasteltava vuosi on 2023, 2024:n
        # poikkeama estää vahvistuksen, mutta ainoana poikkeavana vuotena se
        # raportoidaan varoituksena.
        havainnot2, data2 = tarkista_erahierarkia(asiakas, _roolit(asiakas), "2023", vahimmaisvuodet=3)
        tilat2 = dict(zip(data2.ylaera, data2.tila))
        assert tilat2["HVT_TULOT_600"] == "yksi poikkeava vuosi (2024)", tilat2
        assert tilat2["HVT_TULOT_50"] == "ei additiivinen", tilat2
        yksi = [h for h in havainnot2 if h.era == "HVT_TULOT_600"]
        assert [(h.vakavuus, h.verovuosi) for h in yksi] == [("varoitus", "2024")], yksi
        assert abs(yksi[0].ero + VIRHE_HIERARKIA[2]) < 1.0
        print("OK  erähierarkia vahvistetaan historiasta ja rikkoutuminen löytyy")
        print("OK  erähierarkian ainoa poikkeava vuosi raportoidaan, vaikka suhde ei vahvistu")


def test_kiinnitys_rajaa_perusjoukon():
    """Kiinnitetty muuttuja ei ole luokittelu vaan rajaa kaikki kyselyt."""
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        meta = asiakas.metatiedot("testi/perhetyyppi.px")
        r = tunnista_roolit(meta, LUOKITUKSET, {"Perhetyyppi": "Yksin asuvat"})
        assert r.kiinnitetyt == {"Perhetyyppi": "1"}
        assert r.luokittelu("Perhetyyppi") is None
        assert r.yhteensa_valinta()["Perhetyyppi"] == ["1"]
        assert r.tunniste.endswith("[Perhetyyppi=Yksin asuvat]"), r.tunniste
        # rajattu taulu hakee kiinnitetyn luokan, ei Yhteensä-riviä
        _, data = tarkista_ristiin(
            asiakas, [tunnista_roolit(asiakas.metatiedot("testi/alue.px"), LUOKITUKSET), r],
            "2024", tunnusluvut=["Sum"],
        )
        rajattu = data[data.taulu == r.tunniste]
        kokonais = data[data.taulu == "testi/alue.px"]
        yht = float(kokonais[kokonais.era == "HVT_TULOT_50"].arvo.iloc[0])
        osa = float(rajattu[rajattu.era == "HVT_TULOT_50"].arvo.iloc[0])
        assert abs(osa - round(yht / 4)) < 1, (osa, yht)
        # tuntematon arvo -> selkeä virhe
        from pxtarkistus.inventaario import KiinnitysVirhe
        try:
            tunnista_roolit(meta, LUOKITUKSET, {"Perhetyyppi": "Ei ole"})
            raise AssertionError("kiinnitysvirhe puuttui")
        except KiinnitysVirhe:
            pass
        print("OK  kiinnitys rajaa perusjoukon (siltavertailu kansioiden välillä)")


def test_tekstiositus_ja_yhteensa_paattely():
    """Seurakunta-tyyppinen ositus arvoteksteistä; Yhteensä löytyy vaikka asetus on eri."""
    from pxtarkistus.inventaario import Muuttuja, era_tekstit, ositukset, yhteensa_koodi, Roolit
    from pxtarkistus.pxclient import Metatiedot

    srk = Muuttuja(
        "Seurakunta", "Seurakunta",
        ["S", "EI", "EVL", "ORT", "a", "b", "c"],
        ["Yhteensä", "Ei-kirkollisverovelvolliset yhteensä", "Evankelis-luterilaiset seurakunnat yhteensä",
         "Ortodoksiset seurakunnat yhteensä", "Alajärven seurakunta", "Helsingin ortodoksinen seurakunta",
         "Askolan seurakunta (lakkautettu 31.12.2024)"],
    )
    import yaml
    saannot = yaml.safe_load((JUURI / "asetukset.yaml").read_text(encoding="utf-8"))["luokitukset"]
    y = yhteensa_koodi(srk, saannot)
    assert y == "S"
    o = {x.nimi: (x.koodit, x.paateltu) for x in ositukset(srk, y, saannot)}
    assert o["paajako"] == (["EI", "EVL", "ORT"], False), o
    assert o["seurakunnat"] == (["EI", "a", "b", "c"], True), o

    tl = Muuttuja("Tuloluokka", "Tuloluokka", ["SSS", "1", "2"], ["(total)", "0 - 4 999", "5 000 - 9 999"])
    assert yhteensa_koodi(tl, saannot) == "SSS"  # asetus sanoo SS, taulussa SSS
    tl2 = Muuttuja("Tuloluokka", "Tuloluokka", ["X", "1"], ["Tuloluokka yhteensä", "0 – 4 999"])
    assert yhteensa_koodi(tl2, {}) == "X"
    vali = Muuttuja("K", "K", ["a", "b"], ["2.3 Tutkinnon suorittaneita yhteensä", "2.3.1 Toinen aste"])
    assert yhteensa_koodi(vali, {}) is None  # numeroitu välisumma ei ole Yhteensä

    # numeroitu erän nimi voittaa numeroimattoman (postinumerotaulut)
    def rr(tekstit):
        m = Muuttuja("Erä", "Erä", ["HVT_TULOT_50"], tekstit)
        return Roolit(meta=Metatiedot("t", "t"), aika=None, era=m, tunnusluku=None, luokittelut=[])
    assert era_tekstit([rr(["Tulot yhteensä"]), rr(["2. Tulot yhteensä"])])["HVT_TULOT_50"] == "2. Tulot yhteensä"
    print("OK  tekstiositus, Yhteensä-päättely ja erien nimet")


def test_kunta_vs_postinumerot():
    """Kunnan luku aluenäkymässä = sen postinumeroalueiden summa."""
    from pxtarkistus.tarkistukset import tarkista_alueet
    from testit.simulaattori import VIRHE_ALUE

    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        luok = dict(LUOKITUKSET)
        roolit = [
            tunnista_roolit(asiakas.metatiedot("testi/alue.px"), luok),
            tunnista_roolit(asiakas.metatiedot("testi/postinum.px"), luok),
        ]
        havainnot, data = tarkista_alueet(asiakas, roolit, tunnusluvut=["Sum", "N"])
        virheet = [h for h in havainnot if h.vakavuus == "virhe"]
        assert len(virheet) == 1, [(h.kohde, h.era, h.ero) for h in virheet]
        h = virheet[0]
        assert h.kohde.startswith("006 Espoo"), h.kohde
        assert (h.era, h.verovuosi, h.tunnusluku) == VIRHE_ALUE[:3]
        assert abs(h.ero - VIRHE_ALUE[4]) < 1.0
        # molemmat kunnat, kaikki erät, Sum ja N vertailtiin
        assert set(data.kunta) == {"005", "006"} and set(data.tunnusluku) == {"Sum", "N"}
        print("OK  kunnan luku vs. postinumeroalueiden summa: istutettu 5 000 euron ero löytyi")


def test_aluetaulu_valitaan_vuoden_mukaan():
    """Erä haetaan aluetaulusta, jossa kyseinen vuosi on – ei ensimmäisestä."""
    from pxtarkistus.inventaario import Roolit
    from pxtarkistus.pxclient import Metatiedot, Muuttuja
    from pxtarkistus.tarkistukset import valitse_aluetaulut

    def taulu(nimi, erat, vuodet):
        return Roolit(
            meta=Metatiedot(nimi, nimi), aika=Muuttuja("Verovuosi", "V", vuodet, vuodet),
            era=Muuttuja("Erä", "E", erat, erat), tunnusluku=None, luokittelut=[],
        )

    vanha = taulu("alue_101_2014", ["A", "B"], ["2014"])       # vain 2014
    perus = taulu("tulot_102", ["A"], ["2014", "2024"])        # kaikki vuodet, vain A
    uusi = taulu("alue_101_2024", ["A", "B"], ["2024"])        # vain 2024
    jako = {r.meta.polku: e for r, e in valitse_aluetaulut(["A", "B"], "2024", [vanha, perus, uusi])}
    assert jako == {"tulot_102": ["A"], "alue_101_2024": ["B"]}, jako
    print("OK  kuntatarkistus valitsee aluetaulun, jossa verovuosi todella on")


def test_torjuttu_kysely_puolitetaan():
    """Jos palvelin torjuu liian suuren kyselyn, se jaetaan kahtia."""
    from pxtarkistus.pxclient import PxWebVirhe

    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        alkuperainen = asiakas._hae_yksi
        kutsut = []

        def rajoitettu(polku, meta, valinta):
            solut = 1
            for v in valinta.values():
                solut *= len(v)
            kutsut.append(solut)
            if solut > 12:
                raise PxWebVirhe(f"POST {polku} -> HTTP 403: Too many values")
            return alkuperainen(polku, meta, valinta)

        asiakas._hae_yksi = rajoitettu
        meta = asiakas.metatiedot("testi/alue.px")
        df = asiakas.hae(
            "testi/alue.px",
            {"Alue": ["000"], "Erä": ["HVT_TULOT_50", "HVT_TULOT_80", "HVT_VEROT_10", "HVT_TULOT_600"],
             "Verovuosi": ["2023", "2024"], "Tunnusluvut": ["Sum", "N"]},
            meta=meta,
        )
        assert len(df) == 16 and df["arvo"].notna().all(), len(df)
        assert kutsut[0] == 16 and max(kutsut[1:]) <= 12, kutsut
        print("OK  palvelimen torjuma kysely puolitetaan ja tulos kootaan")


def test_paivitetty_taulu_haetaan_uudelleen():
    """Välimuistin vastaus hylätään, jos taulu on päivitetty palvelimella sen jälkeen."""
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        meta = asiakas.metatiedot("testi/alue.px")
        valinta = {"Alue": ["000"], "Erä": ["HVT_TULOT_80"], "Verovuosi": ["2024"], "Tunnusluvut": ["Sum"]}

        def postit() -> int:
            return sum(1 for m, _ in asiakas.kutsut if m == "POST")

        asiakas.hae("testi/alue.px", valinta, meta=meta)
        asiakas.hae("testi/alue.px", valinta, meta=meta)
        assert postit() == 1  # toinen haku välimuistista
        # listaus kertoo taulun päivittyneen vasta välimuistiin tallentamisen jälkeen
        myohemmin = pd.Timestamp.now(tz="Europe/Helsinki") + pd.Timedelta(minutes=30)
        asiakas.paivitysajat["testi/alue.px"] = myohemmin.strftime("%Y-%m-%dT%H:%M:%S")
        asiakas.hae("testi/alue.px", valinta, meta=meta)
        assert postit() == 2, asiakas.kutsut
        # päivitetty kauan sitten: välimuisti kelpaa
        asiakas.paivitysajat["testi/alue.px"] = "2020-01-01T08:00:00"
        asiakas.hae("testi/alue.px", valinta, meta=meta)
        assert postit() == 2
        # ei päivitysaikaa listauksessa: lyhyempi voimassaolo
        asiakas.paivitysajat["testi/alue.px"] = None
        asiakas.ilman_paivitysaikaa_s = 0.0
        asiakas.hae("testi/alue.px", valinta, meta=meta)
        assert postit() == 3
        assert len(asiakas.hakuajat["testi/alue.px"]) == 5
        print("OK  palvelimella päivitetyn taulun vanha vastaus ei jää välimuistista käyttöön")


def test_osasummat_vuosikohtainen_taulu():
    """Taulu, josta pyydetty vuosi puuttuu, tarkistetaan omalta uusimmalta vuodeltaan."""
    luok = {**LUOKITUKSET, "Kuntapostinumero": {
        "yhteensa": "SSS", "tasot": {"kunnat": "^[0-9]{3}$", "postinumeroalueet": "^[0-9]{3}_"}}}
    with tempfile.TemporaryDirectory() as d:
        asiakas = _asiakas(Path(d))
        roolit = [tunnista_roolit(asiakas.metatiedot("testi/postinum.px"), luok)]  # vain 2024
        havainnot, data = tarkista_osasummat(asiakas, roolit, "2023", tunnusluvut=["Sum"])
        assert not [h for h in havainnot if "Datahaku epäonnistui" in h.kuvaus], havainnot
        assert set(data.verovuosi) == {"2024"}, set(data.verovuosi)
        virheet = [h for h in havainnot if h.vakavuus == "virhe"]
        assert virheet and all(h.verovuosi == "2024" for h in virheet), virheet
        print("OK  verovuosikohtainen taulu tarkistetaan omalta vuodeltaan")


def test_komentorivi_ajaa_useamman_joukon():
    """Koko putki: kansiopohjaiset joukot, kiinnitys, yhteisraportti."""
    import types
    import yaml
    import aja

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        asetukset = {
            "luokitukset": LUOKITUKSET,
            "joukot": {
                # otsikkorajaus: perhetyyppitaulu kuuluu "toiseen perusjoukkoon"
                "kaikki_testi": {"kuvaus": "kansio", "kansiot": ["testi"], "otsikko_ei": "perhetyyp"},
                "silta_testi": {
                    "kuvaus": "kiinnitys",
                    "taulut": [
                        "testi/alue.px",
                        {"polku": "testi/perhetyyppi.px", "kiinnita": {"Perhetyyppi": "SS"}},
                    ],
                },
                "rikki": {"kuvaus": "ei tauluja", "taulut": [], "kansiot": []},
                "vain_perhe": {"kuvaus": "otsikolla valittu",
                               "kansiot": [{"polku": "testi", "otsikko": "perhetyyp"}]},
            },
            "aikasarja": {"tilannevedos": "vedokset/{joukko}.json"},
            "erahierarkia": {"saannot": "saannot/eh_{joukko}.json"},
        }
        vanha_asiakas, vanha_juuri = aja.tee_asiakas, aja.JUURI
        aja.tee_asiakas = lambda a: SimuloituAsiakas(valimuisti_hakemisto=tmp / "vm")
        aja.JUURI = tmp
        try:
            args = types.SimpleNamespace(
                joukko="kaikki", verovuosi="2024", tunnusluvut=None, tarkistukset=None,
                erat_tiedosto=None, max_erat=100, vuodet="kaikki", raportti=str(tmp / "r"),
                tilannevedos=None, erahierarkia_saannot=None,
            )
            paluu = aja.komento_tarkista(args, asetukset)
        finally:
            aja.tee_asiakas, aja.JUURI = vanha_asiakas, vanha_juuri
        assert paluu == 1  # istutetut virheet -> virhe-taso
        raportit = sorted((tmp / "r").glob("*.xlsx"))
        # jokaisesta onnistuneesta joukosta oma raportti + yhteisraportti
        import re as _re
        yhteinen = [r for r in raportit if _re.match(r"^pxtarkistus_kaikki_\d{4}_", r.name)]
        osat = [r for r in raportit if r not in yhteinen]
        assert len(yhteinen) == 1 and len(osat) == 3, [r.name for r in raportit]
        h = pd.read_excel(yhteinen[0], "Havainnot")
        assert set(h["joukko"]) >= {"kaikki_testi", "silta_testi"}, set(h["joukko"])
        yv0 = pd.read_excel(yhteinen[0], "Yhteenveto")
        rivit = dict(zip(yv0.iloc[:, 0].astype(str), yv0.iloc[:, 1].astype(str)))
        # otsikkorajaus: kansiossa 3 taulua, perhetyyppi pois -> 2; vain_perhe -> 1
        assert rivit["– kaikki_testi"].startswith("2 taulua"), rivit
        assert rivit["– vain_perhe"].startswith("1 taulua"), rivit
        assert (tmp / "vedokset" / "kaikki_testi.json").exists()
        assert (tmp / "vedokset" / "silta_testi.json").exists()
        yv = pd.read_excel(yhteinen[0], "Yhteenveto")
        assert any("OHITETTU rikki" == str(t) for t in yv.iloc[:, 0]), yv
        # aineisto vain joukkokohtaisina tiedostoina, yhteisraportissa taustaluvut
        assert not list((tmp / "r").glob(f"{yhteinen[0].stem}_data_*")), "yhteisraportti monisti aineiston"
        assert list((tmp / "r").glob("pxtarkistus_kaikki_testi_*_data_ristiin.csv.gz"))
        assert "Ristiriitojen taustaluvut" in pd.ExcelFile(yhteinen[0]).sheet_names
        taulut = pd.read_excel(yhteinen[0], "Taulut")
        assert {"Joukko", "Taulu", "Päivitetty (PxWeb)", "Data haettu"} <= set(taulut.columns), taulut.columns
        assert len(taulut) == 5, taulut  # 2 + 2 + 1 taulua kolmesta onnistuneesta joukosta
        print("OK  komentorivi ajaa useamman joukon yhteiseen raporttiin ja ohittaa rikkinäisen")


def test_raportti_syntyy():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        asiakas = _asiakas(tmp)
        roolit = _roolit(asiakas)
        h1, d1 = tarkista_ristiin(asiakas, roolit, "2024", vertailutaulu="testi/alue.px")
        h2, d2 = tarkista_osasummat(asiakas, roolit, "2024")
        h3, d3, _ = tarkista_aikasarja(asiakas, roolit, tunnusluvut=["Sum"])
        havainnot = havainnot_kehykseksi(h1 + h2 + h3)
        meta = {"Joukko": "testi", "Verovuosi": "2024", "Tauluja": len(roolit)}
        tiedosto = kirjoita_excel(
            tmp / "raportti.xlsx", havainnot, {"ristiin": d1, "osasummat": d2, "aikasarja": d3}, meta
        )
        assert tiedosto.exists() and tiedosto.stat().st_size > 5000
        # aineisto erillisinä pakattuina tiedostoina, ei Excelissä
        from openpyxl import load_workbook
        valilehdet = load_workbook(tiedosto, read_only=True).sheetnames
        assert not any(v.startswith("Data ") for v in valilehdet), valilehdet
        assert "Ristiriitojen taustaluvut" in valilehdet, valilehdet
        for laji in ("ristiin", "osasummat", "aikasarja"):
            dt = tmp / f"raportti_data_{laji}.csv.gz"
            assert dt.exists() and len(pd.read_csv(dt, sep=";")) > 0, dt
        # vanhan muotoisen (Data-välilehdellisen) raportin tiivistys
        from pxtarkistus.raportti import tiivista_raportti
        vanha = tmp / "vanha.xlsx"
        with pd.ExcelWriter(vanha) as w:
            havainnot.rename(columns={"ero_pros": "Ero-%", "tarkistus": "Tarkistus", "vakavuus": "Vakavuus",
                                      "era": "Erätunnus", "verovuosi": "Verovuosi", "tunnusluku": "Tunnusluku"}
                             ).assign(**{"Ero-%": havainnot["ero_pros"] / 100}).to_excel(w, sheet_name="Havainnot", index=False)
            d1.to_excel(w, sheet_name="Data ristiin", index=False)
        tiivis = tiivista_raportti(vanha)
        assert "Data ristiin" not in load_workbook(tiivis, read_only=True).sheetnames
        assert (tmp / "vanha_tiivis_data_ristiin.csv.gz").exists()
        pros = pd.read_excel(tiivis, "Havainnot")["Ero-%"].dropna()
        alkup = (havainnot["ero_pros"].dropna() / 100).tolist()
        assert all(abs(a - b) < 1e-12 for a, b in zip(sorted(pros), sorted(alkup))), (pros.tolist(), alkup)
        teksti = konsoliyhteenveto(havainnot, meta)
        assert "PXTARKISTUS" in teksti and "virhe" in teksti
        print("OK  Excel-raportti ja konsolituloste syntyvät")
        print()
        print(teksti)


def test_hierarkia_paatellaan_arvoteksteista():
    """Koulutusasteen kaltainen kolmitasoinen luokitus pitää purkaa tasoiksi."""
    from pxtarkistus.inventaario import Muuttuja, ositukset

    m = Muuttuja(
        koodi="Koulutusaste",
        teksti="Koulutusaste",
        arvot=["SSS", "K", "L", "X", "9", "3-8", "3", "4", "5", "6", "7", "8", "99"],
        arvotekstit=[
            "Yhteensä",
            "1. Kuolinpesä",
            "2. Luonnollinen henkilö",
            "2.1 Alaikäiset",
            "2.2 Täysi-ikäiset, ei perusasteen jälkeistä tutkintoa",
            "2.3 Tutkinnon suorittaneita täysi-ikäisiä yhteensä",
            "2.3.1 Toinen aste",
            "2.3.2 Erikoisammattikoulutusaste",
            "2.3.3 Alin korkea-aste",
            "2.3.4 Alempi korkeakouluaste",
            "2.3.5 Ylempi korkeakouluaste",
            "2.3.6 Tutkijakoulutusaste",
            "2.4 Ikä ja koulutusaste tuntematon",
        ],
    )
    o = ositukset(m, "SSS")
    tasot = {x.nimi: x.koodit for x in o}
    assert tasot["taso 1"] == ["K", "L"], tasot["taso 1"]
    assert tasot["taso 2"] == ["K", "X", "9", "3-8", "99"], tasot["taso 2"]
    assert tasot["taso 3"] == ["K", "X", "9", "3", "4", "5", "6", "7", "8", "99"], tasot["taso 3"]
    assert all(not x.paateltu for x in o)  # numeroitu hierarkia ei ole arvaus
    print("OK  hierarkkinen luokitus puretaan arvoteksteistä tasoiksi")


def test_pyoristysvara_vaimentaa_kohinan():
    """Muutaman euron ero 19 solun summassa on pyöristystä, ei virhe."""
    t = Toleranssi()
    assert not t.merkittava(-4.0, 1.27e11, solut=19)      # maakuntien summa
    assert not t.merkittava(160.0, 1.8e11, solut=320)     # kuntien summa
    assert t.merkittava(12_340.0, 1.27e11, solut=19)      # aito poikkeama
    # lukumääriin pyöristystä ei sovelleta
    assert t.merkittava(4.0, 5_520_149, solut=19, pyoristyva=False)
    print("OK  pyöristysvara vaimentaa eurokohinan mutta ei peitä aitoja eroja")


def test_viivamerkinta_tulkitaan_nollaksi():
    """'-' on nolla (ei havaintoja), '..' on salassapidon vuoksi puuttuva."""
    from pxtarkistus.pxclient import jsonstat2_dataframeksi

    js = {
        "class": "dataset",
        "id": ["Alue"],
        "size": [3],
        "dimension": {
            "Alue": {
                "category": {
                    "index": {"000": 0, "01": 1, "02": 2},
                    "label": {"000": "Yhteensä", "01": "Uusimaa", "02": "Varsinais-Suomi"},
                }
            }
        },
        "value": [1000.0, None, None],
        "status": {"1": "-", "2": ".."},
    }
    df = jsonstat2_dataframeksi(js)
    arvot = dict(zip(df["Alue"], df["arvo"]))
    assert arvot["01"] == 0.0, "'-' pitää tulkita nollaksi"
    assert pd.isna(arvot["02"]), "'..' pitää jäädä puuttuvaksi"
    # alkuperäinen merkintä säilyy, jotta raportti voi kertoa tyhjän solun syyn
    assert list(df["tila"])[1:] == ["-", ".."] and pd.isna(df["tila"].iloc[0])
    print("OK  '-' tulkitaan nollaksi ja '..' jää puuttuvaksi")


def test_jaetut_erat_ei_vaadi_kaikkia_tauluja():
    """Alue-näkymän tulo- ja verotaululla ei ole yhteisiä eriä – leikkaus olisi tyhjä."""
    from pxtarkistus.inventaario import Roolit, jaetut_erat
    from pxtarkistus.pxclient import Metatiedot, Muuttuja

    def r(koodit):
        m = Muuttuja("Erä", "Erä", koodit, koodit)
        return Roolit(meta=Metatiedot("t", "t"), aika=None, era=m, tunnusluku=None, luokittelut=[])

    roolit = [r(["A", "B"]), r(["C", "D"]), r(["A", "B", "C", "D", "E"])]
    assert jaetut_erat(roolit, vahintaan=2) == ["A", "B", "C", "D"]
    assert jaetut_erat(roolit, vahintaan=3) == []
    print("OK  erävalinta ei vaadi erän esiintymistä kaikissa tauluissa")


def aja_kaikki() -> int:
    testit = [
        test_roolit_tunnistetaan,
        test_hierarkia_paatellaan_arvoteksteista,
        test_pyoristysvara_vaimentaa_kohinan,
        test_viivamerkinta_tulkitaan_nollaksi,
        test_jaetut_erat_ei_vaadi_kaikkia_tauluja,
        test_ristiin_loytaa_poikkeaman,
        test_ristiin_koko_aikasarjalta,
        test_osasummat_loytaa_vain_vaaran_osituksen,
        test_aikasarja_loytaa_hypyn_puuttuvan_ja_revision,
        test_vanha_tilannevedos_ei_tuota_valerevisioita,
        test_erahierarkia_vahvistetaan_ja_rikkoutuminen_loytyy,
        test_kiinnitys_rajaa_perusjoukon,
        test_tekstiositus_ja_yhteensa_paattely,
        test_kunta_vs_postinumerot,
        test_aluetaulu_valitaan_vuoden_mukaan,
        test_torjuttu_kysely_puolitetaan,
        test_paivitetty_taulu_haetaan_uudelleen,
        test_osasummat_vuosikohtainen_taulu,
        test_komentorivi_ajaa_useamman_joukon,
        test_raportti_syntyy,
    ]
    virheita = 0
    for t in testit:
        try:
            t()
        except AssertionError as e:
            virheita += 1
            print(f"EPÄONNISTUI  {t.__name__}: {e}")
    print()
    print(f"{len(testit) - virheita}/{len(testit)} testiä läpi")
    return 1 if virheita else 0


if __name__ == "__main__":
    sys.exit(aja_kaikki())
