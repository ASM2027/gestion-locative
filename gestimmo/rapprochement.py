"""Rapprochement bancaire : comparer le relevé et la comptabilité, expliquer les écarts."""
from __future__ import annotations

import re
import sqlite3

from .validation import creer_proposition

_FRAIS = re.compile(r"\b(FRAIS|COMMISSION|COTISATION|COM\.|ABONNEMENT|AGIOS)\b", re.I)


def etat(conn: sqlite3.Connection, compte_id: int) -> dict:
    solde_banque = conn.execute(
        "SELECT round(coalesce(sum(montant), 0), 2) FROM mouvements WHERE compte_id = ?", (compte_id,)
    ).fetchone()[0]
    solde_compta = conn.execute(
        "SELECT round(coalesce(sum(montant), 0), 2) FROM ecritures WHERE compte_id = ?", (compte_id,)
    ).fetchone()[0]
    banque_seule = [dict(r) for r in conn.execute(
        "SELECT * FROM mouvements WHERE compte_id = ? AND statut != 'pointe' AND statut != 'ignore' "
        "ORDER BY date", (compte_id,))]
    compta_seule = [dict(r) for r in conn.execute(
        "SELECT * FROM ecritures WHERE compte_id = ? AND mouvement_id IS NULL ORDER BY date", (compte_id,))]
    explique = round(sum(m["montant"] for m in banque_seule) - sum(e["montant"] for e in compta_seule), 2)
    return {
        "solde_banque": solde_banque,
        "solde_comptable": solde_compta,
        "ecart": round(solde_banque - solde_compta, 2),
        "ecart_explique": explique,
        "rapproche": abs((solde_banque - solde_compta) - explique) < 0.01,
        "en_banque_pas_en_compta": banque_seule,
        "en_compta_pas_en_banque": compta_seule,
    }


def proposer_corrections(conn: sqlite3.Connection, compte_id: int,
                         origine: str = "moteur de rapprochement") -> int:
    """Propose les écritures évidentes : frais bancaires et lettrage des débits attendus."""
    n = 0
    attendues = [dict(r) for r in conn.execute(
        "SELECT * FROM ecritures WHERE compte_id = ? AND mouvement_id IS NULL", (compte_id,))]
    for mvt in conn.execute(
        "SELECT * FROM mouvements WHERE compte_id = ? AND statut = 'a_pointer' AND montant < 0 ORDER BY date",
        (compte_id,),
    ).fetchall():
        if _FRAIS.search(mvt["libelle"]):
            creer_proposition(conn, "ecriture", f"Passer les frais bancaires du {mvt['date']} : "
                              f"{mvt['libelle']} ({mvt['montant']:.2f} €)",
                              {"compte_id": compte_id, "mouvement_id": mvt["id"], "date": mvt["date"],
                               "libelle": mvt["libelle"], "montant": mvt["montant"], "nature": "frais"},
                              origine, "haute")
        else:
            cible = next((e for e in attendues if abs(e["montant"] - mvt["montant"]) < 0.01), None)
            if cible is None:
                continue
            attendues.remove(cible)
            creer_proposition(conn, "lettrage", f"Rapprocher le débit du {mvt['date']} ({mvt['montant']:.2f} €, "
                              f"{mvt['libelle']}) de l'écriture « {cible['libelle']} »",
                              {"mouvement_id": mvt["id"], "ecriture_id": cible["id"]}, origine, "haute")
        conn.execute("UPDATE mouvements SET statut = 'propose' WHERE id = ?", (mvt["id"],))
        n += 1
    conn.commit()
    return n
