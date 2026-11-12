# Fa·Fo Scanner : mise en route (environ 15 minutes, gratuit)

Le scanner tourne tout seul dans le cloud (GitHub Actions), toutes les 15 minutes pendant la séance de Paris.
Il envoie les notifications sur votre téléphone (application ntfy) et met à jour une page installable (l'application avec les onglets).

## Ce que ça fait

- **Fa1h · AIR** (🟢) : le **haut du corps** d'une bougie Heikin Ashi 1h clôturée dépasse la MM7 et la Tenkan. La configuration **dure** jusqu'au Fo.
- **Fo1h · AIR** (🔴) : le **bas du corps** d'une bougie clôturée passe sous la MM7 et la Tenkan : c'est l'invalidation du Fa, et le Fo dure à son tour jusqu'au prochain Fa. Entre les deux signaux, l'état ne change pas. Si une même bougie remplit les deux conditions (corps qui couvre toute la zone entre les lignes), l'état précédent continue.
- **S haussière** (🟩) : une bougie Heikin Ashi **sans corps** (corps ≤ 10 % de sa hauteur) apparaît après **au moins 3 bougies HA baissières** consécutives. Notifications en 4h, 1j, 1sem, 1mois. Exemple : « 🟩 S4h · AIR ».
- **S baissière** (🟥) : même bougie sans corps, mais après **au moins 3 bougies HA vertes** consécutives. Visible dans l'application pour toutes les actions ; **notifiée uniquement pour vos positions**. Pas de condition sur la MM7 ou la Tenkan pour S.
- Pour les actions non détenues, Fa et Fo ne notifient qu'en 1j, 1sem, 1mois ; le 1h et le 4h (reconstruit : 9h-13h puis 13h-17h35) restent consultables dans l'application. Les réglages sont dans `NOTIFY` en haut de `scanner.py`.
- **Vos positions (⭐)** : dans la fiche d'une action, le bouton « J'ai cette action » la met en position. Elle apparaît alors **en premier** dans l'application (bloc « Mes positions » en haut, étoile dans toutes les listes) et elle est toujours analysée, même si elle sort de la liste ou passe sous le seuil de liquidité. Ses notifications sont **différentes** : titre précédé de ⭐, ligne « POSITION DÉTENUE », priorité haute (priorité maximale pour Fo et S baissière) et **toutes les unités de temps** (1h, 4h, 1j, 1sem, 1mois) pour Fa, Fo, S haussière et S baissière. Cela demande l'étape 7.
- **Affichage simplifié** : chaque action n'affiche que son nom, son prix et une puce par unité de temps (1h, 4h, 1j, 1sem, 1mois) : rond vert = Fa, rond rouge = Fo, rond vide = neutre ; carré vert = S haussière, carré rouge = S baissière. En touchant l'action : l'état détaillé de chaque unité de temps et les **probabilités à la clôture de la bougie en cours** (Fa, Fo, S haussière, S baissière).
- Toucher la notification ouvre l'état des lieux de l'action (toutes les unités de temps).
- L'application a deux rangées d'onglets : unité de temps, puis Fa (validées) / En cours (avec un %) / Fo (invalidées) / S.
- Univers : une centaine de valeurs de Paris (`universe.csv`), filtrées automatiquement sur la liquidité (au moins 2 M€ échangés par jour en moyenne).

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
5. Ouvrez une action > **J'ai cette action**. La position est prise en compte au prochain passage (15 minutes au plus).

Le jeton reste sur ce téléphone (stockage du navigateur) ; ne le partagez pas. Il ne peut modifier que les variables de ce dépôt, pas le code. Sans jeton, le bouton fonctionne mais seulement sur ce téléphone (tri et affichage), sans notifications spéciales. Vous pouvez aussi renseigner la variable à la main : dépôt > Settings > Secrets and variables > Actions > **Variables** > `HOLDINGS` = `AIR.PA,MC.PA`.

## Réglages (en haut de `scanner.py`)

| Réglage | Valeur actuelle | Rôle |
|---|---|---|
| `BODY_MODE` | `"edge"` | `"edge"` : haut du corps pour Fa, bas du corps pour Fo ; `"full"` : corps entier au-delà des deux lignes ; `"close"` : clôture seule |
| `INDICATORS_ON` | `"ha"` | MM7/Tenkan calculées sur les bougies HA (comme sur un graphique HA) ; `"normal"` : sur bougies classiques |
| `NOTIFY` | Fa, Fo : 1j, 1sem, 1mois ; S haussière : 4h, 1j, 1sem, 1mois ; S baissière : aucune | notifications des actions non détenues, par configuration |
| `NOTIFY_HELD` | toutes les unités de temps, pour les 4 configurations | notifications des actions détenues |
| `S_MIN_RUN` | 3 | bougies HA de même sens minimales avant la bougie sans corps |
| `S_BODY_MAX` | 0.10 | « sans corps » : corps ≤ 10 % de la hauteur de la bougie |
| `MIN_TURNOVER_EUR` | 2 000 000 | liquidité minimale (€ échangés par jour) |
| `PROB_MIN` | 0.20 | % minimal pour apparaître dans « En cours » |
| `MAX_PUSH_PER_RUN` | 30 | au-delà, une notification récapitulative |

Pour ajouter ou retirer une action : modifiez `universe.csv` (une ligne `TICKER.PA,Nom`).

## Bon à savoir

- **Pas de couleur dans les notifications** : ntfy n'affiche pas de texte coloré. Le vert et le rouge sont donc des pastilles dans le titre : 🟢 / 🔴 pour Fa / Fo, carrés 🟩 / 🟥 pour S haussière / baissière ; l'application, elle, est colorée.
- **Délais** : cours Yahoo Finance différés d'environ 15 min. Les exécutions programmées de GitHub peuvent aussi prendre quelques minutes de retard aux heures chargées. Comptez donc 15 à 30 min entre un événement et la notification.
- **Bougies jour, semaine, mois** : validées au passage de 17h55 (après la clôture). Pendant la séance, elles apparaissent dans « En cours » avec un pourcentage.
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
