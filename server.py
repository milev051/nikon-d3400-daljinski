"""Daljinsko upravljanje Nikon D3400 preko USB-a.

Pokreće lokalni server na http://localhost:8400 sa živim prikazom,
okidanjem, autofokusom, ručnim pomeranjem fokusa i osnovnim podešavanjima.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import gphoto2 as gp

KOREN = Path(__file__).parent
SNIMCI = KOREN / "snimci"
PREGLEDI = SNIMCI / "pregled"
SLICICE = SNIMCI / ".slicice"
PREUZIMANJA = SNIMCI / ".preuzeto.json"
ODNOSI_VIDEA = SNIMCI / "odnosi-videa.txt"
PODFOLDERI = {"provere": SNIMCI / "provere", "pregled": PREGLEDI}
TIPOVI = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
PORT = 8400
ODNOSI = {
    "1.7778": "16:9 (FHD)",
    "1.85": "1.85:1",
    "2": "2:1",
    "2.39": "2.39:1",
}


def kontrolni_zbir(putanja):
    """Računa SHA-256 u blokovima, bez učitavanja celog snimka u memoriju."""
    zbir = hashlib.sha256()
    with putanja.open("rb") as fajl:
        for blok in iter(lambda: fajl.read(1024 * 1024), b""):
            zbir.update(blok)
    return zbir.hexdigest()


def provereni_prenosi():
    try:
        return json.loads(PREUZIMANJA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sacuvaj_proverene_prenose(prenosi):
    SNIMCI.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=SNIMCI, prefix=".preuzeto-", delete=False) as fajl:
        privremeni = Path(fajl.name)
        json.dump(prenosi, fajl, ensure_ascii=False, indent=2)
    os.replace(privremeni, PREUZIMANJA)


def zabelezi_odnos_videa(putanja, odnos):
    """Upserta snimak i izabrani odnos u jedan čitljiv tekstualni spisak."""
    if odnos not in ODNOSI:
        return
    SNIMCI.mkdir(parents=True, exist_ok=True)
    zapisi = {}
    if ODNOSI_VIDEA.is_file():
        for red in ODNOSI_VIDEA.read_text(encoding="utf-8").splitlines()[1:]:
            delovi = red.split("\t", 1)
            if len(delovi) == 2:
                zapisi[delovi[0]] = delovi[1]
    zapisi[putanja] = ODNOSI[odnos]
    sadrzaj = "Video sa kartice\tIzabrani odnos kadra\n"
    sadrzaj += "".join(f"{ime}\t{vrednost}\n" for ime, vrednost in sorted(zapisi.items()))
    privremeni = ODNOSI_VIDEA.with_suffix(".txt.tmp")
    privremeni.write_text(sadrzaj, encoding="utf-8")
    os.replace(privremeni, ODNOSI_VIDEA)

# Nikon traži tačku fokusa u koordinatama celog kadra živog prikaza.
# Pretpostavka za D3400, proveriti na aparatu i ispraviti ako tačka promašuje.
SIRINA_KADRA = 6000
VISINA_KADRA = 4000

# Podešavanja koja se prikazuju u interfejsu, ako ih aparat podržava.
PODESAVANJA = [
    "expprogram",
    "iso",
    "shutterspeed",
    "f-number",
    "exposurecompensation",
    "whitebalance",
    "focusmode",
    "liveviewafmode",
    "liveviewaffocus",
    "imagequality",
    "capturetarget",
]


class Aparat:
    """Jedna veza sa aparatom, deljena između svih zahteva."""

    def __init__(self):
        self.brava = threading.Lock()
        self.kamera = None
        self.zivi_prikaz = False
        self.prikaz_trazen = False
        self.serija_zakljucavanje = threading.Lock()
        self.serija_stop = threading.Event()
        self.serija = {"radi": False, "ukupno": 0, "gotovo": 0, "poslednji": None, "greska": None}
        self.spoljno_okidanje = {"broj": 0, "putanja": None}
        self.spoljne_putanje = []
        self.vreme_poslednjeg_spoljnog_okidanja = 0
        threading.Thread(target=self._nadgledaj_fizicki_okidac, name="nikon-fizicki-okidac", daemon=True).start()

    def _nadgledaj_fizicki_okidac(self):
        """Prati PTP događaje sa okidača na aparatu, i uvozi NEF bez obzira na live view."""
        while True:
            try:
                with self.brava:
                    kamera = self.kamera
                    if kamera is not None:
                        tip, podatak = kamera.wait_for_event(1 if self.zivi_prikaz else 250)
                        if tip == gp.GP_EVENT_FILE_ADDED and Path(podatak.name).suffix.lower() == ".nef":
                            self.spoljne_putanje.append(podatak)
                            self.vreme_poslednjeg_spoljnog_okidanja = time.monotonic()
                        if self.spoljne_putanje and time.monotonic() - self.vreme_poslednjeg_spoljnog_okidanja >= 2.5:
                            putanje = self.spoljne_putanje
                            self.spoljne_putanje = []
                            for putanja in putanje:
                                self._preuzmi_spoljni_nef(kamera, putanja)
            except gp.GPhoto2Error:
                with self.brava:
                    self._prekini()
                time.sleep(0.5)
            time.sleep(0.08 if self.zivi_prikaz else 0.12)

    def _preuzmi_spoljni_nef(self, kamera, putanja):
        relativna = self.lokalna_putanja_kamere(putanja.folder, putanja.name)
        cilj = SNIMCI / relativna
        cilj.parent.mkdir(parents=True, exist_ok=True)
        info_omotac = kamera.file_get_info(putanja.folder, putanja.name)
        info = info_omotac.file
        podaci = kamera.file_get(putanja.folder, putanja.name, gp.GP_FILE_TYPE_NORMAL).get_data_and_size()
        if len(podaci) != info.size:
            return
        with tempfile.NamedTemporaryFile(dir=cilj.parent, prefix=".okidac-", delete=False) as izlaz:
            privremeno = Path(izlaz.name)
            izlaz.write(podaci)
        os.replace(privremeno, cilj)
        stat = cilj.stat()
        prenosi = provereni_prenosi()
        prenosi[putanja.folder.rstrip("/") + "/" + putanja.name] = {
            "velicina_kartice": info.size,
            "mtime_kartice": info.mtime,
            "velicina_maca": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": kontrolni_zbir(cilj),
        }
        sacuvaj_proverene_prenose(prenosi)
        self.spoljno_okidanje["broj"] += 1
        self.spoljno_okidanje["putanja"] = relativna.as_posix()

    def _povezi(self):
        if self.kamera is not None:
            return self.kamera
        # macOS sistemski servis zauzme aparat čim se uključi, pa se gasi pre povezivanja.
        subprocess.run(["killall", "-9", "ptpcamerad", "PTPCamera"], capture_output=True)
        time.sleep(0.3)
        kamera = gp.Camera()
        kamera.init()
        self.kamera = kamera
        return kamera

    def _prekini(self):
        try:
            if self.kamera is not None:
                self.kamera.exit()
        except gp.GPhoto2Error:
            pass
        self.kamera = None
        self.zivi_prikaz = False

    def izvrsi(self, radnja):
        """Izvršava radnju nad aparatom, uz jedan ponovni pokušaj ako veza pukne."""
        with self.brava:
            for pokusaj in range(2):
                try:
                    return radnja(self._povezi())
                except gp.GPhoto2Error as greska:
                    veza_pukla = greska.code in (
                        gp.GP_ERROR_IO,
                        gp.GP_ERROR_IO_USB_CLAIM,
                        gp.GP_ERROR_MODEL_NOT_FOUND,
                        gp.GP_ERROR_CAMERA_BUSY,
                    )
                    if veza_pukla and pokusaj == 0:
                        self._prekini()
                        continue
                    raise

    def model(self):
        try:
            return self.izvrsi(lambda k: k.get_abilities().model)
        except gp.GPhoto2Error:
            return self.povezi_ponovo()

    def status(self):
        """Vraća model i nivo baterije u okviru iste USB sesije."""
        def radnja(k):
            model = k.get_abilities().model
            try:
                baterija = k.get_single_config("batterylevel").get_value()
            except gp.GPhoto2Error:
                baterija = None
            return {"model": model, "baterija": baterija}

        try:
            return self.izvrsi(radnja)
        except gp.GPhoto2Error:
            return {"model": self.povezi_ponovo(), "baterija": None}

    def povezi_ponovo(self):
        """Zatvara moguću zastarelu USB vezu i uspostavlja novu."""
        self.prikaz_trazen = False
        with self.brava:
            self._prekini()
            try:
                kamera = self._povezi()
                return kamera.get_abilities().model
            except gp.GPhoto2Error:
                self._prekini()
                return None

    def kadar(self):
        def radnja(k):
            fajl = k.capture_preview()
            self.zivi_prikaz = True
            return bytes(fajl.get_data_and_size())

        return self.izvrsi(radnja)

    def ugasi_zivi_prikaz(self):
        self.prikaz_trazen = False

        def radnja(k):
            if self.zivi_prikaz:
                self._postavi(k, "viewfinder", 0)
                self.zivi_prikaz = False

        self.izvrsi(radnja)

    @staticmethod
    def lokalna_putanja_kamere(folder, ime):
        """Čuva strukturu DCIM sa kartice, bez internog PTP imena memorije."""
        delovi = [deo for deo in folder.split("/") if deo and deo not in (".", "..")] + [ime]
        if delovi and delovi[0].lower().startswith("store_"):
            delovi = delovi[1:]
        return Path(*delovi) if delovi else Path(ime)

    def okini(self, preuzmi=True):
        """Okida; NEF se može preuzeti odmah ili tek nakon završetka serije."""

        def radnja(k):
            putanje = [k.capture(gp.GP_CAPTURE_IMAGE)]
            # Drugi fajl (NEF uz JPEG) stiže kao događaj posle okidanja.
            for _ in range(50):
                tip, podatak = k.wait_for_event(200)
                if tip == gp.GP_EVENT_TIMEOUT:
                    break
                if tip == gp.GP_EVENT_FILE_ADDED:
                    putanje.append(podatak)
            nef_putanje = [p for p in putanje if Path(p.name).suffix.lower() == ".nef"]
            if not nef_putanje:
                raise RuntimeError("Okidanje je uspelo, ali nije pronađen NEF fajl za preuzimanje")
            if not preuzmi:
                return [p.folder.rstrip("/") + "/" + p.name for p in nef_putanje]
            poslednja_putanja = None
            for putanja in nef_putanje:
                odrediste = SNIMCI / self.lokalna_putanja_kamere(putanja.folder, putanja.name)
                odrediste.parent.mkdir(parents=True, exist_ok=True)
                fajl = k.file_get(putanja.folder, putanja.name, gp.GP_FILE_TYPE_NORMAL)
                fajl.save(str(odrediste))
                poslednja_putanja = odrediste.relative_to(SNIMCI).as_posix()
            return poslednja_putanja

        return self.izvrsi(radnja)

    def pokreni_seriju(self, broj, interval_ms):
        broj = max(1, min(500, int(broj)))
        interval_ms = max(0, min(60000, int(interval_ms)))
        with self.serija_zakljucavanje:
            if self.serija["radi"]:
                raise RuntimeError("Serijsko slikanje je već pokrenuto")
            self.serija_stop.clear()
            self.serija = {"radi": True, "faza": "snimanje", "ukupno": broj, "gotovo": 0, "sinhronizovano": 0, "poslednji": None, "greska": None}

        def snimaj():
            putanje = []
            try:
                for _ in range(broj):
                    if self.serija_stop.is_set():
                        break
                    pocetak = time.monotonic()
                    nove_putanje = self.okini(preuzmi=False)
                    putanje.extend(nove_putanje)
                    self.serija["gotovo"] += 1
                    self.serija["poslednji"] = nove_putanje[-1]
                    preostalo = interval_ms / 1000 - (time.monotonic() - pocetak)
                    if preostalo > 0 and self.serija_stop.wait(preostalo):
                        break
            except Exception as greska:
                self.serija["greska"] = str(greska)
            self.serija["faza"] = "prenos"
            for putanja in putanje:
                try:
                    kopija = self.preuzmi_sa_kartice(putanja)
                    self.serija["poslednji"] = kopija["putanja"]
                    self.serija["sinhronizovano"] += 1
                except Exception as greska:
                    self.serija["greska"] = str(greska)
                    break
            with self.serija_zakljucavanje:
                self.serija["radi"] = False

        threading.Thread(target=snimaj, name="nikon-serijsko-slikanje", daemon=True).start()
        return self.stanje_serije()

    def zaustavi_seriju(self):
        self.serija_stop.set()
        return self.stanje_serije()

    def stanje_serije(self):
        with self.serija_zakljucavanje:
            return dict(self.serija)

    def tacka_fokusa(self, x, y):
        """Pomera tačku fokusa na deo kadra, x i y su od 0 do 1."""
        vrednost_x = int(float(x) * SIRINA_KADRA)
        vrednost_y = int(float(y) * VISINA_KADRA)

        def radnja(k):
            # Prepoznavanje lica i praćenje objekta zanemaruju izabranu tačku, pa se prelazi na usku zonu.
            zona = k.get_single_config("liveviewafmode").get_value()
            promenjena = zona in ("Face-priority AF", "Subject-tracking AF")
            if promenjena:
                self._postavi(k, "liveviewafmode", "Normal-area AF")
            vidzet = k.get_single_config("changeafarea")
            vidzet.set_value(f"{vrednost_x}x{vrednost_y}")
            k.set_single_config("changeafarea", vidzet)
            return {"zona_promenjena": promenjena}

        return self.izvrsi(radnja)

    def autofokus(self):
        def radnja(k):
            # Na ručnom fokusu aparat primi komandu i javi uspeh, a ne pomeri objektiv.
            # Ručni AF režim živog prikaza se zato prvo vraća na AF-S.
            rezim_promenjen = k.get_single_config("liveviewaffocus").get_value().startswith("Manual")
            if rezim_promenjen:
                self._postavi(k, "liveviewaffocus", "Single-servo AF")
            if k.get_single_config("focusmode").get_value() == "Manual":
                raise RuntimeError("Aparat je i dalje na ručnom fokusu. Proveri prekidač A/M na objektivu")
            self._postavi(k, "autofocusdrive", 1)
            return {"rezim_promenjen": rezim_promenjen}

        try:
            return self.izvrsi(radnja)
        except gp.GPhoto2Error as greska:
            if greska.code == gp.GP_ERROR:
                raise RuntimeError(
                    "Autofokus nije uspeo da izoštri: premalo kontrasta ili svetla, "
                    "predmet je preblizu, ili je prekidač na objektivu na M"
                ) from greska
            raise

    def rucni_fokus(self, korak):
        self.izvrsi(lambda k: self._postavi(k, "manualfocusdrive", float(korak)))

    def video(self, ukljuci, odnos=None):
        """Pokreće ili zaustavlja video na kartici. Posle zaustavljanja vraća putanju novog snimka."""

        def radnja(k):
            if ukljuci:
                if not self.zivi_prikaz:
                    self._postavi(k, "viewfinder", 1)
                    time.sleep(1.5)
                    self.zivi_prikaz = True
                self._postavi(k, "movie", 1)
                return {}
            try:
                self._postavi(k, "movie", 0)
            except gp.GPhoto2Error as greska:
                # Snimanje se zaustavi, ali libgphoto2 posle toga ne uspe da vrati
                # režim aparata (Access Denied). To ne utiče na snimak.
                if greska.code != gp.GP_ERROR:
                    raise
            for _ in range(40):
                tip, podatak = k.wait_for_event(500)
                if tip == gp.GP_EVENT_FILE_ADDED:
                    return {"na_kartici": f"{podatak.folder}/{podatak.name}"}
            return {"na_kartici": None}

        rezultat = self.izvrsi(radnja)
        if not ukljuci and rezultat.get("na_kartici"):
            zabelezi_odnos_videa(rezultat["na_kartici"], odnos)
            rezultat["odnos"] = ODNOSI.get(odnos)
        return rezultat

    def sadrzaj_kartice(self):
        """Svi fajlovi na kartici aparata, najnoviji prvi."""

        def radnja(k):
            fajlovi = []

            def obidji(folder):
                lista = k.folder_list_files(folder)
                for i in range(lista.count()):
                    ime = lista.get_name(i)
                    # Rezultat mora ostati u promenljivoj, inače Python oslobodi memoriju pre čitanja.
                    info = k.file_get_info(folder, ime)
                    fajlovi.append(
                        {
                            "putanja": f"{folder.rstrip('/')}/{ime}",
                            "ime": ime,
                            "velicina": info.file.size,
                            "vreme": info.file.mtime,
                        }
                    )
                podfolderi = k.folder_list_folders(folder)
                for i in range(podfolderi.count()):
                    obidji(f"{folder.rstrip('/')}/{podfolderi.get_name(i)}")

            obidji("/")
            return fajlovi

        # PTP veza povremeno vrati samo generičku grešku pri čitanju direktorijuma.
        # Jedno potpuno ponovno povezivanje rešava privremeno zauzetu/zastarelu vezu.
        for pokusaj in range(2):
            try:
                fajlovi = self.izvrsi(radnja)
                break
            except gp.GPhoto2Error as greska:
                if pokusaj:
                    raise RuntimeError(
                        "Aparat nije uspeo da izlista karticu ni posle ponovnog povezivanja. "
                        "Proveri da je SD kartica ubačena i osveži spisak. "
                        f"Detalji: {greska}"
                    ) from greska
                with self.brava:
                    self._prekini()
                time.sleep(0.4)

        prenosi = provereni_prenosi()
        for fajl in fajlovi:
            lokalni = SNIMCI / self.lokalna_putanja_kamere(*fajl["putanja"].rsplit("/", 1))
            zapis = prenosi.get(fajl["putanja"], {})
            fajl["velicina_maca"] = lokalni.stat().st_size if lokalni.is_file() else 0
            velicina_kopije = zapis.get("velicina_maca")
            mtime_kopije = zapis.get("mtime_ns")
            provereno = (
                lokalni.is_file()
                and zapis.get("sha256")
                and zapis.get("velicina_kartice") == fajl["velicina"]
                and zapis.get("mtime_kartice") == fajl["vreme"]
                and lokalni.stat().st_size == velicina_kopije
                and lokalni.stat().st_mtime_ns == mtime_kopije
            )
            if provereno:
                provereno = kontrolni_zbir(lokalni) == zapis["sha256"]
            fajl["na_macu"] = bool(provereno)
            fajl["status_maca"] = (
                "provereno" if provereno else
                "nepotpuno" if lokalni.is_file() and lokalni.stat().st_size < fajl["velicina"] else
                "provera" if lokalni.is_file() else "nije"
            )
        fajlovi.sort(key=lambda f: (f["vreme"], f["ime"]), reverse=True)
        return {"fajlovi": fajlovi}

    def slicica_sa_kartice(self, putanja, sirina=480):
        """Sličica sa aparata se čuva lokalno kao WebP; izabrani NEF može dobiti veći pregled."""
        folder, ime = putanja.rsplit("/", 1)
        kljuc = hashlib.sha256(putanja.encode()).hexdigest()
        sufiks = f"-{sirina}" if sirina > 480 else ""
        kes = SLICICE / ("kartica-" + kljuc + sufiks + ".webp")
        jpg = kes.with_suffix(".jpg")
        if kes.is_file():
            return kes
        if jpg.is_file():
            return jpg
        SLICICE.mkdir(parents=True, exist_ok=True)
        if sirina > 480 and Path(ime).suffix.lower() == ".nef":
            izvor = SLICICE / ("kartica-" + kljuc + "-izvor.nef")
            umanjeni = SLICICE / ("kartica-" + kljuc + "-izvor.jpg")
            try:
                def radnja(k):
                    fajl = k.file_get(folder, ime, gp.GP_FILE_TYPE_NORMAL)
                    return bytes(fajl.get_data_and_size())

                izvor.write_bytes(self.izvrsi(radnja))
                rezultat = subprocess.run(
                    ["sips", "-s", "format", "jpeg", "-Z", str(sirina), str(izvor), "--out", str(umanjeni)],
                    capture_output=True,
                )
                if rezultat.returncode != 0 or not umanjeni.is_file():
                    raise RuntimeError("Aparat nije uspeo da napravi veći pregled NEF fotografije")
                webp = konvertuj_u_webp(umanjeni, kes, sirina)
                if webp:
                    return webp
                os.replace(umanjeni, jpg)
                return jpg
            finally:
                izvor.unlink(missing_ok=True)
                umanjeni.unlink(missing_ok=True)

        def radnja(k):
            fajl = k.file_get(folder, ime, gp.GP_FILE_TYPE_PREVIEW)
            return bytes(fajl.get_data_and_size())

        podaci = self.izvrsi(radnja)
        jpg.write_bytes(podaci)
        return konvertuj_u_webp(jpg, kes, sirina) or jpg

    def preuzmi_sa_kartice(self, putanja):
        """Preuzima u privremeni fajl, proverava potpunost i popravlja samo ako kopija nije ista."""
        if not isinstance(putanja, str) or "/" not in putanja:
            raise RuntimeError("Putanja fajla sa kartice nije ispravna")
        folder, ime = putanja.rsplit("/", 1)
        if not ime or ime in (".", "..") or Path(ime).name != ime:
            raise RuntimeError("Ime fajla sa kartice nije ispravno")
        if Path(ime).suffix.lower() in (".jpg", ".jpeg"):
            raise RuntimeError("JPEG fotografije se ne prebacuju; izaberi NEF ili video")
        if Path(ime).suffix.lower() not in (".nef", ".mov", ".mp4"):
            raise RuntimeError("Podržani su NEF fotografije i MOV/MP4 snimci")
        SNIMCI.mkdir(parents=True, exist_ok=True)
        cilj = SNIMCI / self.lokalna_putanja_kamere(folder, ime)
        cilj.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cilj.parent, prefix=".preuzimanje-", suffix=Path(ime).suffix, delete=False
        ) as privremeni:
            privremena_putanja = Path(privremeni.name)
        privremena_putanja.unlink()

        def radnja(k):
            # Omotač mora ostati živ: `info.file` je samo pogled na njegove podatke.
            info_omotac = k.file_get_info(folder, ime)
            info = info_omotac.file
            fajl = k.file_get(folder, ime, gp.GP_FILE_TYPE_NORMAL)
            podaci = fajl.get_data_and_size()
            if len(podaci) != info.size:
                raise RuntimeError(
                    f"Aparat je vratio {len(podaci)} od {info.size} bajtova za {ime}. "
                    "Kopija nije sačuvana; osveži vezu i pokušaj ponovo."
                )
            with privremena_putanja.open("wb") as izlaz:
                upisano = izlaz.write(podaci)
            if upisano != info.size:
                raise RuntimeError(f"Na Mac je upisano {upisano} od {info.size} bajtova za {ime}")
            return info.size, info.mtime

        try:
            for pokusaj in range(2):
                try:
                    velicina_kartice, mtime_kartice = self.izvrsi(radnja)
                    break
                except (gp.GPhoto2Error, RuntimeError):
                    if pokusaj:
                        raise
                    with self.brava:
                        self._prekini()
                    time.sleep(0.4)
            velicina_preuzeta = privremena_putanja.stat().st_size
            if velicina_preuzeta != velicina_kartice:
                raise RuntimeError(
                    f"Kopija nije potpuna ({velicina_preuzeta} od {velicina_kartice} bajtova). "
                    "Pokušaj ponovo."
                )

            sha256 = kontrolni_zbir(privremena_putanja)
            ista_kopija = cilj.is_file() and cilj.stat().st_size == velicina_kartice and kontrolni_zbir(cilj) == sha256
            if ista_kopija:
                privremena_putanja.unlink()
                stanje = "već-provereno"
            else:
                popravljena = cilj.is_file()
                os.replace(privremena_putanja, cilj)
                stanje = "popravljena-kopija" if popravljena else "preuzeto"

            stat = cilj.stat()
            prenosi = provereni_prenosi()
            prenosi[putanja] = {
                "velicina_kartice": velicina_kartice,
                "mtime_kartice": mtime_kartice,
                "velicina_maca": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256,
            }
            sacuvaj_proverene_prenose(prenosi)
        finally:
            if privremena_putanja.exists():
                privremena_putanja.unlink()

        pregled = napravi_pregled(cilj) if cilj.suffix.lower() == ".mov" else None
        return {"ime": ime, "putanja": cilj.relative_to(SNIMCI).as_posix(), "pregled": pregled, "stanje": stanje}

    def podesavanja(self):
        def radnja(k):
            rezultat = []
            for ime in PODESAVANJA:
                try:
                    vidzet = k.get_single_config(ime)
                except gp.GPhoto2Error:
                    continue
                if vidzet.get_type() not in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                    continue
                rezultat.append(
                    {
                        "ime": ime,
                        "naziv": vidzet.get_label(),
                        "vrednost": vidzet.get_value(),
                        "izbor": [vidzet.get_choice(i) for i in range(vidzet.count_choices())],
                        "samo_citanje": bool(vidzet.get_readonly()),
                    }
                )
            return rezultat

        return self.izvrsi(radnja)

    def postavi(self, ime, vrednost):
        def radnja(k):
            # Nikon u živom prikazu primi novu blendu, ali je primeni tek kad se prikaz ponovo pokrene.
            ponovo_pokreni = ime == "f-number" and self.zivi_prikaz
            if ponovo_pokreni:
                self._postavi(k, "viewfinder", 0)
                time.sleep(0.3)
            self._postavi(k, ime, vrednost)
            if ponovo_pokreni:
                self._postavi(k, "viewfinder", 1)
                time.sleep(1.0)

        self.izvrsi(radnja)

    def pusti(self):
        """Gasi živi prikaz i prekida vezu, da dugmad na aparatu opet rade. Sledeća radnja se ponovo poveže."""
        self.prikaz_trazen = False
        with self.brava:
            if self.kamera is not None and self.zivi_prikaz:
                try:
                    self._postavi(self.kamera, "viewfinder", 0)
                except gp.GPhoto2Error:
                    pass
            self._prekini()

    @staticmethod
    def _postavi(k, ime, vrednost):
        vidzet = k.get_single_config(ime)
        vidzet.set_value(vrednost)
        k.set_single_config(ime, vidzet)


def nadji_ffmpeg():
    """Pokrenut dvoklikom, server nema Homebrew u PATH-u, pa se ffmpeg traži i na uobičajenim mestima."""
    return shutil.which("ffmpeg") or next(
        (p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if Path(p).exists()), None
    )


def lokalna_slicica(fajl, sirina=480):
    """Pravi umanjenu WebP sličicu i pamti je pod putanjom relativnom prema snimci/."""
    osnovni_kljuc = hashlib.sha256(fajl.relative_to(SNIMCI).as_posix().encode()).hexdigest()
    kljuc = osnovni_kljuc + (f"-{sirina}" if sirina > 480 else "")
    kes = SLICICE / (kljuc + ".webp")
    jpg = kes.with_suffix(".jpg")
    if kes.is_file() and kes.stat().st_mtime >= fajl.stat().st_mtime:
        return kes
    SLICICE.mkdir(parents=True, exist_ok=True)
    if fajl.suffix.lower() in (".mov", ".mp4"):
        ffmpeg = nadji_ffmpeg()
        if ffmpeg:
            rezultat = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-i", str(fajl), "-frames:v", "1", "-vf", f"scale={sirina}:-2", "-c:v", "libwebp", "-quality", "72", str(kes)],
                capture_output=True,
            )
            if rezultat.returncode == 0 and kes.is_file():
                return kes
        return None
    privremeni = SLICICE / (kljuc + "-izvor.jpg")
    rezultat = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(sirina), str(fajl), "--out", str(privremeni)], capture_output=True)
    if rezultat.returncode != 0 or not privremeni.is_file():
        return None
    webp = konvertuj_u_webp(privremeni, kes, sirina)
    if not webp:
        os.replace(privremeni, jpg)
        return jpg
    privremeni.unlink(missing_ok=True)
    return webp


def konvertuj_u_webp(izvor, odrediste, sirina):
    ffmpeg = nadji_ffmpeg()
    if not ffmpeg:
        return None
    rezultat = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(izvor), "-frames:v", "1", "-vf", f"scale={sirina}:-2", "-c:v", "libwebp", "-quality", "72", str(odrediste)],
        capture_output=True,
    )
    return odrediste if rezultat.returncode == 0 and odrediste.is_file() else None


def napravi_pregled(video):
    """MOV sa aparata ima PCM zvuk koji Chrome ne pušta, pa se pravi MP4 kopija. Slika se ne prekodira."""
    ffmpeg = nadji_ffmpeg()
    if ffmpeg is None:
        return None
    PREGLEDI.mkdir(parents=True, exist_ok=True)
    relativna = video.relative_to(SNIMCI).as_posix()
    izlaz = PREGLEDI / (hashlib.sha256(relativna.encode()).hexdigest() + ".mp4")
    rezultat = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(video), "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(izlaz)],
        capture_output=True,
    )
    return f"pregled/{izlaz.name}" if rezultat.returncode == 0 else None


aparat = Aparat()


class Zahtev(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, podaci, status=200):
        telo = json.dumps(podaci, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(telo)))
        self.end_headers()
        self.wfile.write(telo)

    def _fajl(self, putanja, tip):
        """Šalje fajl, uz podršku za delove (Range), bez koje Safari ne pušta video."""
        velicina = putanja.stat().st_size
        pocetak, kraj = 0, velicina - 1
        opseg = self.headers.get("Range", "")
        if opseg.startswith("bytes="):
            od, _, do = opseg[6:].split(",")[0].partition("-")
            if od:
                pocetak = int(od)
                kraj = min(int(do), velicina - 1) if do else kraj
            elif do:
                pocetak = max(velicina - int(do), 0)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {pocetak}-{kraj}/{velicina}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", tip)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(kraj - pocetak + 1))
        self.end_headers()
        try:
            with putanja.open("rb") as ulaz:
                ulaz.seek(pocetak)
                preostalo = kraj - pocetak + 1
                while preostalo > 0:
                    deo = ulaz.read(min(1024 * 1024, preostalo))
                    if not deo:
                        break
                    self.wfile.write(deo)
                    preostalo -= len(deo)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._fajl(KOREN / "index.html", "text/html; charset=utf-8")
        if url.path == "/status":
            return self._json(aparat.status())
        if url.path == "/podesavanja":
            return self._obradi(lambda: {"podesavanja": aparat.podesavanja()})
        if url.path == "/snimci":
            fajlovi = sorted(
                (
                    f for f in SNIMCI.rglob("*")
                    if f.is_file()
                    and not any(deo.startswith(".") or deo == "pregled" for deo in f.relative_to(SNIMCI).parts)
                    and f.suffix.lower() in (".nef", ".mov", ".mp4")
                ),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            ) if SNIMCI.exists() else []
            snimci = []
            for fajl in fajlovi:
                relativna = fajl.relative_to(SNIMCI).as_posix()
                pregled = PREGLEDI / (hashlib.sha256(relativna.encode()).hexdigest() + ".mp4")
                snimci.append(
                    {
                        "ime": fajl.name,
                        "putanja": relativna,
                        "velicina": fajl.stat().st_size,
                        "pregled": f"pregled/{pregled.name}" if pregled.is_file() else None,
                    }
                )
            return self._json({"snimci": snimci})
        if url.path.startswith("/slicica/"):
            fajl = (SNIMCI / unquote(url.path[len("/slicica/"):])).resolve()
            if not fajl.is_relative_to(SNIMCI.resolve()):
                return self._json({"greska": "Neispravna putanja"}, 400)
            try:
                sirina = max(480, min(1920, int(parse_qs(url.query).get("sirina", ["480"])[0])))
            except ValueError:
                sirina = 480
            kes = lokalna_slicica(fajl, sirina) if fajl.is_file() else None
            if kes is not None:
                return self._fajl(kes, "image/webp" if kes.suffix == ".webp" else "image/jpeg")
        if url.path == "/kartica":
            return self._obradi(aparat.sadrzaj_kartice)
        if url.path == "/kartica/slicica":
            try:
                upit = parse_qs(url.query)
                sirina = max(480, min(1920, int(upit.get("sirina", ["480"])[0])))
                kes = aparat.slicica_sa_kartice(upit["putanja"][0], sirina)
                return self._fajl(kes, "image/webp" if kes.suffix == ".webp" else "image/jpeg")
            except (gp.GPhoto2Error, KeyError, ValueError, RuntimeError) as greska:
                if isinstance(greska, RuntimeError):
                    return self._json({"greska": str(greska)}, 500)
                return self._json({"greska": "Aparat nema sličicu za ovaj fajl"}, 404)
        if url.path.startswith("/snimci/"):
            fajl = (SNIMCI / unquote(url.path[len("/snimci/"):])).resolve()
            if not fajl.is_relative_to(SNIMCI.resolve()):
                return self._json({"greska": "Neispravna putanja"}, 400)
            if fajl.is_file():
                return self._fajl(fajl, TIPOVI.get(fajl.suffix.lower(), "application/octet-stream"))
        if url.path == "/serija/status":
            return self._json(aparat.stanje_serije())
        if url.path == "/spoljni-okidaci":
            return self._json(dict(aparat.spoljno_okidanje))
        if url.path == "/prikaz":
            return self._zivi_prikaz()
        self._json({"greska": "Nije pronađeno"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        upit = {k: v[0] for k, v in parse_qs(url.query).items()}
        radnje = {
            "/povezi": lambda: {"model": aparat.povezi_ponovo()},
            "/okini": lambda: {"snimak": aparat.okini()},
            "/serija/start": lambda: aparat.pokreni_seriju(upit.get("broj", "1"), upit.get("interval", "1000")),
            "/serija/stop": lambda: aparat.zaustavi_seriju(),
            "/tacka": lambda: aparat.tacka_fokusa(upit["x"], upit["y"]),
            "/autofokus": lambda: aparat.autofokus(),
            "/fokus": lambda: aparat.rucni_fokus(upit.get("korak", 0)),
            "/video": lambda: aparat.video(upit.get("ukljuci") == "1", upit.get("format")),
            "/preuzmi": lambda: aparat.preuzmi_sa_kartice(upit["putanja"]),
            "/postavi": lambda: aparat.postavi(upit["ime"], upit["vrednost"]),
            "/ugasi-prikaz": lambda: aparat.ugasi_zivi_prikaz(),
            "/pusti": lambda: aparat.pusti(),
        }
        if url.path in radnje:
            return self._obradi(radnje[url.path])
        self._json({"greska": "Nije pronađeno"}, 404)

    def _obradi(self, radnja):
        try:
            rezultat = radnja()
            self._json(rezultat if isinstance(rezultat, dict) else {"ok": True})
        except (gp.GPhoto2Error, RuntimeError) as greska:
            self._json({"greska": str(greska)}, 500)
        except Exception as greska:
            self._json({"greska": f"{type(greska).__name__}: {greska}"}, 500)

    def _zivi_prikaz(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=kadar")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        aparat.prikaz_trazen = True
        try:
            while aparat.prikaz_trazen:
                try:
                    kadar = aparat.kadar()
                except gp.GPhoto2Error:
                    time.sleep(0.5)
                    continue
                self.wfile.write(b"--kadar\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(kadar)}\r\n\r\n".encode())
                self.wfile.write(kadar + b"\r\n")
                # Aparat vraća isti kadar mnogo brže nego što se menja, pa se ograničava na oko 15 u sekundi.
                time.sleep(0.066)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Zahtev)
    print(f"Nikon D3400 daljinski: http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            aparat.ugasi_zivi_prikaz()
        except gp.GPhoto2Error:
            pass
