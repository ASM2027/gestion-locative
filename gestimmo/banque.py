"""Import des relevés bancaires, quelle que soit la banque.

Trois formats couvrent toutes les banques françaises :
- CFONB 120 : relevé normé que toutes les banques fournissent aux professionnels
  (LCL, CIC, BNP, SG, CA, CE, BP, La Banque Postale, Qonto...).
- OFX : export proposé par la plupart des banques en ligne.
- CSV : dernier recours, colonnes détectées automatiquement.

Les relevés arrivent soit en pièce jointe sur la boîte dédiée, soit par un
agrégateur agréé DSP2 (voir `Agregateur`), en lecture seule.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from .db import journaliser


@dataclass
class Mouvement:
    date: str          # AAAA-MM-JJ
    libelle: str
    montant: float     # positif = crédit, négatif = débit
    reference: str = ""


# ---------------------------------------------------------------- CFONB 120
_SIGNES = {"{": (0, 1), "}": (0, -1)}
_SIGNES.update({c: (i + 1, 1) for i, c in enumerate("ABCDEFGHI")})
_SIGNES.update({c: (i + 1, -1) for i, c in enumerate("JKLMNOPQR")})


def _montant_cfonb(zone: str, decimales: int) -> float:
    zone = zone.strip()
    chiffre, signe = _SIGNES[zone[-1]]
    valeur = int(zone[:-1] + str(chiffre))
    return signe * valeur / (10 ** decimales)


def _date_jjmmaa(s: str) -> str:
    return datetime.strptime(s, "%d%m%y").date().isoformat()


def lire_cfonb120(texte: str) -> list[Mouvement]:
    mouvements: list[Mouvement] = []
    for ligne in texte.splitlines():
        if len(ligne) < 104:
            continue
        code = ligne[0:2]
        if code == "04":
            decimales = int(ligne[19])
            mouvements.append(Mouvement(
                date=_date_jjmmaa(ligne[34:40]),
                libelle=ligne[48:79].strip(),
                montant=_montant_cfonb(ligne[90:104], decimales),
                reference=ligne[104:120].strip(),
            ))
        elif code == "05" and mouvements:
            # Enregistrement complémentaire : suite du libellé (nom du donneur d'ordre, motif)
            mouvements[-1].libelle += " " + ligne[48:118].strip()
    return mouvements


# ---------------------------------------------------------------- OFX
def lire_ofx(texte: str) -> list[Mouvement]:
    mouvements = []
    for bloc in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", texte, re.S):
        def champ(nom: str) -> str:
            m = re.search(rf"<{nom}>([^<\r\n]*)", bloc)
            return m.group(1).strip() if m else ""
        brut = champ("DTPOSTED")[:8]
        libelle = " ".join(x for x in (champ("NAME"), champ("MEMO")) if x)
        mouvements.append(Mouvement(
            date=f"{brut[:4]}-{brut[4:6]}-{brut[6:8]}",
            libelle=libelle,
            montant=float(champ("TRNAMT").replace(",", ".")),
            reference=champ("FITID"),
        ))
    return mouvements


# ---------------------------------------------------------------- CSV
def _nombre(s: str) -> float:
    s = s.strip().replace(" ", "").replace(" ", "").replace("€", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "")
    return float(s.replace(",", "."))


def _date_libre(s: str) -> str:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"date illisible : {s}")


def lire_csv(texte: str) -> list[Mouvement]:
    sep = ";" if texte.count(";") >= texte.count(",") else ","
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=sep)
    cols = {c.lower().strip(): c for c in (lecteur.fieldnames or [])}

    def col(*noms: str) -> str | None:
        for n in noms:
            for k, v in cols.items():
                if k.startswith(n):
                    return v
        return None

    c_date = col("date op", "date")
    c_lib = col("libell", "label", "description")
    c_mt, c_deb, c_cred = col("montant", "amount"), col("débit", "debit"), col("crédit", "credit")
    if not (c_date and c_lib and (c_mt or (c_deb and c_cred))):
        raise ValueError("colonnes date / libellé / montant introuvables dans le CSV")
    mouvements = []
    for row in lecteur:
        if not (row.get(c_date) or "").strip():
            continue
        if c_mt:
            montant = _nombre(row[c_mt])
        else:
            montant = _nombre(row[c_cred]) - abs(_nombre(row[c_deb]))
        mouvements.append(Mouvement(_date_libre(row[c_date]), row[c_lib].strip(), montant))
    return mouvements


def detecter_et_lire(nom_fichier: str, texte: str) -> tuple[str, list[Mouvement]]:
    nom = nom_fichier.lower()
    if "<OFX>" in texte.upper() or nom.endswith((".ofx", ".qfx")):
        return "ofx", lire_ofx(texte)
    premiere = texte.lstrip().splitlines()[0] if texte.strip() else ""
    if premiere[:2] == "01" and (nom.endswith((".cfonb", ".cfo", ".txt", ".dat"))
                                 or len(premiere.rstrip("\r")) == 120):
        return "cfonb120", lire_cfonb120(texte)
    return "csv", lire_csv(texte)


def importer(conn: sqlite3.Connection, compte_id: int, nom_fichier: str, texte: str,
             acteur: str = "import") -> dict:
    """Enregistre les mouvements, sans doublon (un relevé réimporté ne double rien)."""
    fmt, mouvements = detecter_et_lire(nom_fichier, texte)
    nouveaux = 0
    for i, m in enumerate(mouvements):
        cle = f"{compte_id}|{m.date}|{m.montant:.2f}|{m.libelle}|{m.reference or i}"
        empreinte = hashlib.sha256(cle.encode()).hexdigest()[:32]
        cur = conn.execute(
            "INSERT OR IGNORE INTO mouvements (compte_id, date, libelle, montant, empreinte, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (compte_id, m.date, m.libelle, round(m.montant, 2), empreinte, f"{fmt}:{nom_fichier}"),
        )
        nouveaux += cur.rowcount
    journaliser(conn, acteur, "import_releve",
                {"fichier": nom_fichier, "format": fmt, "lus": len(mouvements), "nouveaux": nouveaux})
    conn.commit()
    return {"format": fmt, "lus": len(mouvements), "nouveaux": nouveaux}


# ---------------------------------------------------------------- Agrégateur
class Agregateur:
    """Connexion en lecture seule à toutes les banques via un agrégateur agréé
    (par exemple Bridge ou Powens). À brancher quand le contrat est signé :
    `lister_mouvements` doit renvoyer des `Mouvement`, le reste du logiciel
    ne change pas. Aucun agrégateur n'a accès à l'émission de virements ici."""

    nom = "non configuré"

    def lister_mouvements(self, iban: str, depuis: date) -> list[Mouvement]:
        raise NotImplementedError("Agrégateur bancaire non configuré : importez les relevés CFONB/OFX/CSV.")
