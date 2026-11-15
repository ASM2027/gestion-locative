"""File de validation : Claude propose, un humain décide.

Aucune proposition ne modifie la comptabilité, n'envoie d'e-mail ou ne
prépare de paiement tant qu'une personne ne l'a pas validée.
"""
from __future__ import annotations

import json
import sqlite3

from .db import journaliser, maintenant

TYPES = {"pointage", "ecriture", "lettrage", "sinistre", "email", "lot_virements", "alerte"}


class ValidationErreur(Exception):
    pass


def creer_proposition(conn: sqlite3.Connection, type_: str, resume: str, donnees: dict,
                      origine: str, confiance: str = "moyenne") -> int:
    if type_ not in TYPES:
        raise ValueError(f"type de proposition inconnu : {type_}")
    cur = conn.execute(
        "INSERT INTO propositions (type, resume, donnees, confiance, origine, cree_le) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (type_, resume, json.dumps(donnees, ensure_ascii=False), confiance, origine, maintenant()),
    )
    journaliser(conn, origine, f"proposition_{type_}", {"id": cur.lastrowid, "resume": resume})
    return cur.lastrowid


def en_attente(conn: sqlite3.Connection, type_: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM propositions WHERE statut = 'en_attente'"
    args: tuple = ()
    if type_:
        sql += " AND type = ?"
        args = (type_,)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def refuser(conn: sqlite3.Connection, prop_id: int, par: str) -> None:
    prop = _charger(conn, prop_id)
    conn.execute(
        "UPDATE propositions SET statut = 'refusee', decide_par = ?, decide_le = ? WHERE id = ?",
        (par, maintenant(), prop_id),
    )
    if prop["type"] in ("pointage", "ecriture", "lettrage"):
        d = json.loads(prop["donnees"])
        conn.execute("UPDATE mouvements SET statut = 'a_pointer' WHERE id = ? AND statut = 'propose'",
                     (d.get("mouvement_id"),))
    journaliser(conn, par, "refus", {"proposition": prop_id})
    conn.commit()


def valider(conn: sqlite3.Connection, prop_id: int, par: str) -> str:
    """Applique la proposition. Retourne un compte rendu lisible."""
    prop = _charger(conn, prop_id)
    d = json.loads(prop["donnees"])
    applique = {
        "pointage": _appliquer_pointage,
        "ecriture": _appliquer_ecriture,
        "lettrage": _appliquer_lettrage,
        "sinistre": _appliquer_sinistre,
        "email": _appliquer_email,
        "lot_virements": _appliquer_lot,
        "alerte": lambda c, d: "Alerte prise en compte.",
    }[prop["type"]]
    try:
        compte_rendu = applique(conn, d)
    except Exception:
        conn.rollback()
        raise
    conn.execute(
        "UPDATE propositions SET statut = 'validee', decide_par = ?, decide_le = ? WHERE id = ?",
        (par, maintenant(), prop_id),
    )
    journaliser(conn, par, "validation", {"proposition": prop_id, "resultat": compte_rendu})
    conn.commit()
    return compte_rendu


def _charger(conn: sqlite3.Connection, prop_id: int) -> sqlite3.Row:
    prop = conn.execute("SELECT * FROM propositions WHERE id = ?", (prop_id,)).fetchone()
    if prop is None:
        raise ValidationErreur(f"proposition {prop_id} introuvable")
    if prop["statut"] != "en_attente":
        raise ValidationErreur(f"proposition {prop_id} déjà {prop['statut']}")
    return prop


def _appliquer_pointage(conn: sqlite3.Connection, d: dict) -> str:
    mvt = conn.execute("SELECT * FROM mouvements WHERE id = ?", (d["mouvement_id"],)).fetchone()
    if mvt is None or mvt["statut"] == "pointe":
        raise ValidationErreur("mouvement introuvable ou déjà pointé")
    appel = conn.execute(
        "SELECT a.*, l.nom FROM appels a JOIN locataires l ON l.id = a.locataire_id WHERE a.id = ?",
        (d["appel_id"],),
    ).fetchone()
    if appel is None:
        raise ValidationErreur("appel de loyer introuvable")
    montant = round(float(d["montant"]), 2)
    conn.execute("UPDATE appels SET regle = round(regle + ?, 2) WHERE id = ?", (montant, appel["id"]))
    conn.execute(
        "INSERT INTO ecritures (compte_id, date, libelle, montant, nature, appel_id, mouvement_id) "
        "VALUES (?, ?, ?, ?, 'encaissement', ?, ?)",
        (mvt["compte_id"], mvt["date"], f"Loyer {appel['periode']} {appel['nom']}", montant,
         appel["id"], mvt["id"]),
    )
    conn.execute("UPDATE mouvements SET statut = 'pointe' WHERE id = ?", (mvt["id"],))
    return f"{montant:.2f} € pointés sur le loyer {appel['periode']} de {appel['nom']}."


def _appliquer_ecriture(conn: sqlite3.Connection, d: dict) -> str:
    conn.execute(
        "INSERT INTO ecritures (compte_id, date, libelle, montant, nature, mouvement_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (d["compte_id"], d["date"], d["libelle"], d["montant"], d.get("nature", "autre"),
         d.get("mouvement_id")),
    )
    if d.get("mouvement_id"):
        conn.execute("UPDATE mouvements SET statut = 'pointe' WHERE id = ?", (d["mouvement_id"],))
    return f"Écriture passée : {d['libelle']} ({d['montant']:.2f} €)."


def _appliquer_lettrage(conn: sqlite3.Connection, d: dict) -> str:
    ecr = conn.execute("SELECT * FROM ecritures WHERE id = ?", (d["ecriture_id"],)).fetchone()
    if ecr is None or ecr["mouvement_id"] is not None:
        raise ValidationErreur("écriture introuvable ou déjà rapprochée")
    conn.execute("UPDATE ecritures SET mouvement_id = ? WHERE id = ?", (d["mouvement_id"], ecr["id"]))
    conn.execute("UPDATE mouvements SET statut = 'pointe' WHERE id = ?", (d["mouvement_id"],))
    return f"Débit rapproché de « {ecr['libelle']} »."


def _appliquer_sinistre(conn: sqlite3.Connection, d: dict) -> str:
    cur = conn.execute(
        "INSERT INTO sinistres (lot_id, declarant, date_declaration, nature, description, "
        "pieces_manquantes, email_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (d.get("lot_id"), d["declarant"], d["date"], d["nature"], d["description"],
         json.dumps(d.get("pieces_manquantes", []), ensure_ascii=False), d.get("email_id")),
    )
    return f"Dossier sinistre n°{cur.lastrowid} ouvert ({d['nature']})."


def _appliquer_email(conn: sqlite3.Connection, d: dict) -> str:
    # Prototype : l'envoi réel passera par la boîte dédiée (Gmail API). Ici on
    # marque le brouillon comme envoyé, sans rien transmettre à l'extérieur.
    conn.execute("UPDATE emails SET statut = 'envoye' WHERE id = ? AND sens = 'sortant'",
                 (d["email_id"],))
    return f"E-mail « {d['sujet']} » à {d['destinataire']} marqué envoyé (simulation)."


def _appliquer_lot(conn: sqlite3.Connection, d: dict) -> str:
    lot = conn.execute("SELECT * FROM lots_virements WHERE id = ?", (d["lot_id"],)).fetchone()
    if lot is None:
        raise ValidationErreur("lot de virements introuvable")
    if lot["statut"] == "bloque" or any(a["niveau"] == "bloquant" for a in json.loads(lot["alertes"])):
        raise ValidationErreur("lot bloqué : alertes à lever avant validation")
    conn.execute("UPDATE lots_virements SET statut = 'valide' WHERE id = ?", (lot["id"],))
    # Chaque virement devient une écriture de débit attendue, que le
    # rapprochement lettrera quand le débit apparaîtra sur le relevé.
    for v in conn.execute("SELECT * FROM virements WHERE lot_id = ?", (lot["id"],)).fetchall():
        conn.execute(
            "INSERT INTO ecritures (compte_id, date, libelle, montant, nature) VALUES (?, ?, ?, ?, ?)",
            (lot["compte_id"], lot["cree_le"][:10], f"{v['motif']} - {v['beneficiaire']}",
             -v["montant"], "reversement" if v["origine"].startswith("reversement") else "facture"),
        )
        if v["origine"].startswith("facture:"):
            conn.execute("UPDATE factures SET statut = 'payee' WHERE id = ?", (int(v["origine"][8:]),))
    return (f"Lot n°{lot['id']} validé ({lot['nb']} virements, {lot['total']:.2f} €). "
            "Déposez le fichier SEPA dans votre banque et signez-le : rien n'est transmis automatiquement.")
