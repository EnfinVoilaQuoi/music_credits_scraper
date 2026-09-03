---
name: "source-command-bilan"
description: "Fait le bilan de la session et met à jour JOURNAL.md (décisions, échecs, pièges)"
---

# source-command-bilan

Use this skill when the user asks to run the migrated source command `bilan`.

## Command Template

Tu vas faire le bilan de la session de travail en cours sur le projet Music Credits Scraper.

1. Relis ce qui s'est passé dans cette session.
2. Identifie UNIQUEMENT ce qui mérite d'être gardé en mémoire long terme :
   - une décision technique prise,
   - une approche **testée qui a échoué** (et pourquoi),
   - un **piège à ne pas reproduire**,
   - une API/source abandonnée ou ajoutée.
   Ignore le bruit (questions de syntaxe, essais sans conséquence, etc.).
3. Ouvre `JOURNAL.md`. Avant d'écrire, vérifie que l'info n'y est PAS déjà (pas de doublon).
4. Ajoute une entrée datée d'aujourd'hui dans la section « À ne pas refaire / pièges » ou « Décisions techniques » selon le cas, en respectant ce format :

```
### AAAA-MM-JJ — <titre court>
- Contexte : <ce qu'on essayait de faire>
- Approche testée : <ce qu'on a fait>
- Résultat : <OK / KO / partiel>
- À retenir / à ne pas refaire : <la leçon en une ligne>
```

5. Si une règle **durable** en ressort (du genre « ne jamais utiliser X »), ajoute-la aussi en une ligne dans le résumé « Pièges connus » de `AGENTS.md`.
6. Si rien de notable n'est sorti de la session, ne modifie aucun fichier et dis-le simplement.

Sois concis : une à trois entrées maximum, une ligne par champ. Pas de réécriture des entrées existantes.
