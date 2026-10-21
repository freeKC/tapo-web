# tapo-web

[![tests](https://github.com/freeKC/tapo-web/actions/workflows/tests.yml/badge.svg)](https://github.com/freeKC/tapo-web/actions/workflows/tests.yml)
[![version](https://img.shields.io/github/v/release/freeKC/tapo-web)](https://github.com/freeKC/tapo-web/releases)
[![licence : MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
![Interface en anglais, français, allemand, espagnol, polonais](https://img.shields.io/badge/interface-EN%20%C2%B7%20FR%20%C2%B7%20DE%20%C2%B7%20ES%20%C2%B7%20PL-informational)

[English version](README.md)

**Vos caméras TP-Link Tapo dans le navigateur : le direct, les vidéos de leur carte SD, et une
détection automatique des animaux. Tout reste sur votre réseau. Pas de cloud, pas de compte, pas
d'abonnement.**

Au départ je voulais juste savoir qui vidait la gamelle du chat à 3 h du matin.

C'était une fouine.

![une fouine sur le toit, de nuit](docs/img/marten.jpg)

## Ce que ça fait

- **Toutes les caméras en direct**, une par une ou toutes sur le même écran, en HD ou en SD, avec
  photo, enregistrement à la demande et DVR continu.
- **La carte SD comme une vidéothèque.** Calendrier des jours enregistrés, frise sur 24 h, la
  vignette de la caméra pour chaque clip. La lecture démarre au bout de deux secondes, et le
  téléchargement va à environ 10 fois le temps réel, en MP4 normal, H.264 d'origine, sans
  ré-encodage.
- **La détection d'animaux** sur chaque clip, en tâche de fond, avec
  [DeepFaune](https://www.deepfaune.cnrs.fr). On choisit une espèce et on a toutes ses vidéos,
  tous jours confondus, avec une image où l'animal est encadré. Les clips sont gardés sur le
  disque, donc ils restent là bien après que la carte a bouclé dessus.
- **Les réglages de la caméra** sans l'appli Tapo : mode privé, voyant lumineux, projecteur,
  suivi automatique, détection de mouvement et de personne avec leur sensibilité, vision
  nocturne, orientation avec positions mémorisées. Chaque caméra ne montre que ce qu'elle sait
  faire.
- **Une installation qui trouve vos caméras.** Elle balaie le réseau, dit quel modèle et quel
  firmware elle a trouvés, explique les deux comptes que la caméra demande, et teste la connexion.
- **Compatible avec les firmwares récents** qui répondent `-40211` à pytapo, python-kasa et Home
  Assistant. L'appli parle la nouvelle connexion (V4, SPAKE2+) et l'ancienne, et choisit toute
  seule la bonne.
- Tourne sous **Linux, WSL, Windows ou Docker**. L'interface existe en français, anglais,
  allemand, espagnol et polonais.

## Face aux autres solutions

| | tapo-web | Appli Tapo | Home Assistant + Tapo Control | Frigate |
|---|:-:|:-:|:-:|:-:|
| Direct dans un navigateur, toutes les caméras sur un écran | ✅ | ❌ téléphone seulement | ✅ | ✅ |
| Carte SD par jour, avec frise et vignettes | ✅ | ✅ sur le téléphone | ➖ sans frise | ❌ |
| Clip SD lancé en quelques secondes | ✅ | ✅ | ➖ une fois téléchargé | ❌ |
| Clips SD gardés en MP4 sur l'ordinateur quand la carte tourne | ✅ | ➖ un par un, sur le téléphone | ➖ effacés quand la carte les efface | ❌ |
| Espèces d'animaux : renard, fouine, blaireau, hérisson, chevreuil... | ✅ | ❌ | ❌ | ➖ quelques-unes, avec Frigate+ (payant) |
| Réglages de la caméra (mode privé, LED, projecteur, détection, PTZ...) | ✅ | ✅ | ✅ encore plus | ➖ pan et tilt |
| Alertes en temps réel et automatisations | ❌ | ✅ | ✅ | ✅ |
| Enregistrement 24 h/24 sur un disque | ➖ basique | ❌ | ❌ | ✅ |
| Accessible hors de chez soi | ❌ | ✅ | ✅ | ✅ |
| Tourne sur | Linux, Windows, Docker | Android, iOS | Home Assistant | Docker |

tapo-web est donc l'outil pour la carte SD et pour savoir quels animaux sont passés la nuit. Pour
recevoir une notification quand quelqu'un remonte l'allée, Home Assistant ou Frigate font mieux,
et rien n'empêche de les faire tourner à côté. Chaque case est expliquée, avec sa source, dans
[docs/COMPARISON.md](docs/COMPARISON.md) (en anglais).

## Démarrage rapide

Il faut Python 3.12 et ffmpeg. Ensuite, au choix :

**Linux ou WSL** (avec [uv](https://github.com/astral-sh/uv), ou pip)

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
./start.sh            # puis ouvrir http://localhost:8088
```

**Windows**

```powershell
git clone https://github.com/freeKC/tapo-web; cd tapo-web
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
.\start.bat           # lance le serveur et ouvre le navigateur
```

**Docker**

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
mkdir -p config data && docker compose up -d --build
```

Les détails, Docker Desktop compris, sont dans [docs/DOCKER.md](docs/DOCKER.md) (en anglais).

Au premier lancement, les réglages s'ouvrent tout seuls et cherchent les caméras sur le réseau.
Il vous faut trois choses de l'appli Tapo :

1. le **compte de la caméra** : dans l'appli Tapo, la caméra, puis ⚙, Paramètres avancés, Compte
   de la caméra. Il sert au direct, aux enregistrements et à l'orientation ;
2. le **mot de passe de votre compte TP-Link** (celui avec lequel vous vous connectez à l'appli
   Tapo). La caméra le demande pour sa carte SD ;
3. la **Compatibilité tierce** activée : Moi, Tapo Lab, Compatibilité tierce. Sans elle, les
   firmwares récents refusent les connexions locales.

Tout est gardé dans `cameras.json` sur votre machine, lisible par vous seul, et n'est envoyé qu'à
vos caméras. Les réglages ne se modifient que depuis la machine qui fait tourner l'appli (ou les
adresses listées dans `TAPO_ADMIN_HOSTS`).

<p align="center">
  <img src="docs/img/setup.png" width="420" alt="Les réglages au premier lancement : un mot de bienvenue, les caméras trouvées sur le réseau avec leur modèle, leur adresse et leur firmware, un bouton Ajouter pour chacune, et le mot de passe du compte TP-Link">
  <img src="docs/img/settings.png" width="270" alt="Les flèches d'orientation avec Mémoriser la position, puis les réglages de la caméra : mode privé, voyant lumineux, projecteur, suivi automatique, détection de mouvement et de personne avec une sensibilité, vision nocturne">
</p>

## La carte SD

Un calendrier des jours enregistrés, une frise sur 24 h et une ligne par clip avec la vignette
fournie par la caméra. Des filtres selon ce que la caméra ou la détection a vu (animaux, une
espèce, personnes, véhicules). La lecture démarre pendant que la suite du clip arrive. Le
téléchargement donne un MP4 tout simple.

![onglet carte SD](docs/img/sdcard.png)

L'appli n'écrit et ne supprime jamais rien sur la carte.

## La détection d'animaux

![onglet animaux](docs/img/animals.png)

Elle utilise [DeepFaune](https://www.deepfaune.cnrs.fr) (CNRS), un modèle entraîné sur des pièges
photo de faune européenne : renard, fouine et autres mustélidés, blaireau, hérisson, chat, chien,
chevreuil, sanglier, oiseaux... Un détecteur YOLO trouve l'animal, un classifieur ViT le nomme.
Il se débrouille bien en infrarouge, et c'est la nuit que passent les visiteurs intéressants.

Ce que j'ai dû ajouter pour que ce soit utilisable sur une caméra de jardin :

* les premières secondes du clip sont échantillonnées serré, parce que l'animal qui a déclenché
  l'enregistrement est souvent reparti au bout de trois secondes
* ce qui reste exactement au même endroit image après image est écarté (un coin sombre de mon
  allée était un "oiseau" toutes les nuits), sauf si le classifieur est très sûr de lui : un chat
  assis sans bouger sur le muret reste un chat
* les espèces qui n'ont aucun sens ici (le modèle a vu une vache sur ma terrasse) retombent sur une
  simple étiquette "animal"
* une étiquette fausse se retire à la main, et le reste après une nouvelle analyse

Sur une vieille GTX 1050, un clip d'une minute prend environ 16 secondes, une journée complète est
donc traitée en moins de 20 minutes. Ça marche aussi sur CPU, en plus lent. Sur mes 697 premiers
clips : 418 sans rien du tout, 180 avec notre chien, et 5 avec la fouine. Voilà à quoi ça sert.

C'est facultatif (environ 8 Go avec PyTorch et les poids du modèle), et pour l'instant sous Linux
seulement :

```bash
./ml/setup.sh
```

Le modèle tourne dans son propre processus et son propre environnement Python, et s'arrête quand
il n'a rien à faire. La lecture passe toujours avant le travail de fond. Toute la chaîne et les
chiffres sont dans [docs/DETECTION.md](docs/DETECTION.md) (en anglais).

## Quelles caméras ?

| | Modèles |
|---|---|
| ✅ **Testées** | **C510W** (firmware 1.3.4, nouvelle connexion) : tout fonctionne. **C520WS** (1.3.5, ancienne connexion) : carte SD vérifiée par un contributeur. **C200** hw 5.0 : ses particularités de connexion sont gérées, d'après un retour. |
| 🟢 **Devraient marcher**, intérieur | C100, C101, C110, C111, C125, C840, TC60. Motorisées : C200, C201, C202, C210, C211, C212, C21A, C220, C222, C225, C230, C232, C250, C260, C825, TC70, TC71, TC72, TCA72, TC73, TC74, TCB72 |
| 🟢 **Devraient marcher**, extérieur | C120, C310, C320WS, C325WB, TC65, TCW30. Motorisées : C206, C207, C216, C217, C236, C245D, C246D, C500, C51A, C52A, C530WS, C545D, C550WS, C560WS, C575D, TC34, TCW34, TC40, TC41, TCA41, TC42, TC43, TC46, TC47. Projecteurs : C710, C720, TC53, TC55. Sonnette : D130 |
| 🟡 **En partie** | Sonnettes D225, D235, TD25 (seulement câblées, en mode Always-on). C501GW, TC40GW (pan et tilt seulement en Ethernet) |
| ❔ **Inconnu** | C103, C104, C113, C121, TCW61, C566WB, D235L, TD25L, D260 : TP-Link ne dit pas si elles ont le RTSP |
| ❌ **Pas de direct** | Caméras sur batterie ou solaires : C400, C401, C402, C403, C410, C411, C425, C425K, C460, C465, C610, C615F, C615G, C630, C645D, C660, C665G, C675D, TC50, TCH50, TC80, TC82, TCB82, TC85, TC90, TCW90, TC90G, TC92, TC93D, et les sonnettes D205, TD20, D210, TD21, TDB21. Derrière le hub H200 : C420, C400S2, D230, TD23. La carte SD peut quand même marcher sur celles qui sont en Wi-Fi |

« Devraient marcher » veut dire : sur secteur, avec le RTSP, une carte microSD et la même API
locale que les modèles testés. Elles n'ont pas été essayées ici. Le détail par modèle (pan et
tilt, éclairage, modèles à deux objectifs, firmwares avec la nouvelle connexion) est dans
[docs/CAMERAS.md](docs/CAMERAS.md) (en anglais).

Si vous l'essayez avec une caméra, [dites-moi comment ça s'est passé](https://github.com/freeKC/tapo-web/issues/new?template=camera-report.yml),
en bien ou en mal : les réglages ont un bouton « Copier le diagnostic » qui donne tout ce qu'il me
faut, sans aucune adresse ni mot de passe. Chaque retour fait passer un modèle en ✅.

## Sur un téléphone, une télé ou un casque

Mon autre appli, **[Immuch360](https://github.com/freeKC/Immuch360)**, se connecte elle aussi aux
caméras Tapo. Elle les affiche en direct sur Android, Android TV, le Meta Quest et Windows, et
liste les enregistrements de la carte SD jour par jour sur toutes les plateformes. Un clip se lit
comme n'importe quelle vidéo et peut être envoyé sur votre serveur Immich. Même principe : elle ne
parle qu'à la caméra, sur votre réseau, jamais aux serveurs de TP-Link.

## Quand ça ne marche pas

* **« Mot de passe du compte TP-Link refusé »** dans le test de connexion : vérifiez le mot de
  passe et activez la Compatibilité tierce dans l'appli Tapo. N'essayez pas plusieurs mots de
  passe à la suite : après quelques erreurs, la caméra bloque les connexions pendant une demi-heure
  environ.
* **La caméra n'est pas trouvée** : vérifiez le sous-réseau dans les réglages (les trois premiers
  nombres de vos adresses, par exemple 192.168.1), ou indiquez l'adresse IP de la caméra. Sous WSL,
  l'appli balaie le vrai réseau local et pas celui de WSL, d'où l'importance du sous-réseau.
* **Pas de direct, mais la carte SD fonctionne** : d'après TP-Link, une caméra ne fait tourner que
  deux choses parmi Tapo Care, l'enregistrement local et le RTSP. Avec un abonnement Tapo Care et
  l'enregistrement sur carte SD, la caméra coupe le RTSP. Les caméras sur batterie n'ont pas de
  RTSP du tout.
* **« Caméra occupée »** en lisant un clip : la caméra ne sert qu'une session vidéo à la fois, et
  l'appli Tapo était sans doute ouverte dessus. Fermez-la et réessayez.
* **`-40211` avec d'autres outils** : c'est la nouvelle connexion V4. Son fonctionnement est décrit
  dans [tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol), et pytapo la gère depuis la
  version 3.4.20.

## Pourquoi ce projet

Après une mise à jour du firmware, ma C510W a cessé de répondre à pytapo, python-kasa et à
l'intégration Home Assistant : `error_code -40211` à chaque connexion. La caméra était passée à un
nouveau protocole local (poignée de main SPAKE2+, puis un canal chiffré AES-CCM) que personne
n'avait documenté. Je l'ai reconstitué et tout écrit dans
**[tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol)**. L'appli est construite dessus,
et ce travail a été repris dans pytapo, python-kasa et l'intégration Tapo Control pour Home
Assistant.

Les clips sortent de la carte SD à environ 10 fois le temps réel (un clip de 66 s en 7 s avec mon
Wi-Fi). Les outils basés sur l'ancienne requête `playback` récupèrent le même clip à vitesse 1,
donc une minute de vidéo prend une minute. La différence vient de la requête `download` du port
média de la caméra, que pytapo et Tapo Control pour Home Assistant utilisent aussi désormais.

## Contribuer

Signalements de bugs, retours sur une caméra, traductions et code sont les bienvenus. Le point de
départ est [CONTRIBUTING.md](CONTRIBUTING.md) (en anglais) : lancer les tests (ils n'ont besoin
d'aucune caméra), les règles pour tester sur une vraie, et comment ajouter une langue. Merci à
[@uzior](https://github.com/uzior) pour Windows, l'ancienne connexion et l'interface en polonais.

Réglages utiles dans `.env` (voir `.env.example`) : `TAPO_DATA_DIR` pour mettre les vidéos sur un
autre disque, `TAPO_ANALYZE_KEEP` (`all`, `animals` ou `none`), `TAPO_ANALYZE_AUTO=0` pour
n'analyser qu'à la demande.

## Remarques

* Tourne très bien sous WSL2. Si WSL s'endort quand on ferme le terminal, lancez l'appli depuis
  une tâche planifiée Windows.
* Il n'y a pas de mot de passe sur l'interface web : quiconque atteint le port peut regarder les
  caméras. C'est pour ça que `start.sh` et `start.bat` n'écoutent que sur 127.0.0.1.
* Sans lien avec TP-Link.

Mots-clés : caméra Tapo C510W, C500, C520WS, C200, C210, C220, C100, C110, C310, API locale TP-Link
Tapo, erreur -40211, télécharger les vidéos de la carte SD Tapo sur PC, Tapo sans cloud, plusieurs
caméras Tapo dans le navigateur, Tapo Home Assistant, caméra de surveillance animaux jardin, piège
photo, détection fouine renard hérisson chat, DeepFaune, Docker.

Licence MIT. DeepFaune a sa propre licence (CeCILL, CC BY-SA pour les poids) et est téléchargé par
le script d'installation, il n'est pas inclus ici.
