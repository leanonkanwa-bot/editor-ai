#!/usr/bin/env python3
"""
Batch style-detection test — all styles in the storyboard LLM enum.

Extracts live style definitions from storyboard.py, sends ALL test phrases
to Claude in ONE API call, reports detection accuracy ranked worst-first.

Usage:
    python backend/tools/test_style_detection_batch.py
    python backend/tools/test_style_detection_batch.py --save results.json
"""
import json
import sys
from collections import defaultdict
from pathlib import Path
from anthropic import Anthropic

ROOT = Path(__file__).parent.parent.parent

# Load API key from backend/.env if not already in environment
import os
_env_file = ROOT / "backend" / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip()
            break


# ── Extract live style definitions from storyboard.py ────────────────────────
def _extract_style_defs() -> str:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    s = src.find("- CONTENT STYLE RULES (follow strictly, do not improvise):")
    e = src.find("- VERBATIM GROUNDING")
    if s == -1 or e == -1:
        raise RuntimeError("Cannot locate style-definition boundaries in storyboard.py")
    return src[s:e].strip()


# ── Test dataset ──────────────────────────────────────────────────────────────
# (expected_style, phrase_FR)
# 2-4 cases per style; boundary/ambiguous cases preferred over easy wins.
# prim_numbered_rule and prim_anecdote_frame intentionally ABSENT — they are
# in catalogue.py but NOT in the storyboard LLM enum (line 506 of storyboard.py).
TEST_CASES: list[tuple[str, str]] = [
    # ── stat ──────────────────────────────────────────────────────────────────
    ("stat", "Le taux d'abandon de panier est à 72% dans le e-commerce."),
    ("stat", "Selon une étude récente, 93% du contenu viral contient un visage."),
    ("stat", "En moyenne les créateurs qui postent quotidiennement ont 4x plus d'abonnés."),

    # ── key_phrase ────────────────────────────────────────────────────────────
    ("key_phrase", "Ce n'est pas le marché qui décide de ton prix, c'est toi."),
    ("key_phrase", "La régularité bat le talent sur le long terme."),
    ("key_phrase", "Vends la transformation, pas le produit."),

    # ── quote — personal declarations/moments, NOT transferable principles ────
    ("quote", "Ce jour-là m'a changé pour toujours."),
    ("quote", "Je n'aurais jamais cru que c'était possible."),
    ("quote", "C'était la décision la plus difficile de ma vie — et la meilleure."),

    # ── callout ───────────────────────────────────────────────────────────────
    ("callout", "Ce que peu de gens savent, c'est que les algorithmes favorisent les profils qui postent en soirée."),
    ("callout", "En réalité, 80% de tes résultats viennent de 20% de tes actions."),
    ("callout", "Une info clé : répondre aux commentaires dans la première heure triple la portée."),

    # ── comparison ────────────────────────────────────────────────────────────
    ("comparison", "L'ancienne méthode prenait trois heures. La nouvelle prend vingt minutes."),
    ("comparison", "Les freelances gagnent en liberté ce qu'ils perdent en sécurité."),
    ("comparison", "La version gratuite limite à 5 projets, la pro est illimitée."),

    # ── list ──────────────────────────────────────────────────────────────────
    ("list", "Trois raisons pour lesquelles les créateurs échouent : pas d'audience, pas d'offre, pas de système."),
    ("list", "Les quatre piliers de ma méthode : consistance, clarté, courage, et communauté."),
    ("list", "Les erreurs que j'ai faites : trop diversifier, trop tôt recruter, et mal choisir mes clients."),

    # ── question ──────────────────────────────────────────────────────────────
    ("question", "Et si la vraie raison pour laquelle tu ne te lances pas, c'était la peur du jugement ?"),
    ("question", "Qu'est-ce qui se passerait si tu doublais tes prix dès demain ?"),

    # ── timeline — process flows and narrative arcs (NOT year-anchored achievements) ──
    ("timeline", "D'abord tu définis ta niche, ensuite tu crées ton offre, puis tu la testes avant de scaler."),
    ("timeline", "Première étape : valider l'idée. Deuxième étape : construire le MVP. Troisième étape : lancer en bêta. Quatrième étape : itérer."),
    ("timeline", "On a commencé par l'audience, ensuite le produit, puis le tunnel de vente, et enfin la publicité."),

    # ── dialogue ──────────────────────────────────────────────────────────────
    ("dialogue", "Mon client m'a dit : je n'ai pas le budget. Je lui ai répondu : le budget n'est pas le problème, c'est la priorité."),
    ("dialogue", "Elle m'a demandé combien ça coûtait, j'ai dit 5 000 euros, elle a répondu 'prenons rendez-vous'."),

    # ── trend ─────────────────────────────────────────────────────────────────
    ("trend", "Les ventes ont augmenté de façon continue pendant six mois d'affilée."),
    ("trend", "Le nombre d'abonnés est en chute libre depuis le changement d'algorithme."),

    # ── attributed_quote ──────────────────────────────────────────────────────
    ("attributed_quote", "Comme disait Steve Jobs : les gens ne savent pas ce qu'ils veulent jusqu'à ce qu'on le leur montre."),
    ("attributed_quote", "Warren Buffett résume ça en une phrase : sois avide quand les autres ont peur."),

    # ── carousel ──────────────────────────────────────────────────────────────
    ("carousel", "Conseil 1 : publie chaque jour. Conseil 2 : engage ta communauté. Conseil 3 : analyse tes stats."),
    ("carousel", "Tip rapide numéro un : réponds à tous tes commentaires. Tip deux : utilise des sous-titres. Tip trois : termine avec une question."),

    # ── definition ────────────────────────────────────────────────────────────
    ("definition", "Le taux de conversion, c'est le pourcentage de visiteurs qui passent à l'achat."),
    ("definition", "Le MRR, ou Monthly Recurring Revenue, c'est le revenu mensuel récurrent de ton abonnement."),

    # ── checklist ─────────────────────────────────────────────────────────────
    ("checklist", "J'ai vérifié les trois points : le tunnel est en place, le paiement fonctionne, l'email de confirmation part bien."),
    ("checklist", "Avant de publier, je vérifie toujours : le titre est accrocheur, la miniature est propre, le CTA est clair."),

    # ── score ─────────────────────────────────────────────────────────────────
    ("score", "On a fini deuxième sur la liste des meilleurs outils de la catégorie."),
    ("score", "Le match s'est terminé trois à un en notre faveur."),

    # ── mindmap ───────────────────────────────────────────────────────────────
    ("mindmap", "La croissance d'une marque repose sur trois axes : la visibilité, la confiance, et la conversion."),
    ("mindmap", "Mon business gravite autour de trois pôles : la formation, le coaching, et l'affiliation."),

    # ── instagram-follow ──────────────────────────────────────────────────────
    ("instagram-follow", "Suis-moi sur Instagram pour plus de contenu comme celui-là, tous les jours."),
    ("instagram-follow", "Rejoins ma communauté sur Instagram — lien dans la bio."),

    # ── tiktok-follow ─────────────────────────────────────────────────────────
    ("tiktok-follow", "Abonne-toi sur TikTok, je poste tous les jours des conseils business."),
    ("tiktok-follow", "Follow moi sur TikTok pour ne rater aucun épisode."),

    # ── yt-lower-third ────────────────────────────────────────────────────────
    ("yt-lower-third", "Abonne-toi et active la cloche pour ne rater aucune vidéo."),
    ("yt-lower-third", "N'oublie pas de mettre un pouce bleu et de t'abonner à la chaîne."),

    # ── news_ticker ───────────────────────────────────────────────────────────
    ("news_ticker", "Breaking : notre offre de lancement se termine dans 48 heures."),
    ("news_ticker", "Alerte : les inscriptions ferment ce vendredi à minuit."),

    # ── rating ────────────────────────────────────────────────────────────────
    ("rating", "Personnellement, je lui donne un 8 sur 10. Vraiment solide."),
    ("rating", "Je mettrais 9 sur 10 à cette méthode — elle m'a changé la vie."),
    ("rating", "Honnêtement ? 6 sur 10. Ça fait le travail, mais ce n'est pas exceptionnel."),

    # ── map_location ──────────────────────────────────────────────────────────
    ("map_location", "Je parlais depuis Séoul, devant une salle de 300 personnes."),
    ("map_location", "C'est à Barcelone que j'ai eu cette révélation."),

    # ── progress_bar ──────────────────────────────────────────────────────────
    ("progress_bar", "On est à 70% de notre objectif annuel. Il reste encore du chemin."),
    ("progress_bar", "Le projet est avancé à 40% — on est à peu près à mi-chemin de la phase bêta."),

    # ── before_after_image ────────────────────────────────────────────────────
    ("before_after_image", "Avant, mon profil Instagram était vide et sans direction. Aujourd'hui, il génère 200 leads par mois."),
    ("before_after_image", "La page de vente avant la refonte : conversion à 1%. Après : conversion à 4,3%."),

    # ── countdown ─────────────────────────────────────────────────────────────
    ("countdown", "Il reste exactement 72 heures avant la fermeture des inscriptions."),
    ("countdown", "Plus que 3 jours — après ça le prix remonte de 40%."),

    # ── poll_question ─────────────────────────────────────────────────────────
    ("poll_question", "Dis-moi dans les commentaires : A) tu crées tous les jours, B) deux à trois fois par semaine, ou C) quand l'inspiration vient ?"),
    ("poll_question", "Vote maintenant : tu préfères les formations courtes et intenses, ou les programmes sur plusieurs mois ?"),

    # ── myth_vs_fact ──────────────────────────────────────────────────────────
    ("myth_vs_fact", "On dit que la réussite s'explique par la chance. En réalité, la chance favorise ceux qui se préparent."),
    ("myth_vs_fact", "Le mythe : il faut des années pour réussir. La vérité : six mois suffisent avec la bonne méthode."),

    # ── step_number ───────────────────────────────────────────────────────────
    ("step_number", "La première étape — et c'est celle que tout le monde rate — c'est de valider l'idée AVANT de créer le produit."),
    ("step_number", "Étape numéro deux : définir ton avatar client avec une précision chirurgicale."),
    ("step_number", "C'est là, à ce moment précis, que tout a basculé pour moi."),

    # ── quote_carousel ────────────────────────────────────────────────────────
    ("quote_carousel", "'Travaille dur.' 'Sois patient.' 'N'abandonne jamais.' Ce sont les trois phrases que je me répète chaque matin."),
    ("quote_carousel", "Trois mantras : 'Clarté d'abord.' 'Constance toujours.' 'Courage avant tout.'"),

    # ── emoji_reaction ────────────────────────────────────────────────────────
    ("emoji_reaction", "Et là j'ai réalisé — c'est absolument incroyable ce qu'on peut accomplir en 90 jours !"),
    ("emoji_reaction", "Franchement, ça m'a soufflé. Je n'aurais jamais cru que c'était possible."),

    # ── price_tag ─────────────────────────────────────────────────────────────
    ("price_tag", "J'ai investi 1 200 euros dans cette formation — et ça a tout changé."),
    ("price_tag", "L'accès à vie est à 497 euros, une seule fois."),

    # ── warning_soft ──────────────────────────────────────────────────────────
    ("warning_soft", "Attention, cette erreur est la plus fréquente chez les créateurs débutants."),
    ("warning_soft", "Je te préviens : si tu skip cette étape, tu vas galérer pendant des mois."),

    # ── testimonial ───────────────────────────────────────────────────────────
    ("testimonial", "Lucas, consultant indépendant, m'a dit : grâce à ta méthode j'ai doublé mes revenus en quatre mois."),
    ("testimonial", "Thomas, e-commerçant, témoigne : en trois semaines on a triplé notre taux de conversion."),

    # ── versus_battle ─────────────────────────────────────────────────────────
    ("versus_battle", "Employé contre freelance — voici la vérité que personne ne dit."),
    ("versus_battle", "Instagram versus LinkedIn : lequel gagne pour le B2B en 2025 ?"),

    # ── recap_summary ─────────────────────────────────────────────────────────
    ("recap_summary", "Pour résumer ce qu'on a vu aujourd'hui : l'état d'esprit, la stratégie, l'exécution."),
    ("recap_summary", "Voilà les trois points à retenir de cette vidéo : la clarté, la régularité, et la valeur."),

    # ── location_journey ──────────────────────────────────────────────────────
    ("location_journey", "J'ai voyagé de Paris à Lyon, ensuite à Marseille, et finalement à Nice pour cette tournée."),
    ("location_journey", "Mon parcours nomade : Abidjan, Dakar, Casablanca, Paris, Amsterdam — cinq villes en deux mois."),
    ("location_journey", "La tournée passait par Tokyo, Singapour, Sydney et Auckland."),

    # ── formula_equation ──────────────────────────────────────────────────────
    ("formula_equation", "La formule est simple : temps multiplié par constance égale résultat."),
    ("formula_equation", "Revenu égal prix fois volume. C'est aussi simple que ça."),

    # ── roadmap_milestone ─────────────────────────────────────────────────────
    ("roadmap_milestone", "Après 18 mois de travail, on a enfin signé notre premier client à six chiffres."),
    ("roadmap_milestone", "On vient d'atteindre les 10 000 abonnés — un cap qu'on cherchait depuis le début."),

    # ── pros_cons ─────────────────────────────────────────────────────────────
    ("pros_cons", "Les avantages du freelancing : la liberté et le revenu illimité. Les inconvénients : l'instabilité et la solitude."),
    ("pros_cons", "Pour cette stratégie, les pros c'est la rapidité. Les cons c'est le coût et la complexité."),

    # ── star_rating_review ────────────────────────────────────────────────────
    ("star_rating_review", "Un client a laissé un avis cinq étoiles en disant que c'était la meilleure formation suivie."),
    ("star_rating_review", "On est à 4,8 étoiles sur 5 avec plus de 200 avis vérifiés."),

    # ── income_reveal ─────────────────────────────────────────────────────────
    ("income_reveal", "Je vais te dire exactement ce que j'ai gagné ce mois-ci : 27 400 euros."),
    ("income_reveal", "Le mois de novembre... 43 000 euros de chiffre d'affaires. Je n'en revenais pas."),

    # ── question_answer_pair ──────────────────────────────────────────────────
    ("question_answer_pair", "Qu'est-ce que ça change concrètement ? Ça te fait gagner deux heures par jour."),
    ("question_answer_pair", "Pourquoi ça marche ? Parce que tu t'adresses à l'émotion, pas à la raison."),

    # ── chapter_marker ────────────────────────────────────────────────────────
    ("chapter_marker", "On passe maintenant à la partie deux : comment structurer ton offre."),
    ("chapter_marker", "Chapitre trois — la phase de scale. Voici ce que ça implique."),

    # ── secret_reveal ─────────────────────────────────────────────────────────
    ("secret_reveal", "Le secret que personne ne partage, c'est que les meilleurs créateurs planifient trois semaines à l'avance."),
    ("secret_reveal", "Ce que personne ne dit, c'est que 80% des ventes se font après le cinquième point de contact."),

    # ── objection_response ────────────────────────────────────────────────────
    ("objection_response", "Tu vas me dire : je n'ai pas le temps. Et moi je te réponds : tu n'as pas les bonnes priorités."),
    ("objection_response", "Je t'entends — c'est trop cher. Mais dis-moi, combien te coûte de ne rien faire ?"),

    # ── data_bar_chart ────────────────────────────────────────────────────────
    ("data_bar_chart", "Résultats par produit : formation 50 000 euros, coaching 20 000 euros, affiliation 10 000 euros."),
    ("data_bar_chart", "Conversions comparées : email à 3,2%, SMS à 6,7%, notification push à 1,4%."),

    # ── cause_effect ──────────────────────────────────────────────────────────
    ("cause_effect", "Parce que tu postes de façon irrégulière, l'algorithme te pénalise."),
    ("cause_effect", "Tu as défini clairement ton avatar client — donc tes messages résonnent immédiatement."),

    # ── number_ranking ────────────────────────────────────────────────────────
    ("number_ranking", "Les trois outils qui m'ont le plus impacté : numéro un Notion, numéro deux Calendly, numéro trois Loom."),
    ("number_ranking", "Top 3 des canaux qui convertissent le mieux : premier l'email, deuxième le webinaire, troisième le téléphone."),

    # ── hand_written_note ─────────────────────────────────────────────────────
    ("hand_written_note", "Entre nous : ne fais jamais ça devant un prospect. Vraiment."),
    ("hand_written_note", "Petite parenthèse — ce détail que je vais te donner vaut de l'or."),

    # ── speech_bubble_thought ─────────────────────────────────────────────────
    ("speech_bubble_thought", "Tu es probablement en train de te dire : ça ne marchera pas pour moi."),
    ("speech_bubble_thought", "Dans ta tête là maintenant : 'mais moi j'ai pas le temps pour ça.'"),

    # ── calendar_date_highlight ───────────────────────────────────────────────
    ("calendar_date_highlight", "Le 14 janvier 2025, j'ai lancé officiellement la formation."),
    ("calendar_date_highlight", "Dans 90 jours exactement, on ouvre les portes de la prochaine cohorte."),

    # ── percentage_split ──────────────────────────────────────────────────────
    ("percentage_split", "60% de mon temps va à la création de contenu, 30% à la vente, et 10% à l'administratif."),
    ("percentage_split", "Mon CA se répartit : 70% formations, 20% coaching, 10% affiliation."),

    # ── red_flag_list ─────────────────────────────────────────────────────────
    ("red_flag_list", "Les signaux d'alerte à fuir : il négocie le prix dès le premier échange, change les specs à chaque réunion, ne respecte pas les délais."),
    ("red_flag_list", "Les trois erreurs classiques qui tuent un business : pas de liste email, pas d'offre claire, pas de suivi."),

    # ── success_metric_badge ──────────────────────────────────────────────────
    ("success_metric_badge", "On vient d'atteindre un million d'euros de revenus cumulés — un objectif fixé il y a deux ans."),
    ("success_metric_badge", "100 clients payants. Le cap qu'on cherchait depuis le lancement. On l'a."),

    # ── client_avatar_persona ─────────────────────────────────────────────────
    ("client_avatar_persona", "Mon client idéal s'appelle Thomas, il a 35 ans, il est cadre supérieur, manque de temps et veut un revenu complémentaire."),
    ("client_avatar_persona", "Je m'adresse à Sophie : 28 ans, freelance en galère avec son positionnement, créative mais pas à l'aise avec la vente."),

    # ── book_recommendation ───────────────────────────────────────────────────
    ("book_recommendation", "Je te recommande de lire Atomic Habits de James Clear — ça m'a transformé."),
    ("book_recommendation", "Lis Thinking, Fast and Slow de Daniel Kahneman. Indispensable pour comprendre la décision d'achat."),

    # ── tool_stack ────────────────────────────────────────────────────────────
    ("tool_stack", "Ma stack quotidienne : Notion pour l'organisation, Stripe pour les paiements, Kajabi pour les formations."),
    ("tool_stack", "Les outils que j'utilise : CapCut pour le montage, Canva pour les visuels, Systeme.io pour le tunnel."),

    # ── revenue_breakdown ─────────────────────────────────────────────────────
    ("revenue_breakdown", "Mon CA de 80 000 euros ce mois : 50 000 en formation, 20 000 en coaching, 10 000 en affiliation."),
    ("revenue_breakdown", "Les sources : 60K ventes directes, 15K partenariats, 5K droits d'auteur."),

    # ── age_milestone ─────────────────────────────────────────────────────────
    ("age_milestone", "À 24 ans j'ai lancé ma première boîte. À 27 ans j'ai tout perdu. À 30 ans j'ai recommencé."),
    ("age_milestone", "J'avais 19 ans quand j'ai fait ma première vente en ligne."),

    # ── contrarian_take ───────────────────────────────────────────────────────
    ("contrarian_take", "Je vais dire quelque chose que personne n'ose dire : le diplôme ne sert à rien si tu veux entreprendre."),
    ("contrarian_take", "Voici mon opinion impopulaire : les réseaux sociaux ne sont pas la priorité pour une PME qui commence."),
    ("contrarian_take", "La vérité que tu ne veux pas entendre : travailler plus n'est pas la solution."),

    # ── action_step_cta ───────────────────────────────────────────────────────
    ("action_step_cta", "Maintenant voici exactement ce que tu dois faire dès ce soir : ouvre un document et liste tes cinq clients idéaux."),
    ("action_step_cta", "Passe à l'action maintenant — lance le formulaire, remplis-le, envoie-le. Trois minutes."),

    # ── story_chapter_transition ──────────────────────────────────────────────
    ("story_chapter_transition", "Et là tout a changé."),
    ("story_chapter_transition", "Mais voilà ce qui s'est passé ensuite — et ça m'a complètement pris par surprise."),
    ("story_chapter_transition", "La suite... je n'aurais jamais pu l'anticiper."),

    # ── live_reaction_split ───────────────────────────────────────────────────
    ("live_reaction_split", "On pensait que ça allait planter. En réalité, on a fait notre meilleur mois."),
    ("live_reaction_split", "Tout le monde disait que cette stratégie était morte. En réalité, elle cartonne encore."),

    # ── hidden_cost_reveal ────────────────────────────────────────────────────
    ("hidden_cost_reveal", "Le prix affiché c'est 99 euros. Mais le coût réel une fois tout inclus, c'est 340 euros."),
    ("hidden_cost_reveal", "Abonnement affiché : 9,99 euros par mois. Coût réel avec les fonctions nécessaires : 47 euros."),

    # ── social_proof_counter ──────────────────────────────────────────────────
    ("social_proof_counter", "On vient de passer les 50 000 abonnés — ça a explosé en 48 heures."),
    ("social_proof_counter", "En 72 heures on est passé de 800 à 15 000 membres dans le groupe."),

    # ── timeline_prediction ───────────────────────────────────────────────────
    ("timeline_prediction", "Jusqu'ici on a validé l'idée et lancé la bêta. Dans les prochains mois, on prévoit d'automatiser le funnel."),
    ("timeline_prediction", "Phase 1 terminée : la formation est en ligne. Phase 2 en cours. Phase 3 prévue : le mastermind."),

    # ── red_thread_connector ──────────────────────────────────────────────────
    ("red_thread_connector", "Tu te souviens de ce que j'ai dit sur la régularité ? Et du point sur la confiance ? Les deux sont liés."),
    ("red_thread_connector", "L'avatar client dont on a parlé au début, et la stratégie de pricing qu'on vient de voir — ce sont les deux faces d'un même problème."),

    # ── silent_beat_pause ─────────────────────────────────────────────────────
    ("silent_beat_pause", "[pause dramatique] ..."),
    ("silent_beat_pause", "Laisse ça reposer."),

    # ── comment_reply_style ───────────────────────────────────────────────────
    ("comment_reply_style", "J'ai reçu ce commentaire : t'as l'air d'un imposteur. Voici ma réponse : tu as raison, j'ai longtemps cru l'être."),
    ("comment_reply_style", "Quelqu'un m'a écrit : comment tu fais pour rester motivé ? Ma réponse : je ne compte pas sur la motivation, je compte sur les systèmes."),

    # ── before_you_scroll ─────────────────────────────────────────────────────
    ("before_you_scroll", "Attends avant de partir — ce que je vais dire dans les dix prochaines secondes peut changer ta façon de vendre."),
    ("before_you_scroll", "Lis ça avant de continuer à scroller. C'est important."),
    ("before_you_scroll", "Stop — avant que tu passes à la suite, écoute juste ça."),

    # ── traffic_light_status ──────────────────────────────────────────────────
    ("traffic_light_status", "Cette stratégie ? C'est rouge. On l'abandonne maintenant."),
    ("traffic_light_status", "Le projet est vert — on valide et on passe à la suite."),
    ("traffic_light_status", "Pour moi cette tactique est encore en jaune — à surveiller de près."),

    # ── day_in_life_schedule ──────────────────────────────────────────────────
    ("day_in_life_schedule", "Je me lève à 5h30, à 6h je lis, à 7h je fais du sport, à 9h je commence le deep work, à 12h je pause."),
    ("day_in_life_schedule", "Ma journée : 6h réveil, 8h création de contenu, 12h pause déjeuner, 14h appels clients, 17h admin."),

    # ── skill_tree_unlock ─────────────────────────────────────────────────────
    ("skill_tree_unlock", "D'abord j'ai maîtrisé la création de contenu, ensuite le copywriting s'est débloqué, puis la vente."),
    ("skill_tree_unlock", "Premier niveau : la cohérence. Une fois acquise, l'audience s'est débloquée. Avec l'audience, la monétisation."),

    # ── audience_poll_result ──────────────────────────────────────────────────
    ("audience_poll_result", "J'ai posé la question à ma communauté : 67% ont répondu vidéo, 33% texte. La vidéo gagne haut la main."),
    ("audience_poll_result", "Résultat du sondage : 58% pour l'abonnement mensuel, 42% pour l'achat unique — l'abonnement l'emporte."),

    # ── broken_promise_tracker ────────────────────────────────────────────────
    ("broken_promise_tracker", "J'avais promis de poster chaque jour — tenu. J'avais promis de lancer en mars — pas tenu. Plus d'engagement — à moitié."),
    ("broken_promise_tracker", "Mes engagements de l'année : newsletter hebdo — tenu. Podcast mensuel — pas tenu. Masterclass gratuite — tenu."),

    # ── ingredient_list ───────────────────────────────────────────────────────
    ("ingredient_list", "Pour réussir ça, il te faut trois choses : une audience existante, une offre validée, et un système de paiement."),
    ("ingredient_list", "Les ingrédients de ma méthode : clarté, constance, et curiosité."),

    # ── resource_allocation ───────────────────────────────────────────────────
    ("resource_allocation", "J'alloue 40% de mon budget à la publicité, 30% aux outils, et 30% au coaching."),
    ("resource_allocation", "Le temps dans ma semaine : 60% sur le produit, 25% sur le marketing, 15% sur l'admin."),

    # ── fill_in_the_blank ─────────────────────────────────────────────────────
    ("fill_in_the_blank", "La clé de la croissance, c'est ___ — c'est la constance."),
    ("fill_in_the_blank", "Ce qui différencie les pros des amateurs, c'est ___ — et la réponse c'est les systèmes."),

    # ── streak_counter ────────────────────────────────────────────────────────
    ("streak_counter", "Ça fait maintenant 87 jours que je publie du contenu chaque jour sans exception."),
    ("streak_counter", "Cent jours de suite. Sans interruption. Ce streak m'a tout appris sur la discipline."),

    # ── before_now_later ──────────────────────────────────────────────────────
    ("before_now_later", "Il y a deux ans je galérais à trouver des clients. Aujourd'hui je dirige une équipe de six. Dans deux ans j'ouvre à l'international."),
    ("before_now_later", "Avant : revenus irréguliers, anxiété permanente. Maintenant : 15k par mois récurrent. Demain : focus impact."),

    # ── platform_stats ────────────────────────────────────────────────────────
    ("platform_stats", "Sur TikTok j'ai 180 000 abonnés, sur YouTube 45 000, et sur Instagram 32 000."),
    ("platform_stats", "LinkedIn : 12k connexions. Instagram : 28k abonnés. YouTube : 8k abonnés."),

    # ── cost_comparison ───────────────────────────────────────────────────────
    ("cost_comparison", "Le plan Starter à 0 euros, le Pro à 29 euros par mois, et l'Enterprise à 299 euros."),
    ("cost_comparison", "Trois options : formation seule à 497 euros, formation plus coaching à 997 euros, VIP tout inclus à 2 500 euros."),

    # ── decision_matrix ───────────────────────────────────────────────────────
    ("decision_matrix", "La matrice d'Eisenhower : urgent et important, urgent mais pas important, important mais pas urgent, ni urgent ni important."),
    ("decision_matrix", "Quatre quadrants : à faire maintenant, à planifier, à déléguer, et à supprimer."),

    # ── habit_tracker ─────────────────────────────────────────────────────────
    ("habit_tracker", "Mon tracker de la semaine pour la lecture : lundi oui, mardi non, mercredi oui, jeudi oui, vendredi non, samedi oui, dimanche oui."),
    ("habit_tracker", "Suivi sport cette semaine : L oui, M non, M oui, J oui, V non, S oui, D oui — cinq sur sept."),

    # ── income_vs_expense ─────────────────────────────────────────────────────
    ("income_vs_expense", "Ce mois-ci je gagne 12 000 euros et mes dépenses fixes sont à 7 500 euros. La différence c'est mon vrai salaire."),
    ("income_vs_expense", "Entrées : 40 000 euros. Sorties : 28 000 euros. Bénéfice net : 12 000 euros."),

    # ── milestone_recap ───────────────────────────────────────────────────────
    ("milestone_recap", "En 2021 premier client. En 2022 première équipe. En 2023 premier million. En 2024 première levée de fonds."),
    ("milestone_recap", "Les grandes étapes : lancement en janvier, 1 000 clients en avril, pivot en juillet, profitabilité en novembre."),

    # ── content_calendar ──────────────────────────────────────────────────────
    ("content_calendar", "Mon planning de la semaine : lundi un post produit, mercredi une story behind-the-scenes, vendredi un reel viral."),
    ("content_calendar", "Le calendrier éditorial : mardi tips pratiques, jeudi études de cas, samedi inspiration personnelle."),

    # ── client_result_number ──────────────────────────────────────────────────
    ("client_result_number", "Mon client Fabien a augmenté son chiffre d'affaires de 340% en 60 jours avec cette méthode."),
    ("client_result_number", "Elle est passée de zéro à 10 000 abonnés en trois mois — sans publicité payante."),

    # ── mistake_lesson ────────────────────────────────────────────────────────
    ("mistake_lesson", "J'ai fait l'erreur de lancer sans valider — et j'en ai retenu qu'on ne vend pas ce qu'on croit bon."),
    ("mistake_lesson", "Mon erreur a été de recruter trop vite. La leçon : systématise avant de déléguer."),

    # ── tool_comparison ───────────────────────────────────────────────────────
    ("tool_comparison", "Notion contre Trello contre Asana : Notion gagne sur la flexibilité, Trello sur la simplicité, Asana sur les workflows."),
    ("tool_comparison", "ChatGPT vs Claude vs Gemini pour la rédaction : Claude pour la nuance, ChatGPT pour la vitesse, Gemini pour la recherche."),

    # ── weekly_review ─────────────────────────────────────────────────────────
    ("weekly_review", "Ma semaine : contenu 9 sur 10, prospection 6 sur 10, santé 7 sur 10, famille 8 sur 10."),
    ("weekly_review", "Bilan de la semaine : productivité top, social media en berne, sport à rattraper."),

    # ── audience_question ─────────────────────────────────────────────────────
    ("audience_question", "Et toi, quel est le plus grand obstacle qui t'empêche de passer à l'action ? Dis-moi en commentaire."),
    ("audience_question", "Tu gères combien d'heures de travail par semaine en ce moment ? Je suis curieux."),
    ("audience_question", "C'est quoi ton plus gros blocage en ce moment ? Réponds là, je lis tous les commentaires."),

    # ── prim_stat_counter ─────────────────────────────────────────────────────
    ("prim_stat_counter", "On vient d'atteindre 46,2 millions d'euros de CA. Un chiffre qui nous a tous stupéfaits."),
    ("prim_stat_counter", "Le taux de conversion est passé à 8,7% — le meilleur résultat depuis le lancement."),
    ("prim_stat_counter", "500 000 euros générés ce mois-ci. Je répète : cinq cent mille euros en un mois."),

    # ── prim_split_compare ────────────────────────────────────────────────────
    ("prim_split_compare", "Avant cette méthode : épuisé et sans résultats. Après : reposé et générateur de revenus."),
    ("prim_split_compare", "L'ancienne mentalité versus la nouvelle : scarcity mindset contre abundance mindset."),
    ("prim_split_compare", "L'approche classique : donner pour recevoir. La nouvelle approche : créer de la valeur d'abord."),

    # ── prim_journey_map ──────────────────────────────────────────────────────
    ("prim_journey_map", "Je me suis envolé de Paris pour aller m'installer en Thaïlande, et ça a tout changé."),
    ("prim_journey_map", "J'ai quitté la France pour m'installer à Bangkok — un trajet de 9 500 kilomètres."),
    ("prim_journey_map", "On a tout plaqué à Lyon pour déménager à Lisbonne. C'était la meilleure décision de notre vie."),
    ("prim_journey_map", "Je suis parti de Montréal pour rejoindre Tokyo — une aventure qui a duré deux ans."),

    # ── prim_cinematic_reveal — manifesto card, ONE per video, phrase as thesis ──
    # Boundary: capstone declaration, not a tip or intermediate key_phrase
    ("prim_cinematic_reveal", "Ce que j'ai compris ce jour-là, c'est que tout repose sur la confiance — et rien d'autre."),
    # Boundary: personal creed / absolute truth — not a how-to step
    ("prim_cinematic_reveal", "Arrête de vendre des produits. Vends une transformation. C'est tout."),
    # Boundary: transformational declaration — not a stat or number
    ("prim_cinematic_reveal", "Le vrai levier, celui que personne ne te dit, c'est de devenir quelqu'un d'autre."),
]


# ── Runner ────────────────────────────────────────────────────────────────────
def run_batch_test(cases: list[tuple[str, str]], style_defs: str) -> list[dict]:
    numbered = "\n".join(
        f"{i+1}. {phrase}"
        for i, (_, phrase) in enumerate(cases)
    )

    system = (
        "You are a precise content-style classifier.\n\n"
        "STYLE DEFINITIONS (source of truth — use these exactly):\n"
        f"{style_defs}\n\n"
        "Rules:\n"
        "- Choose the MOST SPECIFIC style whose trigger conditions are satisfied.\n"
        "- Never invent style names — use only names that appear in the definitions above.\n"
        "- Return ONLY a compact JSON array, no markdown, no explanation:\n"
        '  [{"id":1,"style":"style_name"},{"id":2,"style":"style_name"},...]'
    )

    user = (
        f"Classify each of the following {len(cases)} phrases. "
        "Return exactly one JSON object per phrase, in order, with 'id' (1-based) and 'style'.\n\n"
        + numbered
    )

    client = Anthropic()
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    results_raw: list[dict] = json.loads(raw)

    if len(results_raw) != len(cases):
        print(
            f"WARNING: expected {len(cases)} results, got {len(results_raw)}",
            file=sys.stderr,
        )

    results = []
    for i, (expected, phrase) in enumerate(cases):
        assigned = results_raw[i]["style"] if i < len(results_raw) else "MISSING"
        results.append({
            "id": i + 1,
            "expected": expected,
            "assigned": assigned,
            "phrase": phrase,
            "correct": assigned == expected,
        })
    return results


# ── Report ────────────────────────────────────────────────────────────────────
def generate_report(results: list[dict]) -> None:
    by_style: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_style[r["expected"]].append(r)

    rows = []
    for style, cases in by_style.items():
        n = len(cases)
        correct = sum(1 for c in cases if c["correct"])
        rate = correct / n * 100
        collisions: list[str] = [c["assigned"] for c in cases if not c["correct"]]
        coll_str = ", ".join(
            f"{s}({collisions.count(s)}x)" for s in sorted(set(collisions))
        ) if collisions else ""
        rows.append((rate, style, n, correct, coll_str))

    rows.sort(key=lambda r: (r[0], r[1]))  # worst first

    total_correct = sum(1 for r in results if r["correct"])
    total = len(results)
    n_styles = len(by_style)

    print(f"\n{'='*105}")
    print(f"STYLE DETECTION BATCH REPORT  —  {total} phrases  |  {n_styles} styles  |  model: claude-opus-4-7")
    print(f"{'='*105}")
    print(f"{'STYLE':<38} {'N':>3} {'OK':>3} {'RATE':>6}   COLLISIONS DETECTED")
    print(f"{'-'*105}")

    for rate, style, n, correct, coll_str in rows:
        flag = "⚠  " if rate < 67 else ("   " if rate == 100 else "   ")
        print(f"{flag}{style:<35} {n:>3} {correct:>3} {rate:>5.0f}%   {coll_str}")

    print(f"{'-'*105}")
    print(f"OVERALL: {total_correct}/{total} correct ({total_correct/total*100:.1f}%)")
    print(f"{'='*105}\n")

    failures = [r for r in results if not r["correct"]]
    if failures:
        print(f"FAILURES ({len(failures)} total — sorted by expected style):")
        for f in sorted(failures, key=lambda x: x["expected"]):
            phrase_preview = f["phrase"][:90] + ("…" if len(f["phrase"]) > 90 else "")
            print(f"  [{f['expected']:30s}] → got [{f['assigned']}]")
            print(f"    \"{phrase_preview}\"")
        print()

    # Two-style styles with zero overlap registered (prim_numbered_rule, prim_anecdote_frame)
    print("NOTE: prim_numbered_rule and prim_anecdote_frame are NOT in the storyboard LLM enum")
    print("      (they are registered in catalogue.py but the LLM cannot select them).")
    print("      These two styles are injected by other mechanisms — not tested here.")


if __name__ == "__main__":
    print("Extracting style definitions from storyboard.py …")
    style_defs = _extract_style_defs()
    print(f"  → {len(style_defs):,} chars  ({len(style_defs)//4:,} tokens est.)")
    print(f"Running batch classification — {len(TEST_CASES)} test cases in ONE API call …")
    results = run_batch_test(TEST_CASES, style_defs)

    if "--save" in sys.argv:
        idx = sys.argv.index("--save")
        out_path = Path(sys.argv[idx + 1])
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Raw results saved → {out_path}")

    generate_report(results)
