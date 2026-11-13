# Fa·Fo Scanner : mise en route (environ 15 minutes, gratuit)

Le scanner tourne tout seul dans le cloud (GitHub Actions), toutes les 15 minutes pendant la séance de Paris (vos positions toutes les 15 minutes, toutes les autres actions toutes les 30 minutes).
Il envoie les notifications sur votre téléphone (application ntfy) et met à jour une page installable (l'application avec les onglets).

## Ce que ça fait

Le scanner cherche les configurations **en cours de validation**, c'est-à-dire la bougie Heikin Ashi (HA) en formation, avant sa clôture, avec le pourcentage de chances qu'elle valide la configuration.

- **Fa** (🟢) : le **haut du corps** de la bougie HA dépasse la Tenkan et une **MM7 ascendante**. Une action apparaît en « Fa en cours » si elle n'est **pas déjà en Fa** et si la bougie en formation a au moins **30 %** de chances de clôturer en Fa. La mention « déjà au-dessus » indique que la bougie remplit la condition en ce moment même.
- **S haussière** (🟩) : bougie HA **sans corps** (corps ≤ 10 % de sa hauteur) après **au moins 3 bougies HA baissières**, **toutes baissières** ; des mèches sont tolérées, mais au moins **2 bougies de la série** n'ont pas (ou peu) de mèche au-dessus du corps (mèche ≤ 10 % de leur hauteur). Elle apparaît en « S en cours » dès **30 %** de chances de clôturer ainsi.
- **Unités de temps pour les actions que vous ne détenez pas** : 4h, 1j, 1sem, 1mois. Le **1h n'est plus affiché ni notifié** pour elles.
- **Application** : unités de temps en haut, puis seulement deux onglets, **Fa** et **S**, triés par probabilité décroissante. Une Fa déjà validée ne s'y affiche plus.
- **Notifications (actions non détenues)** : une alerte quand la probabilité franchit 30 % (« 🟢 Fa1j 42% · AIR », « 🟩 S4h 35% · AIR »), et une seule autre si elle dépasse 60 %, pour la même bougie. Pas d'alerte de validation, pas de Fo, pas de S baissière.
- **Vos positions (⭐)** : bouton « J'ai cette action » dans la fiche. L'action passe en haut (bloc « Mes positions ») avec **toutes les unités de temps, 1h comprise**. On y voit aussi **Fo** et **S baissière** (🟥 : sans corps après au moins 3 bougies vertes, toutes vertes, dont au moins 2 sans mèche sous le corps), validés et en cours. Seuils : **50 % pour le 1h**, 30 % pour les autres unités. Les notifications sont ⭐, en priorité haute (maximale pour Fo et S baissière), et vous gardez aussi les alertes de validation (Fa, Fo, S, toutes unités). Cela demande l'étape 7.
- Fa et Fo durent : un Fa reste en place jusqu'à l'apparition d'un Fo (le **bas du corps** sous la Tenkan et une **MM7 descendante**), et inversement. Une MM7 plate ou de mauvaise pente ne valide ni l'un ni l'autre ; une bougie ne peut donc plus être Fa et Fo à la fois.
- **Recherche** : la barre de recherche retrouve n'importe quelle action suivie (nom ou ticker), même sans configuration en cours. Pour une action absente de la liste, tapez son code Yahoo (ex. `ALCLS.PA` ; sans suffixe, `.PA` est ajouté) puis « J'ai cette action : l'ajouter à mes positions ». Elle est analysée dès le prochain passage, comme les autres, et apparaît dans « Mes positions ». Seules les actions d'Euronext Paris sont prises en charge. Cela demande l'étape 7 pour les notifications.
- Toucher la notification ouvre la fiche de l'action, sur l'unité de temps de l'alerte. La fiche propose deux boutons graphique : **TradingView** (ouvre le graphique à l'unité de temps de l'onglet : 4h → 4 heures) et **Investing.com** (page de l'action ; l'unité se règle sur place, voir `docs/investing.json`).
- Univers : une centaine de valeurs de Paris (`universe.csv`), filtrées sur la liquidité (au moins 100 k€ échangés par jour en moyenne).

## 1. Notifications sur le téléphone (ntfy)

1. Installez l'application **ntfy** (App Store ou Google Play).
2. Dans l'application : « + » puis abonnez-vous au sujet (topic) : `fa-fo-df1f1252899e`
   (ce nom aléatoire sert de mot de passe : ne le partagez pas ; vous pouvez en choisir un autre, long et illisible).
3. Autorisez les notifications quand le téléphone le demande.

## 2. Mettre le projet sur GitHub

1. Créez un compte sur github.com si besoin, puis **New repository** : nom `fa-fo-scanner`, visibilité **Public** (GitHub Pages est gratuit uniquement sur un dépôt public ; il n'y a aucun secret dans le code).
2. Dans le dépôt : **Add file > Upload files**, puis glissez **tout le contenu** du dossier décompressé (y compris les dossiers `docs`, `.github` et `tests`). Validez avec **Commit changes**.
   - Si le dossier caché `.github` n'apparaît pas dans votre explorateur, affichez les fichiers cachés (Mac : Cmd+Maj+. ; Windows : Affichage > Éléments masqués).
   - Vérifiez ensuite que le fichier `.github/workflows/scan.yml` est bien présent dans le dépôt.

## 3. Brancher les notifications

Dépôt > **Settings > Secrets and variables > Actions > New repository secret**
- Name : `NTFY_TOPIC`
- Secret : `fa-fo-df1f1252899e` (le sujet choisi à l'étape 1)

## 4. Publier l'application

Dépôt > **Settings > Pages** > Source : **Deploy from a branch** > Branch : `main`, dossier `/docs` > **Save**.
Au bout d'une minute, l'adresse est `https://VOTRE-PSEUDO.github.io/fa-fo-scanner/`.

## 5. Premier lancement

1. Dépôt > onglet **Actions** > « Scan Fa/Fo » > **Run workflow**. (Si GitHub demande d'activer les workflows, acceptez.)
2. Le premier passage mémorise l'état actuel **sans envoyer de notification** (pour ne pas vous inonder). Les alertes arrivent ensuite à chaque nouveau signal.
3. Consultez le journal du passage : il indique « N actions analysées » et liste les tickers sans données. Corrigez `universe.csv` si besoin.
4. Test des notifications : depuis un ordinateur, `curl -d "test" ntfy.sh/fa-fo-df1f1252899e`, ou dans l'application ntfy, menu du sujet > « Send test notification ».

## 6. Installer l'application sur le téléphone

Ouvrez l'adresse de l'étape 4 dans Safari (iPhone) ou Chrome (Android), puis **Partager > Sur l'écran d'accueil** (iPhone) ou **Installer l'application** (Android).
Les notifications passent par l'application ntfy ; le toucher d'une notification ouvre la page de l'action dans le navigateur (si l'application est installée, ouvrez-la ensuite depuis l'icône).

## 7. Activer les positions (jeton GitHub, une seule fois)

Le scanner tourne sur GitHub : pour qu'il sache quelles actions vous détenez, l'application met à jour une **variable privée** nommée `HOLDINGS` dans votre dépôt. Il lui faut un jeton limité à cette seule tâche.

1. GitHub > votre photo > **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**.
2. Nom : `fa-fo-positions` ; expiration : 1 an ; **Repository access : Only select repositories** > `fa-fo-scanner`.
3. **Permissions > Repository permissions > Variables : Read and write** (rien d'autre). Générez, puis copiez le jeton (il commence par `github_pat_`, il ne s'affiche qu'une fois).
4. Dans l'application : ⚙ (Réglages) > collez le jeton (le dépôt est rempli automatiquement) > **Enregistrer et synchroniser**.
5. Ouvrez une action > **J'ai cette action**. La position est prise en compte au prochain passage (30 minutes au plus (15 pour vos positions)).

Le jeton reste sur ce téléphone (stockage du navigateur) ; ne le partagez pas. Il ne peut modifier que les variables de ce dépôt, pas le code. Sans jeton, le bouton fonctionne mais seulement sur ce téléphone (tri et affichage), sans notifications spéciales. Vous pouvez aussi renseigner la variable à la main : dépôt > Settings > Secrets and variables > Actions > **Variables** > `HOLDINGS` = `AIR.PA,MC.PA`.

## Réglages (en haut de `scanner.py`)

| Réglage | Valeur actuelle | Rôle |
|---|---|---|
| `BODY_MODE` | `"edge"` | `"edge"` : haut du corps pour Fa, bas du corps pour Fo ; `"full"` : corps entier au-delà des deux lignes ; `"close"` : clôture seule |
| `INDICATORS_ON` | `"ha"` | MM7/Tenkan calculées sur les bougies HA (comme sur un graphique HA) ; `"normal"` : sur bougies classiques |
| `PROB_MIN` | 0.30 | seuil (en cours) pour afficher et notifier une action non détenue, et pour les unités ≥ 4h des positions |
| `PROB_HIGH` | 0.60 | seuil de la 2e notification (une seule par bougie) |
| `PROB_MIN_HELD_1H` | 0.50 | seuil du 1h, réservé aux actions détenues |
| `VISIBLE_TF` | 4h, 1j, 1sem, 1mois | unités de temps affichées et notifiées pour les actions non détenues (ajoutez `"1h"` pour le remettre) |
| `NOTIFY_PENDING` | Fa et S haussière | notifications « en cours » des actions non détenues |
| `NOTIFY_PENDING_HELD` | les 4 configurations, toutes unités | notifications « en cours » des actions détenues |
| `NOTIFY_HELD` | les 4 configurations, toutes unités | notifications de validation (bougie clôturée) des actions détenues |
| `S_MIN_RUN` | 3 | bougies HA de même sens minimales avant la bougie sans corps |
| `S_WICK_MAX` | 0.10 | mèche tolérée sur les bougies précédant la S (haute pour S haussière, basse pour S baissière), en part de leur hauteur |
| `S_WICK_MIN` | 2 | dans toute la série de bougies de même sens, nombre minimal de bougies sans mèche (les autres peuvent en avoir ; elles doivent toutes rester de même sens) |
| `AUTO_UNIVERSE` | True | ajoute à chaque passage toutes les actions de Paris listées par Yahoo ; `False` : seulement `universe.csv` |
| `MA_SLOPE` | True | Fa exige une MM7 ascendante, Fo une MM7 descendante ; `False` supprime la condition de pente |
| `S_BODY_MAX` | 0.10 | « sans corps » : corps ≤ 10 % de la hauteur de la bougie |
| `MIN_TURNOVER_EUR` | 100 000 | liquidité minimale (€ échangés par jour) |
| `MAX_PUSH_PER_RUN` | 30 | au-delà, une notification récapitulative |

Pour ajouter ou retirer une action : modifiez `universe.csv` (une ligne `TICKER.PA,Nom`).

## Bon à savoir

- **Source des cours : Yahoo Finance, pas Investing.com.** Investing.com n'offre pas d'accès automatique autorisé et gratuit à ses cours ; un GitHub Actions y serait de plus bloqué. Le retard d'environ 15 minutes vient de la diffusion gratuite d'Euronext. Un vrai temps réel passe par une licence Euronext, via un fournisseur payant.
- **Bouton Investing.com** : les adresses de ses pages ne se déduisent pas du ticker (Airbus = `eads`). Pour qu'une action ouvre directement sa page, ajoutez une ligne dans `docs/investing.json`, par exemple `"AIR.PA": "eads"` (le texte entre `/equities/` et `-chart` dans l'adresse de la page). Sans ligne, le bouton lance une recherche Investing.com sur le nom de l'action.

- **Pas de couleur dans les notifications** : ntfy n'affiche pas de texte coloré. Le vert et le rouge sont donc des pastilles dans le titre : 🟢 / 🔴 pour Fa / Fo, carrés 🟩 / 🟥 pour S haussière / baissière ; l'application, elle, est colorée.
- **Délais** : cours Yahoo Finance différés d'environ 15 min. Les exécutions programmées de GitHub peuvent aussi prendre quelques minutes de retard aux heures chargées. Comptez donc 15 à 45 min entre un événement et la notification.
- **Bougies jour, semaine, mois** : validées au passage de 17h55 (après la clôture). Pendant la séance, la bougie en formation apparaît dans les onglets Fa et S avec son pourcentage ; une fois clôturée, une Fa validée quitte l'onglet.
- **Le % « en cours »** est une estimation statistique (simulation sur le temps restant de la bougie, avec la volatilité récente), pas une prévision.
- **Limite ntfy.sh** : environ 250 notifications par jour sur le serveur gratuit. Avec les 5 unités de temps, une journée très active peut s'en approcher ; au-delà, vous recevez un récapitulatif. Pour moins de bruit, retirez des unités de temps ou relevez `MIN_TURNOVER_EUR`.
- **Données Yahoo** : gratuites mais non garanties ; vérifiez toujours le signal sur votre graphique avant d'agir. Ce n'est pas un conseil en investissement.
- Les tickers de `universe.csv` ont été saisis de mémoire : ceux que Yahoo ne reconnaît pas sont listés dans le journal du scanner.
- GitHub désactive les exécutions programmées si le dépôt reste sans activité pendant 60 jours ; ici, chaque passage enregistre un fichier, ce qui compte comme activité.

## Tester sur votre ordinateur (facultatif)

```
pip install -r requirements.txt
python tests/test_logic.py        # tests hors ligne
python scanner.py --no-push       # vrai scan, écrit docs/data.json, aucune notification
```
