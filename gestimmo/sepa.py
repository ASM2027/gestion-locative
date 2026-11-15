"""Préparation des règlements sortants au format SEPA (pain.001.001.03).

Ce fichier est accepté par toutes les banques de la zone SEPA : on le dépose
dans l'espace professionnel de la banque, où une personne habilitée le signe.
Le logiciel ne transmet jamais rien à la banque lui-même.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .db import journaliser, maintenant, parametre
from .validation import creer_proposition


def iban_valide(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if len(s) < 15 or len(s) > 34 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    chiffres = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(chiffres) % 97 == 1


def _virements_a_preparer(conn: sqlite3.Connection, periode: str) -> list[dict]:
    lignes = []
    for p in conn.execute("SELECT * FROM proprietaires ORDER BY nom").fetchall():
        encaisse = conn.execute(
            "SELECT round(coalesce(sum(e.montant), 0), 2) FROM ecritures e "
            "JOIN appels a ON a.id = e.appel_id JOIN locataires l ON l.id = a.locataire_id "
            "JOIN lots o ON o.id = l.lot_id WHERE e.nature = 'encaissement' AND a.periode = ? "
            "AND o.proprietaire_id = ?", (periode, p["id"]),
        ).fetchone()[0]
        deja = conn.execute(
            "SELECT count(*) FROM virements v JOIN lots_virements lv ON lv.id = v.lot_id "
            "WHERE v.origine = ? AND lv.statut != 'bloque'", (f"reversement:{p['id']}:{periode}",),
        ).fetchone()[0]
        if encaisse > 0 and not deja:
            net = round(encaisse * (1 - p["taux_honoraires"]), 2)
            lignes.append({"beneficiaire": p["nom"], "iban": p["iban"] or "", "bic": p["bic"],
                           "montant": net, "motif": f"Reversement loyers {periode}",
                           "origine": f"reversement:{p['id']}:{periode}", "iban_verifie": True})
    for f in conn.execute(
        "SELECT fa.*, fo.nom, fo.iban, fo.bic, fo.iban_verifie FROM factures fa "
        "JOIN fournisseurs fo ON fo.id = fa.fournisseur_id WHERE fa.statut = 'a_payer'"
    ).fetchall():
        lignes.append({"beneficiaire": f["nom"], "iban": f["iban"] or "", "bic": f["bic"],
                       "montant": f["montant"], "motif": f"Facture {f['objet']}"[:140],
                       "origine": f"facture:{f['id']}", "iban_verifie": bool(f["iban_verifie"])})
    return lignes


def preparer_lot(conn: sqlite3.Connection, compte_id: int, periode: str, dossier: Path,
                 origine: str = "préparation des règlements") -> dict | None:
    lignes = _virements_a_preparer(conn, periode)
    if not lignes:
        return None
    plafond = float(parametre(conn, "plafond_virement", "5000"))
    alertes = []
    for v in lignes:
        if not iban_valide(v["iban"]):
            alertes.append({"niveau": "bloquant", "message": f"IBAN invalide pour {v['beneficiaire']}"})
        elif not v["iban_verifie"]:
            alertes.append({"niveau": "bloquant", "message":
                            f"IBAN de {v['beneficiaire']} nouveau ou modifié : vérifier par téléphone "
                            "avant tout paiement (risque de fraude au changement d'IBAN)"})
        if v["montant"] > plafond:
            alertes.append({"niveau": "avertissement", "message":
                            f"{v['beneficiaire']} : {v['montant']:.2f} € dépasse le plafond de "
                            f"{plafond:.0f} €, une deuxième personne doit signer"})
    total = round(sum(v["montant"] for v in lignes), 2)
    bloque = any(a["niveau"] == "bloquant" for a in alertes)
    cur = conn.execute(
        "INSERT INTO lots_virements (cree_le, compte_id, statut, total, nb, alertes) VALUES (?, ?, ?, ?, ?, ?)",
        (maintenant(), compte_id, "bloque" if bloque else "prepare", total, len(lignes),
         json.dumps(alertes, ensure_ascii=False)),
    )
    lot_id = cur.lastrowid
    for v in lignes:
        conn.execute(
            "INSERT INTO virements (lot_id, beneficiaire, iban, bic, montant, motif, origine) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lot_id, v["beneficiaire"], v["iban"], v["bic"], v["montant"], v["motif"], v["origine"]),
        )
    fichier = None
    if not bloque:
        compte = conn.execute("SELECT * FROM comptes_bancaires WHERE id = ?", (compte_id,)).fetchone()
        dossier.mkdir(parents=True, exist_ok=True)
        fichier = dossier / f"virements_lot_{lot_id}.xml"
        fichier.write_text(pain001(lot_id, compte, lignes, parametre(conn, "raison_sociale", "Agence")),
                           encoding="utf-8")
        conn.execute("UPDATE lots_virements SET fichier = ? WHERE id = ?", (str(fichier), lot_id))
        for v in lignes:
            if v["origine"].startswith("facture:"):
                conn.execute("UPDATE factures SET statut = 'dans_un_lot' WHERE id = ?", (int(v["origine"][8:]),))
        creer_proposition(conn, "lot_virements",
                          f"Lot n°{lot_id} : {len(lignes)} virements, {total:.2f} € depuis {compte['nom']}",
                          {"lot_id": lot_id}, origine, "haute")
    else:
        creer_proposition(conn, "alerte", f"Lot n°{lot_id} bloqué : " +
                          " ; ".join(a["message"] for a in alertes if a["niveau"] == "bloquant"),
                          {"lot_id": lot_id}, origine, "haute")
    journaliser(conn, origine, "preparation_lot", {"lot": lot_id, "total": total, "alertes": alertes})
    conn.commit()
    return {"lot_id": lot_id, "total": total, "nb": len(lignes), "alertes": alertes,
            "fichier": str(fichier) if fichier else None}


def pain001(lot_id: int, compte: sqlite3.Row, lignes: list[dict], raison_sociale: str) -> str:
    total = sum(v["montant"] for v in lignes)
    msg_id = f"GESTIMMO-{lot_id}-{datetime.now():%Y%m%d%H%M%S}"
    tx = []
    for i, v in enumerate(lignes, 1):
        bic = f"<CdtrAgt><FinInstnId><BIC>{escape(v['bic'])}</BIC></FinInstnId></CdtrAgt>" if v.get("bic") else ""
        tx.append(
            f"<CdtTrfTxInf><PmtId><EndToEndId>LOT{lot_id}-{i}</EndToEndId></PmtId>"
            f"<Amt><InstdAmt Ccy=\"EUR\">{v['montant']:.2f}</InstdAmt></Amt>{bic}"
            f"<Cdtr><Nm>{escape(v['beneficiaire'][:70])}</Nm></Cdtr>"
            f"<CdtrAcct><Id><IBAN>{v['iban'].replace(' ', '')}</IBAN></Id></CdtrAcct>"
            f"<RmtInf><Ustrd>{escape(v['motif'][:140])}</Ustrd></RmtInf></CdtTrfTxInf>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"><CstmrCdtTrfInitn>'
        f"<GrpHdr><MsgId>{msg_id}</MsgId><CreDtTm>{datetime.now():%Y-%m-%dT%H:%M:%S}</CreDtTm>"
        f"<NbOfTxs>{len(lignes)}</NbOfTxs><CtrlSum>{total:.2f}</CtrlSum>"
        f"<InitgPty><Nm>{escape(raison_sociale)}</Nm></InitgPty></GrpHdr>"
        f"<PmtInf><PmtInfId>LOT{lot_id}</PmtInfId><PmtMtd>TRF</PmtMtd>"
        f"<NbOfTxs>{len(lignes)}</NbOfTxs><CtrlSum>{total:.2f}</CtrlSum>"
        "<PmtTpInf><SvcLvl><Cd>SEPA</Cd></SvcLvl></PmtTpInf>"
        f"<ReqdExctnDt>{date.today().isoformat()}</ReqdExctnDt>"
        f"<Dbtr><Nm>{escape(raison_sociale)}</Nm></Dbtr>"
        f"<DbtrAcct><Id><IBAN>{compte['iban'].replace(' ', '')}</IBAN></Id></DbtrAcct>"
        f"<DbtrAgt><FinInstnId><BIC>{compte['bic']}</BIC></FinInstnId></DbtrAgt>"
        "<ChrgBr>SLEV</ChrgBr>" + "".join(tx) + "</PmtInf></CstmrCdtTrfInitn></Document>\n"
    )


def confirmer_iban(conn: sqlite3.Connection, fournisseur_id: int, par: str) -> None:
    """À appeler après avoir vérifié l'IBAN par téléphone auprès du fournisseur."""
    conn.execute("UPDATE fournisseurs SET iban_verifie = 1 WHERE id = ?", (fournisseur_id,))
    journaliser(conn, par, "iban_confirme", {"fournisseur": fournisseur_id})
    conn.commit()
