"""Boîte e-mail dédiée : seule porte d'entrée et de sortie du logiciel.

- Tout e-mail reçu est enregistré et classé.
- Seuls les expéditeurs de la liste blanche (les gestionnaires) peuvent donner
  des ordres. Un e-mail de locataire, d'assureur ou d'inconnu est une pièce à
  analyser, jamais une instruction.
- Claude n'envoie rien : il prépare des brouillons, qui partent après validation.

En production, `BoiteGmail` relève et envoie via l'API Gmail du compte Google
Workspace dédié. Dans le prototype, `BoiteDossier` lit des fichiers .eml.
"""
from __future__ import annotations

import email
import email.policy
import json
import sqlite3
from email.utils import parseaddr
from pathlib import Path

from .db import journaliser, maintenant, parametre
from .validation import creer_proposition


def expediteurs_autorises(conn: sqlite3.Connection) -> set[str]:
    return {a.lower() for a in json.loads(parametre(conn, "expediteurs_autorises", "[]"))}


def recevoir(conn: sqlite3.Connection, expediteur: str, sujet: str, corps: str,
             message_id: str | None = None, date: str | None = None) -> int | None:
    """Enregistre un e-mail entrant. Retourne son id, ou None si déjà reçu."""
    adresse = parseaddr(expediteur)[1].lower() or expediteur.lower()
    autorise = adresse in expediteurs_autorises(conn)
    cur = conn.execute(
        "INSERT OR IGNORE INTO emails (message_id, sens, expediteur, destinataire, sujet, corps, date, autorise) "
        "VALUES (?, 'entrant', ?, ?, ?, ?, ?, ?)",
        (message_id or f"local-{maintenant()}-{hash((adresse, sujet, corps))}", adresse,
         parametre(conn, "adresse_dediee", "claude@agence.example"), sujet, corps,
         date or maintenant(), int(autorise)),
    )
    if not cur.rowcount:
        return None
    journaliser(conn, "boite", "email_recu", {"id": cur.lastrowid, "de": adresse, "sujet": sujet,
                                               "autorise": autorise})
    conn.commit()
    return cur.lastrowid


def preparer_email(conn: sqlite3.Connection, destinataire: str, sujet: str, corps: str,
                   origine: str) -> int:
    """Crée un brouillon depuis l'adresse dédiée et le soumet à validation."""
    cur = conn.execute(
        "INSERT INTO emails (sens, expediteur, destinataire, sujet, corps, date, statut) "
        "VALUES ('sortant', ?, ?, ?, ?, ?, 'brouillon')",
        (parametre(conn, "adresse_dediee", "claude@agence.example"), destinataire, sujet, corps, maintenant()),
    )
    creer_proposition(conn, "email", f"Envoyer à {destinataire} : « {sujet} »",
                      {"email_id": cur.lastrowid, "destinataire": destinataire, "sujet": sujet}, origine)
    conn.commit()
    return cur.lastrowid


def a_traiter(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM emails WHERE sens = 'entrant' AND statut = 'nouveau' ORDER BY id"
    ).fetchall()


class BoiteDossier:
    """Relève les fichiers .eml d'un dossier (démonstration et tests)."""

    def __init__(self, dossier: Path):
        self.dossier = Path(dossier)

    def relever(self, conn: sqlite3.Connection) -> int:
        n = 0
        for f in sorted(self.dossier.glob("*.eml")):
            msg = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
            partie = msg.get_body(preferencelist=("plain",))
            corps = partie.get_content() if partie else ""
            if recevoir(conn, msg["From"] or "", msg["Subject"] or "", corps,
                        message_id=msg["Message-ID"] or f.name):
                n += 1
        return n


class BoiteGmail:
    """Compte Google Workspace dédié (à brancher au déploiement).

    Mise en place prévue : un compte claude@votre-agence.fr, un compte de
    service Google avec délégation limitée aux portées gmail.readonly,
    gmail.compose et gmail.send sur ce seul compte, et une relève toutes les
    minutes (ou une notification Pub/Sub). Les pièces jointes (relevés CFONB,
    OFX, photos de sinistre, factures PDF) sont enregistrées avec l'e-mail.
    """

    def relever(self, conn: sqlite3.Connection) -> int:
        raise NotImplementedError("Connexion Gmail non configurée dans le prototype.")

    def envoyer(self, conn: sqlite3.Connection, email_id: int) -> None:
        raise NotImplementedError("Envoi Gmail non configuré : le prototype n'envoie rien.")
