"""Interface web du logiciel (FastAPI). Lancer : uvicorn gestimmo.app:app --reload"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from jinja2 import DictLoader, Environment, select_autoescape

from . import agent, banque, db, demo, pointage, rapprochement, sepa, validation
from .boite import BoiteDossier, recevoir

RACINE = Path(os.environ.get("GESTIMMO_DONNEES", Path(__file__).resolve().parent.parent / "donnees"))
RACINE.mkdir(parents=True, exist_ok=True)
conn = db.connecter(RACINE / "gestimmo.db")
if os.environ.get("GESTIMMO_DEMO", "1") == "1":
    demo.charger(conn)
    if not (RACINE / "demo" / "boite").exists():
        demo.ecrire_fichiers(RACINE / "demo")
UTILISATEUR = os.environ.get("GESTIMMO_UTILISATEUR", demo.GESTIONNAIRE)

BASE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Gestion locative</title>
<style>
:root{--fond:#f6f5f2;--carte:#fff;--texte:#1d1d1b;--doux:#6b6a65;--bord:#dedcd5;--accent:#2457c5;--ok:#1f7a4d;--alerte:#b4500f;--danger:#b42318}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--fond);color:var(--texte)}
header{background:var(--texte);color:#fff;padding:12px 24px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
header a{color:#ddd;text-decoration:none}header a:hover{color:#fff}header strong{margin-right:12px}
main{max-width:1100px;margin:24px auto;padding:0 16px}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:8px;padding:16px 20px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 16px}h2{font-size:17px;margin:0 0 12px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--bord);vertical-align:top}
th{font-size:13px;color:var(--doux);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:#eee}
.haute{background:#dcefe4;color:var(--ok)}.moyenne{background:#fdf0dc;color:var(--alerte)}.basse,.alerte,.bloque{background:#fbe1df;color:var(--danger)}
button,.bouton{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:7px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.second{background:#fff;color:var(--texte);border:1px solid var(--bord)}
form.ligne{display:inline}input,select{font:inherit;padding:6px;border:1px solid var(--bord);border-radius:6px}
.grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}
.chiffre{font-size:28px;font-weight:600}.doux{color:var(--doux)}.msg{background:#e8effd;border-left:4px solid var(--accent);padding:10px 14px;margin-bottom:16px;border-radius:4px}
pre{white-space:pre-wrap;font:inherit;margin:0}
</style></head><body>
<header><strong>Gestion locative</strong>
<a href="/">Accueil</a><a href="/validations">Validations ({{ nb_attente }})</a><a href="/pointage">Pointage</a>
<a href="/rapprochement">Rapprochement</a><a href="/sinistres">Sinistres</a><a href="/virements">Virements</a>
<a href="/boite">Boîte dédiée</a><a href="/journal">Journal</a></header>
<main>{% if msg %}<div class="msg">{{ msg }}</div>{% endif %}{% block corps %}{% endblock %}</main></body></html>"""

PAGES = {
"accueil": """{% extends "base" %}{% block corps %}
<h1>Tableau de bord</h1>
<div class="grille">
<div class="carte"><div class="doux">À valider</div><div class="chiffre">{{ nb_attente }}</div></div>
<div class="carte"><div class="doux">Encaissements à pointer</div><div class="chiffre">{{ a_pointer }}</div></div>
<div class="carte"><div class="doux">Loyers non soldés</div><div class="chiffre">{{ "%.2f"|format(impayes) }} €</div></div>
<div class="carte"><div class="doux">Sinistres ouverts</div><div class="chiffre">{{ sinistres }}</div></div>
</div>
<div class="carte"><h2>Parler à Claude</h2>
<p class="doux">Dictez un ordre (« pointe les loyers de novembre », « prépare les reversements de novembre »).
Il devient un e-mail d'ordre sur la boîte dédiée, comme si vous l'aviez écrit. Agent : <b>{{ mode }}</b>.</p>
<form method="post" action="/voix" id="fvoix"><input name="texte" id="texte" style="width:70%" placeholder="Votre ordre…">
<button type="button" class="second" id="micro">🎤 Parler</button> <button>Envoyer</button></form>
<script>
const R = window.SpeechRecognition || window.webkitSpeechRecognition, b = document.getElementById('micro');
if (!R) { b.disabled = true; b.title = 'Dictée non disponible dans ce navigateur'; }
else b.onclick = () => { const r = new R(); r.lang = 'fr-FR'; b.textContent = '… écoute';
  r.onresult = e => { document.getElementById('texte').value = e.results[0][0].transcript; };
  r.onend = () => { b.textContent = '🎤 Parler'; }; r.start(); };
</script></div>
<div class="carte"><h2>Boîte dédiée</h2>
<form class="ligne" method="post" action="/boite/relever"><button class="second">Relever la boîte (démo)</button></form>
<form class="ligne" method="post" action="/boite/traiter"><button>Traiter les e-mails ({{ nouveaux }})</button></form></div>
<div class="carte"><h2>Importer un relevé bancaire</h2>
<p class="doux">CFONB 120, OFX ou CSV, toutes banques. Démo : donnees/demo/releve_lcl_novembre.cfonb</p>
<form method="post" action="/releve" enctype="multipart/form-data">
<select name="compte_id">{% for c in comptes %}<option value="{{ c.id }}">{{ c.nom }}</option>{% endfor %}</select>
<input type="file" name="fichier" required> <button>Importer</button></form>
<form method="post" action="/releve/demo" style="margin-top:8px"><button class="second">Importer le relevé de démo</button></form></div>
{% endblock %}""",

"validations": """{% extends "base" %}{% block corps %}
<h1>À valider</h1>
<div class="carte">{% if not attente %}<p class="doux">Rien à valider.</p>{% else %}
<form method="post" action="/validations/haute"><button class="second">Valider tous les pointages en confiance haute</button></form>
<table><tr><th>N°</th><th>Type</th><th>Proposition</th><th>Confiance</th><th>Origine</th><th></th></tr>
{% for p in attente %}<tr><td>{{ p.id }}</td><td>{{ p.type }}</td><td>{{ p.resume }}{% if p.type == 'email' %}<details><summary class="doux">voir le texte</summary><pre>{{ corps_email(p) }}</pre></details>{% endif %}</td>
<td><span class="badge {{ p.confiance }}">{{ p.confiance }}</span></td><td class="doux">{{ p.origine }}</td>
<td style="white-space:nowrap"><form class="ligne" method="post" action="/validations/{{ p.id }}/valider"><button>Valider</button></form>
<form class="ligne" method="post" action="/validations/{{ p.id }}/refuser"><button class="second">Refuser</button></form></td></tr>{% endfor %}
</table>{% endif %}</div>
<div class="carte"><h2>Décisions récentes</h2><table><tr><th>N°</th><th>Proposition</th><th>Décision</th><th>Par</th></tr>
{% for p in recentes %}<tr><td>{{ p.id }}</td><td>{{ p.resume }}</td><td>{{ p.statut }}</td><td class="doux">{{ p.decide_par }}</td></tr>{% endfor %}</table></div>
{% endblock %}""",

"pointage": """{% extends "base" %}{% block corps %}
<h1>Pointage des règlements</h1>
<form method="post" action="/pointage/lancer"><button>Proposer les pointages</button></form><br>
<div class="carte"><h2>Loyers non soldés</h2><table><tr><th>Locataire</th><th>Réf.</th><th>Période</th><th class="n">Dû</th><th class="n">Réglé</th><th class="n">Reste</th></tr>
{% for i in impayes %}<tr><td>{{ i.locataire }}</td><td>{{ i.reference }}</td><td>{{ i.periode }}</td><td class="n">{{ "%.2f"|format(i.du) }}</td><td class="n">{{ "%.2f"|format(i.regle) }}</td><td class="n"><b>{{ "%.2f"|format(i.reste) }}</b></td></tr>{% endfor %}</table></div>
<div class="carte"><h2>Encaissements</h2><table><tr><th>Date</th><th>Libellé</th><th class="n">Montant</th><th>Statut</th></tr>
{% for m in mouvements %}<tr><td>{{ m.date }}</td><td>{{ m.libelle }}</td><td class="n">{{ "%.2f"|format(m.montant) }}</td><td><span class="badge">{{ m.statut }}</span></td></tr>{% endfor %}</table></div>
{% endblock %}""",

"rapprochement": """{% extends "base" %}{% block corps %}
<h1>Rapprochement bancaire</h1>
<form method="get"><select name="compte" onchange="this.form.submit()">{% for c in comptes %}<option value="{{ c.id }}" {% if c.id == compte %}selected{% endif %}>{{ c.nom }}</option>{% endfor %}</select></form><br>
<div class="grille"><div class="carte"><div class="doux">Solde banque</div><div class="chiffre">{{ "%.2f"|format(e.solde_banque) }} €</div></div>
<div class="carte"><div class="doux">Solde comptable</div><div class="chiffre">{{ "%.2f"|format(e.solde_comptable) }} €</div></div>
<div class="carte"><div class="doux">Écart</div><div class="chiffre">{{ "%.2f"|format(e.ecart) }} €</div><div class="doux">{{ "entièrement expliqué" if e.rapproche else "NON EXPLIQUÉ" }}</div></div></div>
<form method="post" action="/rapprochement/{{ compte }}/lancer"><button>Proposer les corrections</button></form><br>
<div class="carte"><h2>En banque, pas en comptabilité</h2><table>{% for m in e.en_banque_pas_en_compta %}<tr><td>{{ m.date }}</td><td>{{ m.libelle }}</td><td class="n">{{ "%.2f"|format(m.montant) }}</td><td><span class="badge">{{ m.statut }}</span></td></tr>{% else %}<tr><td class="doux">Aucune ligne</td></tr>{% endfor %}</table></div>
<div class="carte"><h2>En comptabilité, pas encore en banque</h2><table>{% for x in e.en_compta_pas_en_banque %}<tr><td>{{ x.date }}</td><td>{{ x.libelle }}</td><td class="n">{{ "%.2f"|format(x.montant) }}</td></tr>{% else %}<tr><td class="doux">Aucune ligne</td></tr>{% endfor %}</table></div>
{% endblock %}""",

"sinistres": """{% extends "base" %}{% block corps %}
<h1>Sinistres</h1><div class="carte"><table><tr><th>N°</th><th>Lot</th><th>Déclarant</th><th>Nature</th><th>Pièces manquantes</th><th>Statut</th></tr>
{% for s in sinistres %}<tr><td>{{ s.id }}</td><td>{{ s.lot or "?" }}</td><td>{{ s.declarant }}</td><td>{{ s.nature }}<div class="doux">{{ s.description[:160] }}</div></td>
<td>{{ s.pieces|join(", ") }}</td><td><span class="badge">{{ s.statut }}</span></td></tr>{% else %}<tr><td class="doux">Aucun dossier.</td></tr>{% endfor %}</table></div>
{% endblock %}""",

"virements": """{% extends "base" %}{% block corps %}
<h1>Règlements sortants</h1>
<div class="carte"><p class="doux">Le logiciel prépare un fichier SEPA (pain.001) accepté par toutes les banques.
Vous le déposez et le signez dans votre espace bancaire : rien n'est transmis automatiquement.</p>
<form method="post" action="/virements/preparer">Période <input name="periode" value="{{ periode }}" size="8">
<select name="compte_id">{% for c in comptes %}<option value="{{ c.id }}">{{ c.nom }}</option>{% endfor %}</select>
<button>Préparer le lot</button></form></div>
{% for l in lots %}<div class="carte"><h2>Lot n°{{ l.id }} · {{ "%.2f"|format(l.total) }} € · <span class="badge {{ l.statut }}">{{ l.statut }}</span></h2>
{% for a in l.alertes %}<div class="msg" style="border-color:{{ 'var(--danger)' if a.niveau == 'bloquant' else 'var(--alerte)' }}">{{ a.message }}</div>{% endfor %}
<table><tr><th>Bénéficiaire</th><th>IBAN</th><th>Motif</th><th class="n">Montant</th></tr>
{% for v in l.virements %}<tr><td>{{ v.beneficiaire }}</td><td>{{ v.iban }}</td><td>{{ v.motif }}</td><td class="n">{{ "%.2f"|format(v.montant) }}</td></tr>{% endfor %}</table>
{% if l.fichier %}<p><a class="bouton" href="/virements/{{ l.id }}/fichier">Télécharger le fichier SEPA</a></p>{% endif %}</div>{% endfor %}
<div class="carte"><h2>IBAN fournisseurs</h2><table>{% for f in fournisseurs %}<tr><td>{{ f.nom }}</td><td>{{ f.iban }}</td>
<td>{% if f.iban_verifie %}<span class="badge haute">vérifié</span>{% else %}<form class="ligne" method="post" action="/fournisseurs/{{ f.id }}/confirmer"><button class="second">Confirmé par téléphone</button></form>{% endif %}</td></tr>{% endfor %}</table></div>
{% endblock %}""",

"boite": """{% extends "base" %}{% block corps %}
<h1>Boîte dédiée</h1><div class="carte"><table><tr><th>Sens</th><th>De / À</th><th>Sujet</th><th>Résultat</th></tr>
{% for e in emails %}<tr><td>{{ "⬇" if e.sens == "entrant" else "⬆" }} {{ e.statut }}</td>
<td>{{ e.expediteur if e.sens == "entrant" else e.destinataire }}{% if e.sens == "entrant" %}<div class="doux">{{ "gestionnaire autorisé" if e.autorise else "tiers" }}</div>{% endif %}</td>
<td><details><summary>{{ e.sujet }}</summary><pre>{{ e.corps }}</pre></details></td>
<td>{% if e.categorie %}<span class="badge {{ e.categorie }}">{{ e.categorie }}</span> {% endif %}{{ e.compte_rendu or "" }}</td></tr>{% endfor %}</table></div>
{% endblock %}""",

"journal": """{% extends "base" %}{% block corps %}
<h1>Journal</h1><div class="carte"><table><tr><th>Quand</th><th>Qui</th><th>Action</th><th>Détail</th></tr>
{% for j in journal %}<tr><td class="doux">{{ j.quand }}</td><td>{{ j.acteur }}</td><td>{{ j.action }}</td><td><small>{{ j.detail }}</small></td></tr>{% endfor %}</table></div>
{% endblock %}""",
}

env = Environment(loader=DictLoader({"base": BASE, **PAGES}), autoescape=select_autoescape(default=True))
app = FastAPI(title="Gestion locative avec Claude")


def rendre(page: str, msg: str | None = None, **ctx) -> HTMLResponse:
    nb = conn.execute("SELECT count(*) FROM propositions WHERE statut = 'en_attente'").fetchone()[0]
    return HTMLResponse(env.get_template(page).render(nb_attente=nb, msg=msg, **ctx))


def retour(url: str, msg: str) -> RedirectResponse:
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}msg={quote(msg)}", status_code=303)


def comptes():
    return conn.execute("SELECT * FROM comptes_bancaires ORDER BY id").fetchall()


@app.get("/", response_class=HTMLResponse)
def accueil(msg: str | None = None):
    imp = sum(i["reste"] for i in pointage.etat_impayes(conn))
    return rendre("accueil", msg, mode="Claude (API)" if agent.mode() == "claude" else "simulé (sans clé d'API)",
                  a_pointer=conn.execute("SELECT count(*) FROM mouvements WHERE statut='a_pointer' AND montant>0").fetchone()[0],
                  impayes=imp, sinistres=conn.execute("SELECT count(*) FROM sinistres WHERE statut!='clos'").fetchone()[0],
                  nouveaux=conn.execute("SELECT count(*) FROM emails WHERE sens='entrant' AND statut='nouveau'").fetchone()[0],
                  comptes=comptes())


@app.post("/voix")
def voix(texte: str = Form(...)):
    recevoir(conn, UTILISATEUR, "Ordre vocal", texte)
    res = agent.traiter_boite(conn, RACINE / "sorties")
    return retour("/", " | ".join(r["compte_rendu"] for r in res) or "Rien à traiter.")


@app.post("/boite/relever")
def relever():
    n = BoiteDossier(RACINE / "demo" / "boite").relever(conn)
    return retour("/", f"{n} nouvel(s) e-mail(s) relevé(s).")


@app.post("/boite/traiter")
def traiter():
    res = agent.traiter_boite(conn, RACINE / "sorties")
    return retour("/boite", f"{len(res)} e-mail(s) traité(s). Les actions attendent dans Validations.")


@app.post("/releve")
async def importer_releve(compte_id: int = Form(...), fichier: UploadFile = File(...)):
    brut = await fichier.read()
    try:
        texte = brut.decode("utf-8")
    except UnicodeDecodeError:
        texte = brut.decode("latin-1")
    try:
        r = banque.importer(conn, compte_id, fichier.filename or "releve", texte, UTILISATEUR)
    except ValueError as exc:
        return retour("/", f"Import impossible : {exc}")
    return retour("/pointage", f"Relevé {r['format']} : {r['lus']} lignes lues, {r['nouveaux']} nouvelles.")


@app.post("/releve/demo")
def releve_demo():
    r = banque.importer(conn, 1, "releve_lcl_novembre.cfonb", demo.releve_cfonb(), UTILISATEUR)
    return retour("/pointage", f"Relevé {r['format']} : {r['lus']} lignes lues, {r['nouveaux']} nouvelles.")


@app.get("/validations", response_class=HTMLResponse)
def validations(msg: str | None = None):
    def corps_email(p):
        d = json.loads(p["donnees"])
        row = conn.execute("SELECT corps FROM emails WHERE id = ?", (d["email_id"],)).fetchone()
        return row["corps"] if row else ""
    return rendre("validations", msg, attente=validation.en_attente(conn), corps_email=corps_email,
                  recentes=conn.execute("SELECT * FROM propositions WHERE statut != 'en_attente' "
                                        "ORDER BY decide_le DESC LIMIT 20").fetchall())


@app.post("/validations/haute")
def valider_haute():
    n = 0
    for p in validation.en_attente(conn, "pointage"):
        if p["confiance"] == "haute":
            validation.valider(conn, p["id"], UTILISATEUR)
            n += 1
    return retour("/validations", f"{n} pointage(s) validé(s).")


@app.post("/validations/{pid}/{decision}")
def decider(pid: int, decision: str):
    try:
        if decision == "valider":
            msg = validation.valider(conn, pid, UTILISATEUR)
        elif decision == "refuser":
            validation.refuser(conn, pid, UTILISATEUR)
            msg = f"Proposition {pid} refusée."
        else:
            raise HTTPException(404)
    except validation.ValidationErreur as exc:
        msg = f"Impossible : {exc}"
    return retour("/validations", msg)


@app.get("/pointage", response_class=HTMLResponse)
def page_pointage(msg: str | None = None):
    return rendre("pointage", msg, impayes=pointage.etat_impayes(conn),
                  mouvements=conn.execute("SELECT * FROM mouvements ORDER BY date DESC").fetchall())


@app.post("/pointage/lancer")
def lancer_pointage():
    b = pointage.proposer_pointages(conn, UTILISATEUR)
    return retour("/validations", f"{b['proposes']} pointage(s) proposé(s), {len(b['a_examiner'])} encaissement(s) non identifié(s).")


@app.get("/rapprochement", response_class=HTMLResponse)
def page_rapprochement(compte: int = 1, msg: str | None = None):
    return rendre("rapprochement", msg, comptes=comptes(), compte=compte, e=rapprochement.etat(conn, compte))


@app.post("/rapprochement/{compte}/lancer")
def lancer_rapprochement(compte: int):
    n = rapprochement.proposer_corrections(conn, compte, UTILISATEUR)
    return retour(f"/rapprochement?compte={compte}", f"{n} correction(s) proposée(s), à valider.")


@app.get("/sinistres", response_class=HTMLResponse)
def page_sinistres(msg: str | None = None):
    rows = conn.execute("SELECT s.*, o.immeuble || ' - ' || o.designation AS lot FROM sinistres s "
                        "LEFT JOIN lots o ON o.id = s.lot_id ORDER BY s.id DESC").fetchall()
    return rendre("sinistres", msg, sinistres=[{**dict(r), "pieces": json.loads(r["pieces_manquantes"])} for r in rows])


@app.get("/virements", response_class=HTMLResponse)
def page_virements(msg: str | None = None):
    lots = []
    for l in conn.execute("SELECT * FROM lots_virements ORDER BY id DESC").fetchall():
        lots.append({**dict(l), "alertes": json.loads(l["alertes"]),
                     "virements": conn.execute("SELECT * FROM virements WHERE lot_id = ?", (l["id"],)).fetchall()})
    return rendre("virements", msg, lots=lots, comptes=comptes(), periode=demo.PERIODE,
                  fournisseurs=conn.execute("SELECT * FROM fournisseurs").fetchall())


@app.post("/virements/preparer")
def preparer(periode: str = Form(...), compte_id: int = Form(...)):
    r = sepa.preparer_lot(conn, compte_id, periode, RACINE / "sorties", UTILISATEUR)
    if r is None:
        return retour("/virements", "Aucun règlement à préparer.")
    return retour("/virements", f"Lot n°{r['lot_id']} préparé : {r['nb']} virements, {r['total']:.2f} €.")


@app.get("/virements/{lot_id}/fichier")
def fichier_lot(lot_id: int):
    l = conn.execute("SELECT fichier FROM lots_virements WHERE id = ?", (lot_id,)).fetchone()
    if not l or not l["fichier"]:
        raise HTTPException(404)
    return FileResponse(l["fichier"], media_type="application/xml", filename=Path(l["fichier"]).name)


@app.post("/fournisseurs/{fid}/confirmer")
def confirmer(fid: int):
    sepa.confirmer_iban(conn, fid, UTILISATEUR)
    return retour("/virements", "IBAN confirmé. Vous pouvez préparer un nouveau lot.")


@app.get("/boite", response_class=HTMLResponse)
def page_boite(msg: str | None = None):
    return rendre("boite", msg, emails=conn.execute("SELECT * FROM emails ORDER BY id DESC").fetchall())


@app.get("/journal", response_class=HTMLResponse)
def page_journal(msg: str | None = None):
    return rendre("journal", msg, journal=conn.execute("SELECT * FROM journal ORDER BY id DESC LIMIT 200").fetchall())
