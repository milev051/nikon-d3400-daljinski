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
from urllib.parse import parse_qs, urlparse

import gphoto2 as gp

KOREN = Path(__file__).parent
SNIMCI = KOREN / "snimci"
PREGLEDI = SNIMCI / "pregled"
SLICICE = SNIMCI / ".slicice"
PREUZIMANJA = SNIMCI / ".preuzeto.json"
PODFOLDERI = {"provere": SNIMCI / "provere", "pregled": PREGLEDI}
TIPOVI = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
PORT = 8400


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

    def okini(self, folder=SNIMCI):
        """Okida i prebacuje snimak. Uz NEF+JPEG stižu dva fajla, vraća se ime JPEG-a."""

        def radnja(k):
            putanje = [k.capture(gp.GP_CAPTURE_IMAGE)]
            # Drugi fajl (NEF uz JPEG) stiže kao događaj posle okidanja.
            for _ in range(50):
                tip, podatak = k.wait_for_event(200)
                if tip == gp.GP_EVENT_TIMEOUT:
                    break
                if tip == gp.GP_EVENT_FILE_ADDED:
                    putanje.append(podatak)
            osnova = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            imena = []
            for putanja in putanje:
                ime = osnova + (Path(putanja.name).suffix.lower() or ".jpg")
                fajl = k.file_get(putanja.folder, putanja.name, gp.GP_FILE_TYPE_NORMAL)
                fajl.save(str(folder / ime))
                imena.append(ime)
            jpeg = [ime for ime in imena if ime.endswith((".jpg", ".jpeg"))]
            return (jpeg or imena)[0]

        return self.izvrsi(radnja)

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

    def video(self, ukljuci):
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

        return self.izvrsi(radnja)

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
            lokalni = SNIMCI / fajl["ime"]
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

    def slicica_sa_kartice(self, putanja):
        """Sličica koju aparat već čuva uz svaki snimak. Pamti se na disku, da se ne čita ponovo."""
        folder, ime = putanja.rsplit("/", 1)
        kes = SLICICE / ("kartica" + folder.replace("/", "_") + "_" + ime + ".jpg")
        if not kes.is_file():

            def radnja(k):
                fajl = k.file_get(folder, ime, gp.GP_FILE_TYPE_PREVIEW)
                return bytes(fajl.get_data_and_size())

            podaci = self.izvrsi(radnja)
            SLICICE.mkdir(parents=True, exist_ok=True)
            kes.write_bytes(podaci)
        return kes

    def preuzmi_sa_kartice(self, putanja):
        """Preuzima u privremeni fajl, proverava potpunost i popravlja samo ako kopija nije ista."""
        if not isinstance(putanja, str) or "/" not in putanja:
            raise RuntimeError("Putanja fajla sa kartice nije ispravna")
        folder, ime = putanja.rsplit("/", 1)
        if not ime or ime in (".", "..") or Path(ime).name != ime:
            raise RuntimeError("Ime fajla sa kartice nije ispravno")
        SNIMCI.mkdir(parents=True, exist_ok=True)
        cilj = SNIMCI / ime
        with tempfile.NamedTemporaryFile(dir=SNIMCI, prefix=".preuzimanje-", delete=False) as privremeni:
            privremena_putanja = Path(privremeni.name)

        def radnja(k):
            info = k.file_get_info(folder, ime).file
            fajl = k.file_get(folder, ime, gp.GP_FILE_TYPE_NORMAL)
            fajl.save(str(privremena_putanja))
            return info.size, info.mtime

        try:
            velicina_kartice, mtime_kartice = self.izvrsi(radnja)
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
        return {"ime": ime, "pregled": pregled, "stanje": stanje}

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


def lokalna_slicica(fajl):
    """Sličica za fajl na Mac-u: slike i NEF preko sips, video preko ffmpeg. Pamti se na disku."""
    kes = SLICICE / (fajl.name + ".jpg")
    if kes.is_file() and kes.stat().st_mtime >= fajl.stat().st_mtime:
        return kes
    SLICICE.mkdir(parents=True, exist_ok=True)
    if fajl.suffix.lower() in (".mov", ".mp4"):
        ffmpeg = nadji_ffmpeg()
        if ffmpeg is None:
            return None
        naredba = [ffmpeg, "-y", "-v", "error", "-i", str(fajl), "-frames:v", "1", "-vf", "scale=480:-2", str(kes)]
    else:
        naredba = ["sips", "-s", "format", "jpeg", "-Z", "480", str(fajl), "--out", str(kes)]
    subprocess.run(naredba, capture_output=True)
    return kes if kes.is_file() else None


def napravi_pregled(video):
    """MOV sa aparata ima PCM zvuk koji Chrome ne pušta, pa se pravi MP4 kopija. Slika se ne prekodira."""
    ffmpeg = nadji_ffmpeg()
    if ffmpeg is None:
        return None
    PREGLEDI.mkdir(parents=True, exist_ok=True)
    izlaz = PREGLEDI / (video.stem + ".mp4")
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
            return self._json({"model": aparat.model()})
        if url.path == "/podesavanja":
            return self._obradi(lambda: {"podesavanja": aparat.podesavanja()})
        if url.path == "/snimci":
            fajlovi = sorted(
                (f for f in SNIMCI.iterdir() if f.is_file() and not f.name.startswith(".")),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            snimci = []
            for fajl in fajlovi:
                pregled = PREGLEDI / (fajl.stem + ".mp4")
                snimci.append(
                    {
                        "ime": fajl.name,
                        "velicina": fajl.stat().st_size,
                        "pregled": f"pregled/{pregled.name}" if pregled.is_file() else None,
                    }
                )
            return self._json({"snimci": snimci})
        if url.path.startswith("/slicica/"):
            fajl = SNIMCI / Path(url.path).name
            kes = lokalna_slicica(fajl) if fajl.is_file() else None
            if kes is not None:
                return self._fajl(kes, "image/jpeg")
        if url.path == "/kartica":
            return self._obradi(aparat.sadrzaj_kartice)
        if url.path == "/kartica/slicica":
            try:
                return self._fajl(aparat.slicica_sa_kartice(parse_qs(url.query)["putanja"][0]), "image/jpeg")
            except (gp.GPhoto2Error, KeyError):
                return self._json({"greska": "Aparat nema sličicu za ovaj fajl"}, 404)
        if url.path.startswith("/snimci/"):
            delovi = Path(url.path).parts
            folder = PODFOLDERI.get(delovi[2], SNIMCI) if len(delovi) > 3 else SNIMCI
            fajl = folder / delovi[-1]
            if fajl.is_file():
                return self._fajl(fajl, TIPOVI.get(fajl.suffix.lower(), "application/octet-stream"))
        if url.path == "/prikaz":
            return self._zivi_prikaz()
        self._json({"greska": "Nije pronađeno"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        upit = {k: v[0] for k, v in parse_qs(url.query).items()}
        radnje = {
            "/povezi": lambda: {"model": aparat.povezi_ponovo()},
            "/okini": lambda: {"snimak": aparat.okini()},
            "/tacka": lambda: aparat.tacka_fokusa(upit["x"], upit["y"]),
            "/autofokus": lambda: aparat.autofokus(),
            "/fokus": lambda: aparat.rucni_fokus(upit.get("korak", 0)),
            "/video": lambda: aparat.video(upit.get("ukljuci") == "1"),
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
