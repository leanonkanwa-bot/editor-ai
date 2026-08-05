#!/usr/bin/env python3
"""
Seed prim_ascension_reveal phrases into trigger_bank_v3.json.
Safe to run while Wave 2 is live: reads current bank, merges, writes once.
"""
import json, sys
from pathlib import Path

BANK_PATH = Path(__file__).parent / "trigger_bank_v3.json"

PAR_PHRASES = {
    "tier1": [
        # 1-4: surface wording echoes PCR / key_phrase but contains concrete figure
        "j'ai accompagné 47 entrepreneurs vers leur premier 10k€/mois",
        "87 clients ont atteint leur objectif — voilà ce que ça donne vraiment",
        "depuis que j'ai lancé cette méthode, 312 personnes ont transformé leur situation",
        "le bilan après 3 ans : 200 clients, 94% de taux de réussite",
        # 5-7: counter-intuitive framing but still PAR
        "contrairement à ce qu'on croit, ce n'est pas une conviction abstraite : j'ai accompagné 63 personnes",
        "ce n'est pas qu'un chiffre — c'est 128 entrepreneurs qui ont validé ce modèle",
        "ça peut sembler anecdotique, mais : 47 résultats concrets en 18 mois",
        # 8-10: downplayed framing
        "c'est peut-être pas impressionnant, mais j'ai généré 85k€ en 90 jours avec cette méthode",
        "à titre d'exemple concret : 23 clients, 100% ont atteint leurs objectifs en 60 jours",
        "ce n'est qu'un exemple parmi d'autres, mais j'ai accompagné 34 personnes vers leur premier 10k",
        # 11-13: rhetorical question opener then delivers figure
        "vous savez ce que j'ai réussi à produire ? 47 entrepreneurs à 10k€/mois",
        "et vous savez quel est le bilan réel ? 200 clients, 3 millions d'euros générés",
        "vous voulez savoir ce que ça donne concrètement ? 68% de mes clients doublent en 90 jours",
        # 14-15: FR+EN mix
        "j'ai eu 47 results concrets — 47 clients qui ont atteint leur premier 10k",
        "le bilan, literally : 312 personnes transformées en 36 mois",
    ],
    "tier2": [
        # 1-4: speaker quotes someone delivering the trigger
        "elle m'a dit 'j'ai accompagné 47 personnes comme toi — voilà le bilan'",
        "il m'a montré ses chiffres : '200 clients, 94% de réussite en 12 mois'",
        "mon mentor m'a soufflé : 'le vrai résultat c'est les 63 personnes que j'ai aidées à passer ce cap'",
        "elle m'a confié quelque chose qui m'a marqué : 'j'ai généré 1,2M€ avec exactement ce modèle'",
        # 5-7: trigger as correction/clarification in dialogue
        "non, attendez — la vraie donnée c'est : j'ai accompagné 47 entrepreneurs et 44 ont réussi",
        "je me corrige : ce n'est pas une conviction, c'est un résultat — 312 personnes aidées en 3 ans",
        "pour être précis : le chiffre réel, c'est 87 clients avec un taux de succès de 91%",
        # 8-10: multiple competing signals, correct one wins
        "voilà ce qui a tout changé ET ce que ça a produit : 47 résultats réels en 18 mois",
        "pas juste une conviction — une preuve : 200 clients, 94% de taux de réussite sur 3 ans",
        "c'est à la fois ma plus grande conviction ET mon bilan : 63 entrepreneurs à 10k€/mois",
        # 11-13: reading aloud from document/screen
        "je lis mes stats : 'depuis le lancement — 47 clients, 44 succès, 1 record de revenus'",
        "j'ouvre mon CRM et je vous lis ça : '312 personnes transformées, 94% de satisfaction'",
        "voici ce que j'ai devant moi : 'bilan Q4 — 85k€ générés, 23 nouveaux clients passés à 10k'",
        # 14-15: speaker introduces then retracts/corrects before delivering figure
        "j'allais dire que c'est une question de méthode — mais non, c'est un fait : 47 résultats prouvés",
        "au départ je pensais exagérer en disant ça, mais c'est factuel : 200 clients, 94% de succès",
    ],
    "tier3": [
        # Tier 3 (70): ultra-hard adversarial — signal buried, negated, wrapped, domain-specific, etc.

        # 8 phrases: conditional/hypothetical framing
        "si on regarde objectivement ce que ça a produit, on verrait que j'ai accompagné 47 entrepreneurs",
        "à supposer qu'on veuille mesurer : le résultat serait de 312 personnes transformées en 36 mois",
        "si je devais mettre un chiffre dessus, ce serait : 63 clients, 91% de taux de réussite",
        "imaginons qu'on audit mes résultats réels — on trouverait 200 accompagnements en 3 ans",
        "dans l'hypothèse où on voudrait quantifier : 85k€ en 90 jours, avec cette approche",
        "si on prenait le temps d'additionner : 47 entrepreneurs à 10k€/mois, c'est ce que ça a donné",
        "à condition de regarder les vrais chiffres : 94% de mes clients passent le cap en 60 jours",
        "en se demandant ce que ça vaut vraiment, on trouverait 312 résultats concrets en 3 ans",

        # 8 phrases: negative assertion — what it is NOT, but reveals PAR
        "ce n'est pas juste une impression — c'est 47 entrepreneurs accompagnés vers leur 10k€",
        "ce n'est pas une métaphore : j'ai littéralement aidé 200 personnes à doubler leur CA",
        "ce n'est pas un concept abstrait — les chiffres sont là : 94% de succès sur 312 clients",
        "non, je ne parle pas d'une conviction vague : je parle de 63 résultats réels et mesurés",
        "ce n'est pas une question de croyance — c'est 85k€ générés en 90 jours avec cette méthode",
        "pas une théorie — une réalité : 47 entrepreneurs ont atteint leur premier 10k€/mois",
        "je ne vous parle pas d'un principe — je vous parle de 200 accompagnements réussis en 3 ans",
        "ce n'est pas de la conviction pure — c'est factuel : 312 personnes transformées, données en main",

        # 8 phrases: metadiscursive wrap
        "je vais vous donner le bilan réel, et ce bilan c'est : 47 clients à 10k€/mois en 18 mois",
        "je veux vous montrer un chiffre qui va tout changer, et ce chiffre c'est : 312 succès sur 312",
        "je vais vous partager quelque chose de concret — voilà ce que ça a produit : 94% de réussite",
        "laissez-moi vous donner le résultat brut — j'ai accompagné 63 entrepreneurs vers leur premier 10k",
        "ce que je m'apprête à vous dire c'est factuel : 200 clients, 3 millions générés ensemble",
        "permettez-moi de vous montrer le vrai bilan — depuis 3 ans : 312 personnes, 94% de taux de succès",
        "voici ce que j'ai à vous annoncer : j'ai aidé 47 entrepreneurs à atteindre leur objectif réel",
        "je vais vous lire le résultat tel quel : '85k€ en 90 jours — voilà ce que la méthode produit'",

        # 8 phrases: domain-specific vocabulary (finance, sport, médecine, tech, cuisine)
        "mon portefeuille client : 47 positions ouvertes, 44 sorties positives — taux de réussite : 93,6%",
        "en termes de conversion, mon funnel a produit 312 clients transformés en 36 sprints",
        "côté métriques de santé du business : 200 accompagnements, 94% de NPS positif en 3 saisons",
        "j'ai coaché 63 athlètes vers leur podium — 63 sur 68, c'est mon taux de qualification",
        "en yield réel : 85k€ générés sur 90 jours, avec une exposition quasi nulle au risque",
        "mon protocole a été appliqué sur 47 cas — 44 rémissions complètes en moins de 60 jours",
        "mes sprints ont produit 312 livrables clients — avec un delta positif de 94% sur les OKRs",
        "le scorecard parle de lui-même : 200 mandats, 188 objectifs atteints, 3 ans d'historique",

        # 8 phrases: signal only in LAST sentence of a long anecdote
        "j'ai démarré sans rien, dans une petite chambre, avec une idée que tout le monde jugeait naïve. Pendant 18 mois, j'ai tout appris en faisant des erreurs que personne ne m'avait préparée à faire. Et aujourd'hui je peux vous dire que j'ai accompagné 47 entrepreneurs vers leur premier 10k€/mois.",
        "c'est une longue histoire. Au début, j'avais du mal à convaincre une seule personne. Puis j'ai affiné ma méthode mois après mois, échoué plusieurs fois, mais toujours recommencé. Le résultat aujourd'hui : 200 clients accompagnés, 94% de taux de réussite.",
        "pendant 3 ans, j'ai tout remis en question. La méthode, le positionnement, le tarif, tout. Il y a eu des moments où j'ai vraiment douté. Et pourtant, si je regarde le bilan objectif — 312 personnes transformées en 36 mois.",
        "j'ai d'abord refusé de croire que ma méthode fonctionnait à cette échelle. J'ai fait des recherches, j'ai demandé des retours, j'ai attendu. Et un jour les chiffres sont devenus impossibles à ignorer : j'avais accompagné 63 entrepreneurs vers leur premier 10k.",
        "au tout début je me demandais si j'avais le droit de parler de résultats. Je manquais de preuves. Je manquais de clients. Et puis les années ont passé, les témoignages se sont accumulés, et aujourd'hui je peux vous montrer : 85k€ générés en 90 jours avec cette approche.",
        "je me souviens très bien de la première fois que j'ai partagé cette méthode. Personne ne m'écoutait vraiment. C'était il y a 3 ans. Aujourd'hui le bilan est sans appel : 47 clients à 10k€/mois et un taux de succès de 94%.",
        "il m'a fallu longtemps pour oser parler de mes résultats. Je craignais de paraître prétentieux. Mais la réalité, c'est que les données sont là. Depuis le lancement de la méthode : 200 accompagnements réussis sur 3 ans.",
        "j'ai traversé des phases de doute profondes. Des moments où j'aurais pu tout arrêter. Et je suis content d'avoir persévéré — parce que le bilan, il est concret : 312 personnes qui ont atteint leur objectif grâce à cette approche.",

        # 7 phrases: signal attributed to third party then commented on
        "j'ai lu quelque part que selon un audit indépendant, j'avais aidé 47 entrepreneurs — et honnêtement ça m'a surpris moi-même",
        "un journaliste a écrit que j'avais accompagné 200 clients vers leur premier 10k — je n'avais jamais compté mais c'est exact",
        "selon mon équipe, le bilan est de 312 personnes transformées en 36 mois — et ça m'a ému de voir ça écrit",
        "une cliente m'a envoyé un message qui disait : 'grâce à vous, j'ai atteint 10k€ en 60 jours comme 46 autres avant moi'",
        "d'après une étude sur mes anciens clients, 94% ont maintenu leurs résultats au-delà de 6 mois — et je n'en savais rien",
        "il m'a montré un tableau et il m'a dit 'tu as généré 85k€ en 90 jours avec tes clients — tu réalises ?'",
        "elle m'a cité dans son podcast en disant 'il a accompagné 63 personnes vers leur premier 10k en moins de 2 ans' — c'est vrai",

        # 7 phrases: temporal/aspectual distancing
        "ça fait maintenant 3 ans que je mesure ce chiffre : 94% de mes clients atteignent leur objectif",
        "depuis 18 mois je comptabilise : 47 entrepreneurs accompagnés, 44 ont atteint leur premier 10k",
        "il y a 3 ans, j'aurais été incapable de dire ça — aujourd'hui le résultat est là : 312 transformations",
        "depuis que j'ai lancé cette approche, le bilan est de 200 accompagnements réussis",
        "au fil des années j'ai réalisé que le vrai chiffre, celui qui compte, c'est 63 résultats prouvés",
        "ça fait maintenant 36 mois que je note chaque résultat — et le total : 312 personnes transformées",
        "depuis le lancement, j'ai accompagné 47 entrepreneurs et 44 ont validé leur premier 10k€/mois",

        # 7 phrases: minimal signal (shortest possible correct classification)
        "47 entrepreneurs, 10k€/mois — c'est le bilan",
        "j'ai aidé 200 personnes — 94% de succès",
        "85k€ en 90 jours — voilà le résultat concret",
        "312 personnes transformées en 3 ans",
        "63 clients, 91% de réussite — c'est factuel",
        "j'ai accompagné 47 entrepreneurs vers ce cap",
        "depuis 18 mois : 47 résultats, 44 succès",

        # 7 phrases: speaker skeptical but still presents PAR
        "je ne suis pas forcément à l'aise avec ça, mais les chiffres montrent : 47 résultats en 18 mois",
        "j'aurais préféré ne pas avoir à parler de ça, mais les faits sont là : 200 clients, 94% de succès",
        "je trouve ça gênant de le dire, mais objectivement : j'ai accompagné 63 entrepreneurs vers leur 10k",
        "je n'aime pas me vanter, mais le bilan est le suivant : 312 transformations en 36 mois",
        "c'est pas mon style de sortir des chiffres, mais là c'est incontournable : 85k€ en 90 jours",
        "je suis un peu gêné de le partager, mais voilà le résultat réel : 47 entrepreneurs à 10k€/mois",
        "je ne dis jamais ça d'habitude, mais honnêtement : 200 clients accompagnés, 94% de taux de succès",
        "j'hésite toujours à avancer ces chiffres, mais ils sont réels : 47 entrepreneurs à leur 1er 10k",
        "je préférerais ne pas avoir à le quantifier, mais le résultat est là : 312 réussites en 3 ans",
    ],
}

# Validate counts
assert len(PAR_PHRASES["tier1"]) == 15, f"tier1: {len(PAR_PHRASES['tier1'])} (expected 15)"
assert len(PAR_PHRASES["tier2"]) == 15, f"tier2: {len(PAR_PHRASES['tier2'])} (expected 15)"
assert len(PAR_PHRASES["tier3"]) == 70, f"tier3: {len(PAR_PHRASES['tier3'])} (expected 70)"

# Merge into bank
bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
if "prim_ascension_reveal" in bank:
    print("prim_ascension_reveal already in bank — skipping (use --force to overwrite)")
    sys.exit(0)

bank["prim_ascension_reveal"] = PAR_PHRASES
BANK_PATH.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")

total = len(PAR_PHRASES["tier1"]) + len(PAR_PHRASES["tier2"]) + len(PAR_PHRASES["tier3"])
print(f"✓ prim_ascension_reveal added: {total} phrases (t1={len(PAR_PHRASES['tier1'])}, t2={len(PAR_PHRASES['tier2'])}, t3={len(PAR_PHRASES['tier3'])})")
print(f"  Bank now has {len(bank)} styles")
