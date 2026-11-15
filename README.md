# Gestion locative avec Claude : prototype

Logiciel de gérance locative construit de zéro, avec un agent Claude branché sur une
boîte e-mail dédiée. Il tourne sur des données fictives : il n'envoie aucun e-mail et
ne paie rien.

## Lancer

```bash
pip install -r requirements.txt
uvicorn gestimmo.app:app --reload
# puis ouvrir http://localhost:8000
```

Parcours de démonstration, depuis l'accueil :
1. **Importer le relevé de démo** : relevé LCL au format CFONB 120.
2. **Relever la boîte** puis **Traiter les e-mails** : 4 e-mails fictifs (un sinistre, une
   demande de changement d'IBAN, un ordre du gestionnaire, un e-mail piège).
3. **Validations** : tout ce que Claude a préparé attend ici. Valider ou refuser.
4. **Rapprochement** : soldes banque et comptabilité, écarts listés.
5. **Virements** : le lot est bloqué tant que l'IBAN du fournisseur n'est pas confirmé par
   téléphone, puis le fichier SEPA se télécharge pour être signé dans la banque.
6. **Parler à Claude** : bouton micro (Chrome ou Edge), l'ordre dicté devient un e-mail
   d'ordre sur la boîte dédiée.

Sans clé d'API, l'agent tourne en **mode simulé** (règles). Avec `ANTHROPIC_API_KEY`
défini, il utilise Claude (`claude-opus-5-5`, repli automatique activé si le modèle
refuse une demande).

Tests : `python -m pytest -q`

## Ce qui est en place

| Module | Rôle |
| --- | --- |
| `banque.py` | Import CFONB 120, OFX et CSV : fonctionne avec toutes les banques. Sans doublon au réimport. Point d'entrée prévu pour un agrégateur (lecture seule). |
| `pointage.py` | Rapproche chaque encaissement de l'appel de loyer (référence, nom, montant), gère les paiements partiels et isole les non identifiés. |
| `rapprochement.py` | État de rapprochement, frais bancaires, lettrage des débits attendus. |
| `sepa.py` | Lot de virements (reversements propriétaires nets d'honoraires, factures) au format pain.001.001.03. Blocage si IBAN invalide ou non vérifié, alerte au-delà du plafond. |
| `boite.py` | Boîte dédiée : liste blanche des expéditeurs autorisés à donner des ordres, brouillons soumis à validation. |
| `agent.py` | Agent Claude. Un tiers n'a accès qu'aux outils d'analyse (sinistre, réponse, alerte) ; seuls les gestionnaires peuvent faire pointer, rapprocher ou préparer des paiements. |
| `validation.py` | File de validation : rien ne s'applique sans un humain. |
| `app.py` | Interface web. |

## Pas encore branché

- Compte Google Workspace dédié (`BoiteGmail`) : relève et envoi réels.
- Agrégateur bancaire (`Agregateur`) : relevés automatiques de toutes les banques.
- Comptes utilisateurs et droits (aujourd'hui un seul gestionnaire de démonstration).
- Comptabilité mandant complète (CRG, quittances, régularisation de charges, dépôts de garantie).
