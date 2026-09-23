# pxtarkistus

Verohallinnon julkisten verotilastojen (PxWeb, <https://vero2.stat.fi/PXWeb/pxweb/fi/Vero/>)
automaattinen ristiintarkistus: hakee taulut rajapinnasta ja etsii niistä
epäjohdonmukaisuuksia, jotka ihmissilmä löytää vasta sattumalta.

Työkalu ei korjaa mitään eikä ota kantaa siihen, onko poikkeama virhe. Se
nostaa esiin kohdat, joissa saman asian kaksi julkaistua lukua eivät täsmää,
ja näyttää molemmat luvut sekä eron euroina ja prosentteina.

---

## Mitä tarkistetaan

### A. Sama tilastoerä eri näkymissä (`ristiin`)

Henkilöasiakkaiden tuloverotilastossa näkymät 06–12 kuvaavat samaa
perusjoukkoa (yleisesti verovelvolliset) eri luokituksin: alueittain,
kuntaryhmittäin, perhetyypeittäin, koulutustason mukaan ja niin edelleen.
Kun jokaisen luokituksen Yhteensä-arvo valitaan, koko maan tason luvun
pitäisi olla sama taulusta riippumatta.

Tarkistus hakee jokaisesta taulusta saman erätunnuksen (esim.
`HVT_TULOT_80`, palkkatulot) koko maan arvon ja vertaa tauluja toisiinsa.
Mukaan otetaan erät, jotka esiintyvät vähintään kahdessa joukon taulussa —
kaikkien taulujen leikkaus olisi tyhjä, koska alue-näkymä on jaettu erillisiin
tulo-, vähennys- ja verotauluihin.
Vertailukohdaksi otetaan useimpien taulujen yhteinen arvo (enemmistöarvo):
jos neljä näkymää viidestä on yhtä mieltä, poikkeava on se viides, ja
havainto kohdistuu siihen. Jos enemmistöä ei ole – esimerkiksi erä on vain
kahdessa näkymässä ja niiden arvot eroavat (1 vs 1) – poikkeavaa näkymää ei
voi päätellä. Silloin ristiriidasta tulee yksi havainto, jonka kohteena ovat
kaikki taulut ja kuvauksessa jokaisen taulun arvo (Vertailukohta
"ei enemmistöä (1 vs 1)"). Joukolle voi asettaa `vertailutaulu`-taulun,
joka ratkaisee tasatilanteen, kun se on mukana vertailussa.

Summan ja lukumäärän lisäksi verrataan keskiarvoa ja mediaania. Ne eivät ole
additiivisia, mutta koko maan tasolla niiden pitää olla samat joka näkymässä
(asetus `ristiin.tunnusluvut`).

Löytää tyypillisesti: näkymän jäämisen päivittämättä, luokituksen katkon,
väärin rajatun perusjoukon, erätunnuksen merkityksen muutoksen yhdessä
taulussa.

### B. Osasummat vs. Yhteensä-rivi (`osasummat`)

Taulun sisäinen looginen eheys:

* osituksen luokkien summa = Yhteensä-rivi (muut luokittelumuuttujat
  kiinnitettynä omiin Yhteensä-arvoihinsa),
* lukumäärä × keskiarvo ≈ kokonaissumma.

Hierarkkiset luokitukset hoidetaan **tasoina**. Tason voi määritellä koodin
säännöllisellä lausekkeella, koodilistana tai arvotekstin perusteella
(`teksti`, `teksti_ei`); `varmistamaton: true` raportoi poikkeaman lievänä
huomiona virheen sijaan, kun rakenteesta ei ole varmuutta. Esimerkiksi Kuntaryhmityksen
arvot ovat SSS (yhteensä), 1 (Manner-Suomi), TK1–TK3 (kaupunkimaiset, taajaan
asutut, maaseutumaiset), 2 (Ahvenanmaa) ja ZZZ (tuntematon). Manner-Suomi ja
TK1–TK3 kuvaavat samaa joukkoa eri tarkkuudella, joten kaikkien arvojen
summaaminen kaksinkertaistaisi sen. Asetuksissa määritellään kaksi erillistä
ositusta, jotka kumpikin tarkistetaan omanaan.

Useimmiten tasoja ei tarvitse kirjoittaa käsin: Verohallinnon luokitukset
numeroivat hierarkian auki arvoteksteissä ("1. Kuolinpesä",
"2. Luonnollinen henkilö", "2.3 Tutkinnon suorittaneita täysi-ikäisiä
yhteensä", "2.3.1 Toinen aste"), ja ohjelma purkaa numeroinnista jokaiselle
syvyydelle oman osituksen, jossa haaran syvin taso korvaa ylemmän. Näin
esimerkiksi Koulutusaste, Perhetyyppi ja Syntymävuosikymmen tarkistuvat
tasoittain ilman asetuksia. Vain numeroimattomat hierarkiat (kuten
Kuntaryhmitys) on määriteltävä itse.

Jos ositusta ei saada kummallakaan tavalla, ohjelma olettaa kaikki
ei-yhteensä-arvot yhdeksi ositukseksi ja merkitsee havainnon lievemmäksi
("huomio") – silloin poikkeama voi johtua myös siitä, että luokitus on
hierarkkinen.

Tyhjät solut käsitellään merkinnän mukaan, koska ne tarkoittavat eri asioita:

| Merkintä | Tulkinta | Vaikutus tarkistukseen |
|---|---|---|
| `-` | ei yhtään havaintoa, eli nolla | luetaan nollaksi, vertailu tehdään normaalisti |
| `..` | tieto salassapidon vuoksi puuttuva | summaa ei voi verrata, tapaus ohitetaan |
| `.` | tieto ei sovellu | summaa ei voi verrata, tapaus ohitetaan |

`-`-merkinnän tulkinta on todennettu aineistosta: kun osituksen ainoat tyhjät
solut olivat `-`, luokkien summa täsmäsi Yhteensä-riviin 3 077 tapauksessa
kolmesta tuhannesta seitsemästäkymmenestäseitsemästä. `..`-tapauksissa summa
jäi järjestelmällisesti vajaaksi. Jos jokin muu tietokanta käyttää merkintöjä
toisin, muuta `NOLLAMERKINNAT`-vakiota moduulissa `pxclient`.

Ohitetut tapaukset eivät ole virheitä eivätkä saa omaa riviä jokaisesta
erästä, vaan ne kootaan yhdeksi havainnoksi ositusta ja syytä kohden ("n erän
osasummaa ei voitu verrata").

Oletuksena osasummat tarkistetaan tarkasteltavalta verovuodelta
(`--verovuosi`). Verovuosikohtaiset taulut (esim. kansion 19 taulut, joissa on
vain yksi vuosi) tarkistetaan omalta vuodeltaan. `--osasummavuodet kaikki` tarkistaa jokaisen julkaistun
vuoden (tai `--osasummavuodet 2018,2019,2020` valitut). Kaikki vuodet haetaan
taulukohtaisesti samalla kyselyllä, mutta datan määrä kasvaa vuosien
mukana, joten koko aikasarjan ajo kestää tunteja.

### C. Aikasarjan eheys ja revisiot (`aikasarja`)

Koko maan aikasarja jokaiselle erälle:

* arvo puuttuu keskeltä sarjaa,
* vuosimuutos ylittää raja-arvon (oletus 30 %),
* arvon etumerkki vaihtuu,
* **revisio**: aiemmin julkaistu arvo on muuttunut edellisen ajon jälkeen.

Revisiotarkistus perustuu tilannevedokseen: jokainen ajo tallentaa hakemansa
arvot JSON-tiedostoon ja vertaa niitä seuraavalla kerralla. Ensimmäinen ajo
ei siis vielä löydä revisioita – se luo vertailukohdan.

### D. Yläerä vs. alaerien summa (`erahierarkia`)

Tilastoerien nimet numeroivat rakenteen auki: "4.1 Palkkatulot yhteensä" on
erän "4. Ansiotulot yhteensä" alaerä. Numerointi ei kuitenkaan kerro, onko
suhde additiivinen — moni alaerä on "josta"-tyyppinen erittely, joka on jo
luettu mukaan muualla. Henkilöasiakkaiden tuloverotilastossa vain noin
neljännes numeroinnin ehdottamista suhteista on aitoja summia.

Siksi jokainen ehdokassuhde **kalibroidaan historiaa vasten**: se otetaan
valvontaan vasta kun yläerä on ollut alaeriensä summa jokaisena vuonna, jolta
molemmat on julkaistu (vähintään `vahimmaisvuodet` vuotta), tarkasteltava
verovuosi pois lukien. Vahvistetut suhteet kirjoitetaan sääntötiedostoon, jota
voi korjata käsin kuvaustiedostojen laskentasääntöjen perusteella; tiedostossa
olevia valvotaan jatkossa ilman uutta kalibrointia.

Jos suhde on täsmännyt jokaisena vuonna yhtä aiempaa vuotta lukuun ottamatta
(vähintään viisi vertailukelpoista vuotta), se ei vahvistu, mutta sen ainoa
poikkeava vuosi raportoidaan varoituksena: yksittäinen poikkeama pitkässä
sarjassa on todennäköisemmin kyseisen vuoden virhe kuin merkki
ei-additiivisesta suhteesta. Raportin aineistotiedostossa
(`…_data_erahierarkia.csv.gz`) tällaisen suhteen tila on
"yksi poikkeava vuosi (vuosi)".

Tarkistus koskee vain eurosummia. Lukumäärät eivät ole additiivisia: sama
henkilö voi kuulua useaan alaerään, jolloin alaerien summa ylittää yläerän.

Verovuoden 2024 aineistossa vahvistui 25 suhdetta — muun muassa palkkatulot
kuudesta osasta, tuloverot seitsemästä ja osingot listatuista yhtiöistä
27:stä — eikä yksikään niistä rikkoutunut.

### E. Kunnan luku vs. postinumeroalueiden summa (`alueet`)

Aluenäkymä (06) julkaisee kunnan luvun suoraan, postinumeronäkymä (08) saman
kunnan postinumeroalueittain (`005_62710`, `005_ZZZPL`, `005_MUUKN`).
Kuntanumerointi on molemmissa sama, joten jokaisen kunnan
postinumeroalueiden summan pitää olla kunnan luku aluenäkymässä. Tarkistus
tehdään jokaiselle kunnalle, erälle ja vuodelle, jolta molemmat näkymät on
julkaistu.

Jos kunnan jokin postinumeroalue on peitetty (`..`), kuntaa ei voi verrata,
ja se kirjataan ohitetuksi. Jos sama ero toistuu yli 30 kunnassa, se
raportoidaan yhtenä rivinä: niin laaja ero kertoo näkymien erilaisesta
rajauksesta eikä yksittäisen kunnan virheestä.

---

## Asennus

Vaatii Python 3.10 tai uudemman.

```bash
cd pxtarkistus
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Ympäristö, jossa työkalu ajetaan, tarvitsee suoran yhteyden osoitteeseen
`vero2.stat.fi` (HTTPS). Rajapinnan datahaku tehdään POST-kutsuna, joten
pelkkä selainyhteys ei riitä.

---

## Käyttö

```bash
# 1. Katso mitä tauluja tietokannassa on (tulos myös taulut.csv:hen)
python aja.py kartoita --juuri Henkiloasiakkaiden_tuloverot

# 2. Katso yhden taulun rakenne ja miten ohjelma tulkitsee sen muuttujat
python aja.py muuttujat --taulu Henkiloasiakkaiden_tuloverot/lopulliset/alue/tulot_102.px

# 3. Etsi erätunnuksia nimen perusteella
python aja.py erat --taulu Henkiloasiakkaiden_tuloverot/lopulliset/kuntaryh/kuntaryh_101.px --haku osinko

# 4. Aja tarkistukset ja kirjoita Excel-raportti
python aja.py tarkista --joukko kaikki --verovuosi 2024

# näkymävertailu koko aikasarjalta, koko erävalikoimalla
python aja.py tarkista --joukko kaikki --vuodet kaikki --max-erat 1000

# osasummat koko aikasarjalta (pitkä ajo)
python aja.py tarkista --joukko henkilo_kaikki --tarkistukset osasummat --osasummavuodet kaikki --max-erat 1000

# vain osa tarkistuksista, harvemmilla erillä
python aja.py tarkista --joukko henkilo_yleisesti --tarkistukset ristiin,aikasarja --max-erat 30

# omat erät tiedostosta (yksi erätunnus per rivi)
python aja.py tarkista --joukko henkilo_kaikki --erat-tiedosto omat_erat.txt

# 5. Muunna vanhan version raportti kevyeksi (Data-välilehdet .csv.gz-tiedostoiksi)
python aja.py tiivista raportit/pxtarkistus_henkilo_kaikki_2024_2026-09-21_1623.xlsx
```

Paluuarvo on 1, jos raportille tuli vakavuudeltaan `virhe`-tason havaintoja –
näin komennon voi ketjuttaa ajastukseen tai putkeen.

Ensimmäinen ajo kannattaa tehdä pienellä `--max-erat`-arvolla: yksi kysely
per taulu ja tarkistus, mutta kyselyiden koko kasvaa erien määrän mukana.

---

## Asetukset (`asetukset.yaml`)

**`joukot`** – mitkä taulut verrataan keskenään. Vertailu on mielekäs vain,
jos taulujen perusjoukko on sama. Valmiit joukot kattavat koko
henkilöasiakkaiden tuloverotilaston, kaikki 24 kansiota:

| Joukko | Kansiot | Perusjoukko |
|---|---|---|
| `henkilo_kaikki` | 01–05, 13–20 | kaikki verovelvolliset |
| `henkilo_yleisesti` | 06–12 + silta 05:een | yleisesti verovelvolliset |
| `henkilo_yel` | 21 | YEL-vakuutetut |
| `henkilo_yel_alueittain` | 06 (taulut 6.16) | yleisesti verovelvolliset YEL-vakuutetut |
| `henkilo_myel` | 22 | MYEL-vakuutetut |
| `henkilo_myel_alueittain` | 06 (taulut 6.17) | yleisesti verovelvolliset MYEL-vakuutetut |
| `henkilo_listamosin` | 23 | listaamattomista yhtiöistä osinkoja saaneet |
| `henkilo_listamosin_alueittain` | 06 (taulut 6.18) | vastaava, yleisesti verovelvolliset |
| `henkilo_luovutusvoitot` | 24 | luovutusvoitot ja osakesäästötilit |

Joukko luetellaan kansioina (`kansiot`), jolloin kaikki kansion taulut
alikansioineen otetaan mukaan – myös verovuosikohtaiset alikansiot kuten
postinumero- ja seurakuntataulut. Yksittäisiä tauluja voi antaa `taulut`-listalla
ja pois jättää `ohita`-lausekkeilla.

**Perusjoukko luetaan taulun otsikosta.** Kansion nimi ei aina kerro
perusjoukkoa: kansiossa 19 alueittaiset taulut (19.02, 19.06 … 19.42) ovat
"Yleisesti verovelvollisten …", ja aluekansion verovuosikohtaisissa
alikansioissa on YEL-, MYEL- ja osinkotaulujen alueversiot (6.16–6.18).
Joukon tai kansion rajaukset `otsikko` ja `otsikko_ei` (säännöllisiä
lausekkeita) ohjaavat taulut oikeaan joukkoon.

**Silta perusjoukkojen välillä.** Kansion 05 verovelvollisuustauluissa on
muuttuja Verovelvollisuus. Kun se rajataan arvoon "Yleisesti verovelvolliset",
koko maan lukujen pitää olla samat kuin kansioissa 06–12. Tämä tehdään
kiinnityksellä:

```yaml
- polku: "Henkiloasiakkaiden_tuloverot/lopulliset/verovelv"
  kiinnita:
    Verovelvollisuus: "Yleisesti verovelvolliset"
```

Kiinnitetty muuttuja ei ole luokittelu: sitä ei ositeta, se vain rajaa
perusjoukon. Raportilla rajattu taulu näkyy muodossa
`…/verovelv_102.px [Verovelvollisuus=Yleisesti verovelvolliset]`.

Useampi joukko ajetaan yhteen raporttiin, jossa jokaisella rivillä on
`joukko`-sarake:

```bash
python aja.py tarkista --joukko kaikki --vuodet kaikki --max-erat 1000
python aja.py tarkista --joukko henkilo_kaikki,henkilo_yleisesti
```

Jos jokin joukko ei ole tarkistettavissa, se ohitetaan ja syy kirjataan
yhteenvetoon; muut ajetaan silti. Tilannevedos ja erähierarkian sääntötiedosto
ovat joukkokohtaisia (`{joukko}` tiedostonimessä).

Jokaisesta joukosta kirjoitetaan oma raportti heti kun se valmistuu, joten
pitkä ajo ei ole kaikki tai ei mitään.

**Ajoaika.** Koko tilaston ensimmäinen ajo tekee tuhansia kyselyitä, joista
raskaimmat ovat postinumero- ja seurakuntataulujen osasummat. Sekunnin
tauolla se voi kestää tunnin tai kaksi. Kannattaa ajaa ensin kevyet
tarkistukset (`--tarkistukset ristiin,aikasarja,erahierarkia`) ja osasummat
perään; välimuisti säilyttää haetut luvut viikon.

**`luokitukset`** – tämä on työkalun tärkein käsin tehtävä osa. Jokaiselle
luokittelumuuttujalle kerrotaan Yhteensä-arvon koodi ja mahdolliset tasot:

```yaml
luokitukset:
  Alue:
    yhteensa: "000"
    tasot:
      maakunnat: "^[0-9]{2}$"     # säännöllinen lauseke
      kunnat: "^[0-9]{3}$"
  Kuntaryhmitys:
    yhteensa: "SSS"
    tasot:
      paajako: ["1", "2", "ZZZ"]  # tai suora lista koodeja
      tilastollinen_kuntaryhmitys: ["TK1", "TK2", "TK3", "2", "ZZZ"]
```

Ohjelma osaa päätellä Yhteensä-arvon arvoteksteistä ("Yhteensä", "Kaikki",
"Koko maa") ja tavallisista koodeista (SSS, SS, 000, Y), mutta hierarkkiset
tasot on kerrottava itse. Komento `muuttujat` näyttää, mitä ohjelma päätteli.

**`toleranssi`** – PxWeb julkaisee eurosummat kokonaisina euroina, eli
jokainen solu on pyöristetty erikseen. Kun 19 maakunnan tai 320 kunnan luvut
lasketaan yhteen, summa poikkeaa julkaistusta kokonaissummasta tyypillisesti
muutaman euron. Se on pyöristystä, ei virhe, joten toleranssi kasvaa
summattavien solujen määrän mukana: `pyoristys_per_solu` (oletus 0,5 €)
kerrottuna luokkien lukumäärällä. Lukumääriin (`N`) pyöristysvaraa ei
sovelleta, koska ne ovat tarkkoja kokonaislukuja. `absoluuttinen` on
vähimmäisvara ja `suhteellinen` osuus vertailuarvosta (oletus 0).

**`valimuisti`** – jokainen kysely tallennetaan levylle ja haetaan
palvelimelta vain kerran voimassaoloajan sisällä (oletus 7 vrk). Kun
tarkistussääntöjä säätää, ajot ovat sen jälkeen nopeita eikä palvelinta
kuormiteta turhaan. Välimuistin tyhjentää poistamalla `.pxvalimuisti`-
hakemiston.

Taulun välimuistiin tallennettu vastaus hylätään, jos kansiolistauksen mukaan
taulu on päivitetty palvelimella tallentamisen jälkeen. Ilman tätä sama ajo
voisi verrata ennen ja jälkeen päivityksen haettuja lukuja, jolloin ero
näyttäisi näkymien väliseltä virheeltä. Kansiolistaukset haetaan uudelleen
tunnin välein (`listaus_valimuisti_tuntia`). Osassa tauluja listaus ei kerro
päivitysaikaa (syyskuussa 2026 esimerkiksi kansioiden 01, 05, 13–15 ja 20
taulut); niiden vastaukset haetaan uudelleen 12 tunnin jälkeen
(`ilman_paivitysaikaa_tuntia`).

---

## Raportti

Excel-työkirja hakemistoon `raportit/`. Se sisältää vain havainnot, joten se
pysyy pienenä (tyypillisesti alle muutama megatavu) ja aukeaa nopeasti:

| Välilehti | Sisältö |
|---|---|
| Yhteenveto | ajon parametrit, havaintojen lukumäärät ja aineistotiedostojen nimet |
| Havainnot | kaikki havainnot vakavuusjärjestyksessä, värikoodattuna |
| Näkymien välillä / Osasummat / Aikasarja / Revisiot / Erähierarkia / Kunnat vs postinumerot | havainnot tarkistuksittain |
| Ristiriitojen taustaluvut | näkymävertailun virheistä ja varoituksista jokaisen taulun luku samalle erälle, vuodelle ja tunnusluvulle – näkee suoraan, mikä taulu poikkeaa |
| Taulut | joukon taulut, niiden päivitysaika PxWebissä, milloin data haettiin ja montako havaintoa taulusta tuli |

Näkymien välisen eron kuvaus kertoo poikkeavan taulun ja enemmistön
päivitysajat. Erot johtuvat usein siitä, että taulut on muodostettu eri
aikaan: jos poikkeava taulu on päivitetty toukokuussa ja muut syyskuussa,
kyse on todennäköisesti taulusta, joka jäi päivittämättä. Samoin revisiohavainto
kertoo, onko taulu päivitetty tilannevedoksen jälkeen.

Näkymästä puuttuvat koko maan arvot kootaan yhdeksi riviksi taulua ja erää
kohden (vuodet ja tunnusluvut lueteltuna), ja rivi kertoo solun merkinnän. Jos
koko maan summa on peitetty (`..`) yhdessä näkymässä mutta julkaistu muissa,
kyse on usein toissijaisesta peitosta – ja silloin muualla julkaistu summa voi
paljastaa peitetyn solun erotuksena.

Koko haettu aineisto, josta havainnot on laskettu, kirjoitetaan raportin
viereen pakattuina CSV-tiedostoina (`<raportti>_data_ristiin.csv.gz`,
`…_data_osasummat.csv.gz`, `…_data_aikasarja.csv.gz`, `…_data_erahierarkia.csv.gz`,
`…_data_alueet.csv.gz`). Aineistoa voi olla satojatuhansia rivejä, mikä on
liikaa Excel-välilehdelle. Tiedostot ovat puolipisteellä eroteltuja ja
UTF-8-koodattuja; pandas lukee ne suoraan (`pd.read_csv(polku, sep=";")`),
ja LibreOffice avaa ne purkamisen jälkeen (`gunzip -k tiedosto.csv.gz`).

Vanhemman version raporteissa aineisto oli Data-välilehdillä, jolloin
tiedosto saattoi kasvaa kymmeniin megatavuihin. `python aja.py tiivista
<raportti.xlsx>` kirjoittaa niistä kevyen `<raportti>_tiivis.xlsx`-version
ja siirtää aineiston .csv.gz-tiedostoihin; havainnot säilyvät ennallaan.
Jos `python-calamine`-paketti on asennettu, suuren työkirjan luku on
moninkertaisesti nopeampi (`pip install python-calamine`), mutta se ei ole
pakollinen.

Vakavuudet: **virhe** (osasummat eivät täsmää määritellyllä osituksella, tai
yli 1 %:n ero näkymien välillä), **varoitus** (ero selittyy mahdollisesti
puuttuvilla soluilla tai on pieni), **huomio** (aikasarjan hyppy, revisio,
päätellyn osituksen poikkeama, kohta jota ei voitu tarkistaa).

Tyhjä solu ei ole automaattisesti virhe: PxWebissä pieniä soluja piilotetaan
tietosuojasyistä, ja tarkistus merkitsee tällaisen osasumman erikseen
("n luokan arvo puuttuu").

---

## Testit

Tarkistuslogiikka on todennettu simuloidulla aineistolla, johon on istutettu
tunnetut virheet (näkymien välinen ero, osasummavirhe yhdellä hierarkiatasolla,
aikasarjan hyppy, puuttuva arvo, revisio):

```bash
python testit/test_tarkistukset.py
```

Simulaattori rakentaa oikean muotoiset PxWeb-vastaukset, joten myös
json-stat2-purku ja kyselyn muodostus tulevat testatuiksi ilman verkkoyhteyttä.
Kansiossa `esimerkki/` on tällä aineistolla tuotettu raportti
aineistotiedostoineen: kaikki viisi tarkistusta ja jokainen istutettu virhe.

---

## Rajoitukset ja jatkokehitys

* **Tilastojen välinen ristiintarkistus** (esim. elinkeinoverotilaston verot
  vs. verotulojen kehitys) vaatii erätunnusten vastaavuustaulun, koska
  tunnukset eivät ole yhteisiä. Rakenne tukee tätä: lisää joukkoon taulut ja
  anna `--erat-tiedosto`.
* **Prosenttiluvut, mediaanit ja fraktiilit** eivät ole additiivisia, joten
  osasummatarkistus koskee vain tunnuslukuja `Sum` ja `N`.
* Postinumero- ja seurakuntanäkymät on julkaistu verovuosikohtaisina
  kansioina; ne toimivat, mutta polkuun tulee välilyönti
  (`.../postinum/Verovuosi 2024/...`).
* Ajastus (esim. viikoittainen ajo julkaisujen jälkeen) onnistuu
  tehtävästä riippuen Windowsin Tehtävien ajoituksella tai cronilla:
  komento palauttaa poikkeustilanteessa paluuarvon 1.
