# Muutosloki

Merkittävät muutokset versioittain. Versionumero on `pxtarkistus.__version__`,
ja se kirjataan jokaisen raportin Yhteenveto-välilehdelle kenttään
"Ohjelmaversio" – niin vanhasta raportista näkee, millä logiikalla se on tehty.

Versiot 1.3.0 ja sitä uudemmat on kirjattu julkaisuhetkellä. Sitä vanhemmat
merkinnät on koottu jälkikäteen koodin perusteella, eikä niille ole tarkkaa
päivämäärää.

## 1.5.1 (2026-09-29)

Dokumentaatio ja paketointi; tarkistuslogiikka ennallaan.

- README kertoo heti alussa, että tarkistus kattaa tilaston kaikki 24 kansiota.
  Aiemmin tarkistus A alkoi esimerkillä näkymistä 06–12, mistä saattoi saada
  sen käsityksen, että vain ne tarkistetaan.
- README:hen taulukko kaikista komentorivivalitsimista.
- Välimuistin kuvauksesta poistettu vanhentunut luettelo tauluista, joilla ei
  ole päivitysaikaa – joukko vaihtuu sitä mukaa kun tauluja julkaistaan.
- `pyproject.toml`: lisenssi PEP 639 -muodossa (`license = "MIT"`), koska
  vanha taulukkomuoto poistuu setuptoolsista 2/2027.
- Kuittausten kenttävertailu käsittelee tyhjän arvon oikein (`fillna` ennen
  merkkijonomuunnosta).

## 1.5.0 (2026-09-28)

Toistuvan ajon työkalut: sama julkaisu tuottaa samat havainnot joka kerta, joten
olennaista on erottaa uusi tieto jo selvitetystä.

- **Kuittaukset**: `kuittaukset.yaml` (tai `--kuittaukset`) vaimentaa jo
  tutkitut havainnot perusteluineen. Kuitattu havainto ei katoa – se siirtyy
  raportin *Kuitatut*-välilehdelle eikä vaikuta lukumääriin tai paluuarvoon.
  `voimassa_asti` päättää kuittauksen automaattisesti, ja ajo huomauttaa
  säännöistä, jotka eivät enää osu mihinkään (havainto on korjattu).
- **`vertaa`-komento**: kahden raportin havainnot rinnakkain – uudet,
  poistuneet ja ne, joiden ero on muuttunut. Tulos konsoliin ja Exceliksi.
- **`--tuore`**: ohittaa levyvälimuistin ja hakee luvut uudelleen. Saman ajon
  sisällä toistuva sama kysely luetaan silti välimuistista, joten kyselyiden
  määrä ei moninkertaistu.
- **Palvelinvirheiden sieto**: HTTP 500/502/503/504 yritetään uudelleen
  porrastetusti (5 s, 15 s, 45 s). Yksi huoltokatko ei enää keskeytä tuntien
  ajoa. 4xx-vastaukset päätyvät edelleen suoraan virheeseen, jotta liian suuren
  kyselyn puolittaminen toimii.
- **Aikasarjan olennaisuusraja** `aikasarja.vahimmaisero`: tätä pienempi
  absoluuttinen vuosimuutos ei nouse esiin, vaikka prosenttimuutos olisi suuri.
  Sarjan päättyminen raportoidaan aina. Oletus 0 = kaikki mukaan, kuten ennen.
- **Asennettava paketti**: `pip install .` tuo komennon `pxtarkistus`.
  Käyttöliittymä on nyt `pxtarkistus/cli.py`; `python aja.py ...` toimii
  ennallaan. Suhteelliset polut (raportit, säännöt, vedokset) osuvat
  työhakemistoon, jos siinä on `asetukset.yaml`.
- Repoon `LICENSE` (MIT), `.gitignore`, `CHANGELOG.md`, `pyproject.toml` ja
  GitHub Actions -työnkulku, joka ajaa testit Python 3.10:llä ja 3.12:lla.
- Testejä 25 (aiemmin 20).

## 1.4.1 (2026-09-22)

- **Seurakuntien pääjako**: "Yhteensä" sisältää kaksi erikseen listattua
  seurakuntaa, joten luokkien summa on aina suurempi. Ositus merkittiin
  varmistamattomaksi, jolloin poikkeama ei enää näy virheenä (24 911 väärää
  hälytystä pois).
- **Verovuosikohtaiset taulut**: osasummatarkistus kysyi tauluilta vuotta,
  jota niissä ei ole. Nyt taulu tarkistetaan omalta uusimmalta vuodeltaan.
- **Taulut-välilehti**: joukon taulut, niiden PxWeb-päivitysaika, datan
  hakuaika ja havaintojen määrä. Näkymien erot johtuvat usein siitä, että
  taulut on päivitetty eri aikaan.
- Tiivistys osasi lukea Excelistä vuosiluvun `2018.0`; tilamerkinnän `nan`
  tulostuminen kuvaukseen korjattu.

## 1.4.0 (2026-09-21)

- **Välimuisti tuntee taulun päivitysajan**: kansiolistauksesta luettu
  `updated` hylkää välimuistista vastauksen, joka on haettu ennen taulun
  päivitystä. Muuten yhteen ajoon voisi sekoittua ennen ja jälkeen päivityksen
  haettua dataa. Listaukset haetaan tunnin välein, ja taulut ilman
  päivitysaikaa saavat lyhyemmän voimassaolon.
- Näkymien erojen kuvauksissa näkyvät taulujen päivitysajat, jolloin
  ristiriidan syyn (eri vuosikerta) näkee suoraan havainnosta.
- Tasatilanne näkymien välillä: kun mikään arvo ei ole enemmistönä, poikkeavaa
  taulua ei väitetä tiedetyksi.

## 1.3.0 (2026-09-21)

- **Kevyt raportti**: Excel sisältää vain havainnot ja ristiriitojen
  taustaluvut; koko haettu aineisto kirjoitetaan pakattuina
  `<raportti>_data_<laji>.csv.gz`-tiedostoina. Aiemmat raportit saa kevyeksi
  komennolla `tiivista`.
- **Tarkistus E** (`alueet`): kunnan luku vs. sen postinumeroalueiden summa.
- Erähierarkian kalibrointi historiasta: yläerä–alaerä-suhde otetaan
  valvontaan vasta, kun se on pitänyt jokaisena julkaistuna vuonna.
- Näkymävertailun voi ajaa koko aikasarjalta (`--vuodet kaikki`) ja osasummat
  valituilta vuosilta (`--osasummavuodet`).

## 1.2.0

- Tarkistus D: yläerä vs. alaerien summa, säännöt tallentuvat
  `saannot/`-kansioon ja ovat käsin korjattavissa.
- Joukot voi määritellä kansioina, ja muuttujan voi kiinnittää arvoon
  (`kiinnita`), jolloin eri perusjoukkoja ei verrata keskenään.
- Otsikkorajaus: sama kansio voi sisältää eri perusjoukon tauluja.

## 1.1.0

- Tarkistus C: aikasarjan eheys, vuosihypyt ja revisiot tilannevedosta vasten.
- Pyöristysvara summattavaa solua kohden: PxWeb pyöristää jokaisen eurosolun
  erikseen, joten osasummien ero ei ole virhe.
- Tilamerkintä `-` tulkitaan nollaksi, `..` (salassapito) ei.

## 1.0.0

- Ensimmäinen versio: PxWeb-asiakas välimuisteineen ja kyselyn paloitteluineen,
  taulujen kartoitus, muuttujaroolien tunnistus, tarkistus A (sama erä eri
  näkymissä) ja tarkistus B (osasummat vs. Yhteensä), Excel-raportti ja tiivis
  konsolituloste.
