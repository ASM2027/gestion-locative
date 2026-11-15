"""Base de données SQLite du logiciel de gestion.

Le schéma couvre le minimum pour la gérance locative : patrimoine, baux,
appels de loyers, mouvements bancaires, écritures, sinistres, fournisseurs,
virements, e-mails de la boîte dédiée, propositions à valider et journal.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS comptes_bancaires (
    id INTEGER PRIMARY KEY, nom TEXT NOT NULL, banque TEXT NOT NULL,
    iban TEXT NOT NULL UNIQUE, bic TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proprietaires (
    id INTEGER PRIMARY KEY, nom TEXT NOT NULL, email TEXT, iban TEXT, bic TEXT,
    taux_honoraires REAL NOT NULL DEFAULT 0.07
);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY, immeuble TEXT NOT NULL, designation TEXT NOT NULL,
    proprietaire_id INTEGER NOT NULL REFERENCES proprietaires(id)
);
CREATE TABLE IF NOT EXISTS locataires (
    id INTEGER PRIMARY KEY, nom TEXT NOT NULL, email TEXT, telephone TEXT,
    lot_id INTEGER NOT NULL REFERENCES lots(id), reference TEXT NOT NULL UNIQUE,
    loyer REAL NOT NULL, charges REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS appels (
    id INTEGER PRIMARY KEY, locataire_id INTEGER NOT NULL REFERENCES locataires(id),
    periode TEXT NOT NULL, loyer REAL NOT NULL, charges REAL NOT NULL,
    regle REAL NOT NULL DEFAULT 0,
    UNIQUE (locataire_id, periode)
);
CREATE TABLE IF NOT EXISTS mouvements (
    id INTEGER PRIMARY KEY, compte_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    date TEXT NOT NULL, libelle TEXT NOT NULL, montant REAL NOT NULL,
    empreinte TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'a_pointer'   -- a_pointer | propose | pointe | ignore
);
CREATE TABLE IF NOT EXISTS ecritures (
    id INTEGER PRIMARY KEY, compte_id INTEGER NOT NULL REFERENCES comptes_bancaires(id),
    date TEXT NOT NULL, libelle TEXT NOT NULL, montant REAL NOT NULL,
    nature TEXT NOT NULL,                      -- encaissement | reversement | facture | frais | autre
    appel_id INTEGER REFERENCES appels(id),
    mouvement_id INTEGER REFERENCES mouvements(id)
);
CREATE TABLE IF NOT EXISTS fournisseurs (
    id INTEGER PRIMARY KEY, nom TEXT NOT NULL, email TEXT, iban TEXT, bic TEXT,
    iban_verifie INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS factures (
    id INTEGER PRIMARY KEY, fournisseur_id INTEGER NOT NULL REFERENCES fournisseurs(id),
    lot_id INTEGER REFERENCES lots(id), objet TEXT NOT NULL, montant REAL NOT NULL,
    statut TEXT NOT NULL DEFAULT 'a_payer'     -- a_payer | dans_un_lot | payee
);
CREATE TABLE IF NOT EXISTS sinistres (
    id INTEGER PRIMARY KEY, lot_id INTEGER REFERENCES lots(id), declarant TEXT NOT NULL,
    date_declaration TEXT NOT NULL, nature TEXT NOT NULL, description TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'ouvert',     -- ouvert | declare_assureur | expertise | clos
    pieces_manquantes TEXT NOT NULL DEFAULT '[]', email_id INTEGER
);
CREATE TABLE IF NOT EXISTS lots_virements (
    id INTEGER PRIMARY KEY, cree_le TEXT NOT NULL, compte_id INTEGER NOT NULL,
    statut TEXT NOT NULL DEFAULT 'prepare',    -- prepare | bloque | valide | signe_en_banque
    total REAL NOT NULL, nb INTEGER NOT NULL, fichier TEXT, alertes TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS virements (
    id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL REFERENCES lots_virements(id),
    beneficiaire TEXT NOT NULL, iban TEXT NOT NULL, bic TEXT, montant REAL NOT NULL,
    motif TEXT NOT NULL, origine TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY, message_id TEXT UNIQUE, sens TEXT NOT NULL,  -- entrant | sortant
    expediteur TEXT NOT NULL, destinataire TEXT NOT NULL, sujet TEXT NOT NULL,
    corps TEXT NOT NULL, date TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'nouveau',    -- nouveau | traite | brouillon | envoye
    categorie TEXT, autorise INTEGER NOT NULL DEFAULT 0, compte_rendu TEXT
);
CREATE TABLE IF NOT EXISTS propositions (
    id INTEGER PRIMARY KEY, type TEXT NOT NULL, resume TEXT NOT NULL,
    donnees TEXT NOT NULL, confiance TEXT NOT NULL DEFAULT 'moyenne',
    statut TEXT NOT NULL DEFAULT 'en_attente', -- en_attente | validee | refusee
    origine TEXT NOT NULL, cree_le TEXT NOT NULL, decide_par TEXT, decide_le TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY, quand TEXT NOT NULL, acteur TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parametres (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
"""


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connecter(chemin: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(chemin), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def journaliser(conn: sqlite3.Connection, acteur: str, action: str, detail: dict | str) -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute(
        "INSERT INTO journal (quand, acteur, action, detail) VALUES (?, ?, ?, ?)",
        (maintenant(), acteur, action, detail),
    )


def parametre(conn: sqlite3.Connection, cle: str, defaut: str = "") -> str:
    row = conn.execute("SELECT valeur FROM parametres WHERE cle = ?", (cle,)).fetchone()
    return row["valeur"] if row else defaut


def definir_parametre(conn: sqlite3.Connection, cle: str, valeur: str) -> None:
    conn.execute(
        "INSERT INTO parametres (cle, valeur) VALUES (?, ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, valeur),
    )
