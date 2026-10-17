# Stanje projekta

- 17.10.2026: aplikacija radi na http://localhost:8400; server restartovan posle ispravke.
- Novi snimci idu u lokalni `snimci/` na internom disku; SSD snimci nisu menjani.
- Ranije odredište: `/Volumes/SSD/Projects/Random/20260923 Nikon D3400/snimci`.
- Ispravljeno gašenje živog prikaza: kadar na čekanju više ga ne uključuje ponovo.
- Gašenje sada šalje komandu aparatu i posle ponovnog povezivanja.
- UI pamti ugašen prikaz; za duge ekspozicije ga ne pokreće pri osvežavanju.
- Posle promene podešavanja UI proverava i prikazuje stvarnu vrednost aparata.
- Na povezanom D3400 potvrđeno 1.0000 s, ostaje i posle ponovnog povezivanja.
- Provere gašenja prikaza i Python/JS sintakse prošle; snimanje nije pokretano.
- Provereno preko USB-a: nijedno podešavanje (ni d1ac LiveViewMovieMode 1/2) ne dozvoljava zatvarač duži od 1/30 dok je živi prikaz uključen.
- JPEG (snimanje samo u JPEG) se sada okida, uvozi i prikazuje; uz NEF istog imena JPEG se preskače.
- HDR režim (desni panel): 3 ili 5 snimaka, 1 ili 2 EV, samo M; menja zatvarač, gasi živi prikaz tokom serije.
- Spajanje `hdr.py` (Mertens fusion, NEF preko rawpy u 16 bita) → `snimci/hdr/HDR_*.tif` + `.jpg`. Proba 3×JPEG prošla.
- „HDR auto sredina”: ISO na najniži (auto ISO isključen), probni snimci (brišu se) mere svetlo, bira srednji zatvarač; blenda ostaje. Proba prošla (10 s → 4 s).
- Posle uspešnog HDR spajanja snimci serije (i JPEG parovi) se brišu sa kartice; originali ostaju u snimci/DCIM.
- 5 NEF snimaka traži oko 3 GB RAM-a i oko 40 s spajanja.
- Otvoreno: automatsko gašenje prikaza pri okidanju duge ekspozicije (čeka odluku).
