"""Pointage des règlements : relier chaque encaissement à l'appel de loyer.

Le moteur ne pointe rien lui-même : il crée des propositions dans la file
de validation. Les encaissements qu'il ne sait pas attribuer avec certitude
sont laissés à l'agent Claude ou au gestionnaire.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata

from .validation import creer_proposition


def _normaliser(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9 ]", " ", s)


def appels_ouverts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, l.nom, l.reference, round(a.loyer + a.charges - a.regle, 2) AS reste "
        "FROM appels a JOIN locataires l ON l.id = a.locataire_id "
        "WHERE round(a.loyer + a.charges - a.regle, 2) > 0 ORDER BY a.periode, l.nom"
    ).fetchall()


def _score(libelle: str, montant: float, appel: sqlite3.Row) -> tuple[int, list[str]]:
    lib = _normaliser(libelle)
    raisons, score = [], 0
    if appel["reference"] and _normaliser(appel["reference"]).replace(" ", "") in lib.replace(" ", ""):
        score += 50
        raisons.append(f"référence {appel['reference']}")
    mots = [m for m in _normaliser(appel["nom"]).split() if len(m) > 2]
    if mots and all(m in lib.split() for m in mots):
        score += 30
        raisons.append("nom du locataire")
    elif any(m in lib.split() for m in mots):
        score += 15
        raisons.append("nom partiel")
    if abs(montant - appel["reste"]) < 0.01:
        score += 30
        raisons.append("montant exact")
    elif montant < appel["reste"]:
        score += 5
        raisons.append("paiement partiel")
    return score, raisons


def proposer_pointages(conn: sqlite3.Connection, origine: str = "moteur de pointage") -> dict:
    """Parcourt les crédits non pointés et propose un rapprochement pour chacun."""
    ouverts = appels_ouverts(conn)
    credits = conn.execute(
        "SELECT * FROM mouvements WHERE statut = 'a_pointer' AND montant > 0 ORDER BY date"
    ).fetchall()
    bilan = {"proposes": 0, "a_examiner": []}
    deja_pris: set[int] = set()
    for mvt in credits:
        notes = sorted(
            ((*_score(mvt["libelle"], mvt["montant"], a), a) for a in ouverts if a["id"] not in deja_pris),
            key=lambda t: t[0], reverse=True,
        )
        if not notes or notes[0][0] < 45:
            bilan["a_examiner"].append(dict(mvt))
            continue
        score, raisons, appel = notes[0]
        ex_aequo = len(notes) > 1 and notes[1][0] == score
        if ex_aequo:
            bilan["a_examiner"].append(dict(mvt))
            continue
        montant = min(mvt["montant"], appel["reste"])
        confiance = "haute" if score >= 80 else "moyenne"
        resume = (f"Pointer {mvt['montant']:.2f} € du {mvt['date']} sur le loyer {appel['periode']} "
                  f"de {appel['nom']} ({', '.join(raisons)})")
        if mvt["montant"] > appel["reste"] + 0.01:
            resume += f". Trop-perçu de {mvt['montant'] - appel['reste']:.2f} € à traiter"
        elif montant < appel["reste"] - 0.01:
            resume += f". Reste dû après pointage : {appel['reste'] - montant:.2f} €"
        creer_proposition(conn, "pointage", resume,
                          {"mouvement_id": mvt["id"], "appel_id": appel["id"], "montant": round(montant, 2)},
                          origine, confiance)
        conn.execute("UPDATE mouvements SET statut = 'propose' WHERE id = ?", (mvt["id"],))
        if abs(montant - appel["reste"]) < 0.01:
            deja_pris.add(appel["id"])
        bilan["proposes"] += 1
    conn.commit()
    return bilan


def etat_impayes(conn: sqlite3.Connection, periode: str | None = None) -> list[dict]:
    lignes = appels_ouverts(conn)
    return [
        {"locataire": a["nom"], "reference": a["reference"], "periode": a["periode"],
         "du": round(a["loyer"] + a["charges"], 2), "regle": a["regle"], "reste": a["reste"]}
        for a in lignes if periode is None or a["periode"] == periode
    ]
