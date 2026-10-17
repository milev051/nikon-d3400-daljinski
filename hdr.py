"""Spaja snimke različite ekspozicije u jednu sliku (Mertens exposure fusion).

Upotreba: python hdr.py IZLAZ_BEZ_EKSTENZIJE ULAZ1 ULAZ2 ...
Pravi IZLAZ.tif (16 bita, za dalju obradu) i IZLAZ.jpg (pregled).
NEF se razvija u 16 bita sa balansom bele iz aparata. Obrada ide sliku po sliku,
da 24 MP snimci stanu u memoriju Mac-a sa 8 GB.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

NIVOI = 8


def ucitaj(putanja):
    """Vraća sliku kao uint16 BGR."""
    if Path(putanja).suffix.lower() == ".nef":
        import rawpy

        with rawpy.imread(str(putanja)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    slika = cv2.imread(str(putanja), cv2.IMREAD_ANYDEPTH | cv2.IMREAD_COLOR)  # poštuje EXIF orijentaciju
    if slika is None:
        raise RuntimeError(f"Ne mogu da pročitam {putanja}")
    return slika.astype(np.uint16) * 257 if slika.dtype == np.uint8 else slika


def poravnaj(slike):
    """Stalak se može malo pomeriti pri okidanju; MTB nalazi pomeraj u odnosu na srednji snimak."""
    mtb = cv2.createAlignMTB()
    sive = [cv2.cvtColor((s >> 8).astype(np.uint8), cv2.COLOR_BGR2GRAY) for s in slike]
    referenca = sive[len(sive) // 2]
    for i, siva in enumerate(sive):
        dx, dy = mtb.calculateShift(referenca, siva)
        if dx or dy:
            matrica = np.float32([[1, 0, dx], [0, 1, dy]])
            slike[i] = cv2.warpAffine(slike[i], matrica, slike[i].shape[1::-1], borderMode=cv2.BORDER_REFLECT)


def tezina(slika):
    """Kontrast, zasićenost i dobra eksponiranost, kao u Mertens i sar. 2007."""
    f = slika.astype(np.float32) / 65535
    siva = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    kontrast = np.abs(cv2.Laplacian(siva, cv2.CV_32F))
    zasicenost = f.std(axis=2)
    eksponiranost = np.exp(-((f - 0.5) ** 2) / (2 * 0.2**2)).prod(axis=2)
    return kontrast * zasicenost * eksponiranost + 1e-12


def gausova(slika, nivoi):
    piramida = [slika]
    for _ in range(nivoi - 1):
        piramida.append(cv2.pyrDown(piramida[-1]))
    return piramida


def laplasova(slika, nivoi):
    gaus = gausova(slika, nivoi)
    piramida = [gaus[i] - cv2.pyrUp(gaus[i + 1], dstsize=gaus[i].shape[1::-1]) for i in range(nivoi - 1)]
    return piramida + [gaus[-1]]


def spoji(ulazi):
    slike = [ucitaj(p) for p in ulazi]
    if len({s.shape for s in slike}) != 1:
        raise RuntimeError("Snimci nisu iste veličine")
    poravnaj(slike)
    tezine = [tezina(s) for s in slike]
    zbir = sum(tezine)
    nivoi = min(NIVOI, int(np.log2(min(slike[0].shape[:2]))) - 2)
    rezultat = None
    for slika, w in zip(slike, tezine):
        w_pir = gausova(w / zbir, nivoi)
        l_pir = laplasova(slika.astype(np.float32) / 65535, nivoi)
        doprinos = [l * g[..., None] for l, g in zip(l_pir, w_pir)]
        rezultat = doprinos if rezultat is None else [r + d for r, d in zip(rezultat, doprinos)]
    slika = rezultat[-1]
    for nivo in reversed(rezultat[:-1]):
        slika = cv2.pyrUp(slika, dstsize=nivo.shape[1::-1]) + nivo
    return np.clip(slika, 0, 1)


def main():
    izlaz, ulazi = Path(sys.argv[1]), sys.argv[2:]
    if len(ulazi) < 2:
        raise SystemExit("Potrebna su bar dva snimka")
    slika = spoji(ulazi)
    izlaz.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(izlaz.with_suffix(".tif")), (slika * 65535 + 0.5).astype(np.uint16))
    cv2.imwrite(str(izlaz.with_suffix(".jpg")), (slika * 255 + 0.5).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 95])


if __name__ == "__main__":
    main()
