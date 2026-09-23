# Nikon D3400 daljinski

Javni GitHub repozitorijum za aplikaciju. Ovo nije javno hostovan upravljački
sajt: aplikacija mora da radi na Mac-u koji je USB kablom povezan sa aparatom.
GitHub Pages ne može da pristupi USB uređaju niti lokalnom serveru.

Upravljanje aparatom Nikon D3400 sa Mac-a preko USB kabla, kroz pregledač na
`http://localhost:8400`. Služi da se pre snimanja proveri kadar, fokus i
podešavanja, bez diranja aparata.

## Preuzimanje

Preuzmi ili kloniraj ovaj repozitorijum na Mac-u koji je povezan sa aparatom,
pa pokreni `pokreni.command`. Lični snimci i lokalno Python okruženje nisu deo
javnog repozitorijuma.

Napravljeno 23.09.2026. Prva proba sa aparatom istog dana, rezultati u spisku ispod.

## Pokretanje

1. Uključi aparat i poveži ga USB kablom sa Mac-om.
2. Dvoklik na `pokreni.command`.
3. Pregledač se sam otvori.

Prvo pokretanje instalira paket `gphoto2` u folder `.venv` pored aplikacije, ne u
sistem. Homebrew nije potreban.

## Fajlovi

| fajl | šta je |
|---|---|
| `server.py` | lokalni server, drži stalnu vezu sa aparatom |
| `index.html` | interfejs u pregledaču |
| `pokreni.command` | pokretanje dvoklikom |
| `snimci/` | fotografije prebačene sa aparata |
| `snimci/pregled/` | MP4 kopije videa za puštanje u pregledaču |
| `snimci/odnosi-videa.txt` | izabrani odnos kadra uz putanju svakog završenog videa |
| `snimci/.slicice/` | sličice za galeriju, mogu da se obrišu, prave se ponovo |

## Šta postoji sada

- Živi prikaz, uključi i ugasi.
- Prekidač **Foto | Video** i jedno okruglo dugme desno od živog prikaza (i
  razmaknica): u foto režimu slika, u video režimu počinje i zaustavlja snimanje.
  U video režimu se vidi samo deo kadra koji ide u video, ostatak je sakriven.
  Format se bira: 16:9 (FHD), 1.85:1, 2:1 ili 2.39:1. Aparat uvek snima 16:9,
  pa su širi formati vodič za kadriranje i sečenje u montaži. Okvir je
  približan: po sredini, cela širina.
- Slika odmah ide na Mac.
- Serijsko slikanje: izaberi broj NEF fotografija i najmanji razmak u
  milisekundama (0 za bez čekanja). Prenos preko USB-a može da učini stvarni
  razmak dužim od izabranog; serija prikazuje napredak i može da se zaustavi.
- Aplikacija prati PTP događaje sa fizičkog dugmeta zatvarača i preuzima NEF
  ako ga Nikon pošalje preko USB-a, i kad je živi prikaz ugašen. To treba
  potvrditi probom na D3400; ponašanje može zavisiti od režima aparata i cilja
  čuvanja slike.
- Sličica poslednje fotografije ili izabrane stavke iz galerije prikazuje se u
  malom pomerljivom panelu. Panel pamti svoju poziciju; klik na original otvara
  sačuvani fajl.
- Autofokus: dugme „Izoštri (AF)", taster F, ili klik na deo živog prikaza
  (pomeri tačku fokusa tamo i odmah izoštri).
  - Klik prebacuje AF zonu sa prepoznavanja lica na usku zonu, jer lica i
    praćenje objekta zanemaruju izabranu tačku.
  - Kad je AF režim živog prikaza na ručnom fokusu, aparat javi uspeh a ne
    izoštri. Server ga zato pre autofokusa sam vrati na AF-S. Ako aparat i
    posle toga ostane na ručnom, javlja da se proveri prekidač A/M.
- „Gde ide fotografija": „Samo na Mac" (Internal RAM) ne ostavlja sliku na
  kartici, „Kartica i Mac" (Memory card) ostavlja. Video uvek ide na karticu.
- Podešavanja: režim, ISO, zatvarač, blenda, korekcija ekspozicije, balans bele,
  režim fokusa, AF režim živog prikaza (npr. prepoznavanje lica), AF-S ili AF-F,
  kvalitet slike, gde se slika čuva. Vidi se samo ono što aparat prijavi.
- Video: počni i zaustavi sa računara, pa „Pogledaj na Mac-u" prebaci snimak
  sa kartice i pusti ga u pregledaču. Original (MOV) ide u `snimci/`, a kopija
  za pregledač (MP4, ista slika, AAC zvuk) u `snimci/pregled/`. Za MP4 treba
  `ffmpeg`, bez njega se pušta MOV (radi u Safari-ju). Izabrani odnos kadra
  zapisuje se uz putanju videa u `snimci/odnosi-videa.txt`; video se ne seče.
- Levo je uska galerija sa sličicama u dve kolone; prikaz i kontrole su desno,
  tako da se na širokom ekranu sve vidi u jednom redu.
- Galerija ima dve kartice:
  - **Na kartici aparata**: NEF fotografije i MOV/MP4 video sa SD kartice, sa veličinom i oznakom šta je
    već na Mac-u. Klik bira, dvoklik prebacuje i otvara. „Izaberi sve za
    proveru" pa „Proveri i prebaci". Veličina kopije se proverava, sadržaj se
    poredi kontrolnim zbirom; nepotpuna ili različita kopija se popravlja bez
    pravljenja duplikata. JPEG fotografije se preskaču. Fajlovi zadržavaju
    originalno ime i foldere sa kartice (DCIM/.../DSC_…).
  - **Na Mac-u**: folder `snimci/`, klik otvara sliku ili pušta video.
  - Umanjene WebP sličice se pamte u `snimci/.slicice/`; za prikaz se koristi
    JPEG pregled koji aparat već čuva uz fotografiju.
- Živi prikaz se sam uključi kad se stranica otvori, a ugasi kad se zatvori.
  Proverena veličina je 640×424 piksela pri oko 15 kadrova u sekundi; to nije
  puna rezolucija fotografije. Najveća fotografija je 6000×4000 piksela.
- Osvežavanje stranice ili dugme „Osveži vezu" prekida zastarelu USB vezu i
  ponovo pokušava povezivanje. Ako aparat ne može da izlista karticu, galerija
  prikaže fajlove sa Mac-a i nudi ponovno osvežavanje.
- Klik na živi prikaz bira tačku fokusa. Kontrole za fotografisanje i video su
  desno od prikaza; zum i probni snimak za proveru oštrine uklonjeni su iz
  interfejsa.

## Prva proba, šta se proverava

Stanje posle prve probe, 23.09.2026 (libgphoto2 2.5.34, firmver aparata 1.13):

- [x] Veza: „Nikon DSC D3400".
- [x] Instalacija `gphoto2` prošla i na Pythonu 3.14.
- [x] Živi prikaz, 640×424, oko 15 kadrova u sekundi.
- [x] Okidanje i prebacivanje. Uz NEF+Fine stižu oba fajla, JPEG je 6000×4000.
- [x] Autofokus. Greška je bila samo kad aparat gleda u tamnu jednobojnu površinu.
- [x] U meniju su „Face-priority AF" i „Single-servo AF", mogu da se menjaju.
- [ ] Klik na živi prikaz: da li se tačka fokusa na aparatu pomeri na isto
      mesto. Pun kadar je 6000×4000, isto kao `SIRINA_KADRA` i `VISINA_KADRA`.
- [x] Video sa računara: 1920×1080, 30 kadrova u sekundi, H.264 sa PCM zvukom.
      Pri zaustavljanju libgphoto2 javi „Access Denied" dok vraća režim
      aparata, snimak je ipak ispravan, pa server tu grešku preskače.
- [ ] Da li dugme za video na aparatu radi dok je aparat povezan.

Ako se aparat ne poveže: izvući i vratiti kabl, pa osvežiti stranicu. macOS
servis `ptpcamerad` zna da zauzme aparat, server ga gasi sam, ali ne uvek na
vreme.

## Provera fokusa, plan

1. **Tačka fokusa klikom.** Klik na živi prikaz pomera tačku fokusa aparata na
   taj deo kadra, pa autofokus oštri tamo.
2. **Poruka da fokus nije uspeo.** Ubačeno posle prve probe: umesto opšte
   greške piše zašto autofokus verovatno nije uspeo.
3. **Prikaz trenutnih AF podešavanja.** Upozorenje ako nije uključeno
   prepoznavanje lica ili ako je AF-F umesto AF-S pre snimanja.
4. **Automatsko traženje najboljeg fokusa.** Server pomera fokus u koracima,
   meri oštrinu na svakom i vrati se na najbolji. Zamena za autofokus kad on
   pumpa ili promašuje.
5. **Probni video.** Ubačeno: snimi se par sekundi sa računara, server ih
   povuče sa kartice i pusti u pregledaču u punoj rezoluciji. Jedina provera
    koja pokazuje pravi video, sa fokusom, bojama i ekspozicijom.

## Preporuka za snimanje osobe koja sedi

Video fokus na D3400 radi na kontrast, spor je i u AF-F režimu zna da pumpa.
Pouzdanije je:

1. AF režim živog prikaza: prepoznavanje lica. Fokus: AF-S.
2. Autofokus jednom, pre snimanja, pa proveriti oštrinu.
3. Tokom snimanja fokus ostaje zaključan.
4. Blenda f/5.6 ili više, da sitno pomeranje osobe ne pokvari oštrinu.

## Ograničenja D3400

- Prebacivanje videa ide preko USB 2.0, pa je dobro za probne snimke od
  nekoliko sekundi. Dug snimak je brže preuzeti sa kartice čitačem.
- Živi prikaz preko USB-a je 640×424 piksela i oko 15 kadrova u sekundi.
- Aparat se preko USB-a ne puni i ne napaja. Dok je povezan ne ide u mirovanje,
  a živi prikaz najviše troši bateriju. Za duže snimanje postoji mrežni adapter
  EP-5A sa EH-5b ili EH-5c.
- Podešavanja se menjaju samo u režimima P, S, A i M, ne u AUTO.

## Moguće nadogradnje

Timelapse, bracketing za HDR, focus stacking, okidanje na pokret, fotografisanje
proizvoda sa automatskom obradom u WEBP, šabloni podešavanja, upravljanje sa
telefona preko kućne mreže (uz lozinku), stop-motion sa prozirnim prethodnim
kadrom, foto-štand, nivo baterije, pregled SD kartice, mreža trećina.
