# tapo-web

[English version](README.md)

Une petite appli web auto-hébergée pour les caméras TP-Link Tapo : le direct, un navigateur pour
les vidéos de la carte SD, et une détection automatique des animaux. Au départ je voulais juste
savoir qui vidait la gamelle du chat à 3 h du matin.

C'était une fouine.

![une fouine sur le toit, de nuit](docs/img/marten.jpg)

Tout tourne sur mon PC et parle à la caméra par le réseau local. Pas de cloud, pas d'abonnement,
rien ne sort de la maison.

## Ce que ça fait

**Onglet Animaux.** Chaque clip enregistré par la caméra est téléchargé et analysé en tâche de
fond. À gauche on choisit une espèce, à droite on a toutes les vidéos correspondantes, tous jours
confondus, avec une image d'aperçu où l'animal est encadré. Un clic sur une étiquette lance la
vidéo au moment où l'animal apparaît. Les vidéos sont gardées sur le disque, donc elles restent
disponibles bien après que la carte SD a bouclé dessus.

![onglet animaux](docs/img/animals.png)

**Onglet Carte SD.** Calendrier des jours enregistrés, frise sur 24 h, une ligne par clip avec la
vignette fournie par la caméra. La lecture démarre au bout d'une ou deux secondes pendant que la
suite arrive (la caméra envoie à environ 10 fois le temps réel). Le téléchargement donne un MP4
normal, H.264 d'origine, sans ré-encodage.

![onglet carte sd](docs/img/sdcard.png)

**Onglet Live.** Le direct dans le navigateur (HLS), bascule HD/SD, photo, enregistrement à la
demande, DVR continu par tranches de 10 minutes, et pilotage des modèles motorisés.

![onglet live](docs/img/live.png)

## Pourquoi ce projet

Après une mise à jour du firmware, ma C510W (fw 1.3.4) a cessé de répondre à pytapo, python-kasa
et à l'intégration Home Assistant : `error_code -40211` à chaque connexion. La caméra était passée
à un nouveau protocole local (poignée de main SPAKE2+, puis un canal chiffré AES-CCM) que personne
n'avait documenté. Je l'ai reconstitué et tout écrit ici :
**[tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol)**. L'appli est construite dessus.

## La détection d'animaux

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

Sur une vieille GTX 1050, un clip d'une minute prend environ 16 secondes (moyenne sur 700 clips),
une journée complète est donc traitée en moins de 20 minutes. Toute la chaîne, le pourquoi de
chaque étape et les chiffres sont dans [docs/DETECTION.md](docs/DETECTION.md) (en anglais).

Sur les 697 premiers clips : 418 sans rien du tout, 180 avec notre chien, et 5 avec la fouine.
Voilà à quoi ça sert.

Le modèle tourne dans son propre processus et son propre environnement Python, et s'arrête quand
il n'a rien à faire. L'appli web reste légère. Ça marche aussi sur CPU, en plus lent.

## Vitesse

Les clips sortent de la carte SD à environ 10 fois le temps réel (un clip de 66 s en 7 s avec mon
Wi-Fi) et la lecture démarre au bout de 2 ou 3 secondes. Les outils basés sur l'ancienne requête
`playback` récupèrent le même clip à vitesse 1, donc une minute de vidéo prend une minute. La
différence vient de la requête `download` du port média de la caméra, voir le dépôt du protocole.

## Installation

Il faut Python 3.12, ffmpeg et [uv](https://github.com/astral-sh/uv) (ou adaptez les commandes à pip).

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
./start.sh            # puis ouvrir http://localhost:8088
```

Au premier lancement l'appli demande les identifiants de la caméra dans le navigateur (roue
dentée en haut à droite) :

* le **compte caméra** créé dans l'appli Tapo (Paramètres avancés, Compte de la caméra). Il sert pour le direct et ONVIF.
* le **mot de passe de votre compte TP-Link**. L'API de contrôle locale et le port média le réclament. Il n'est envoyé qu'à votre caméra.

Ils sont enregistrés dans un fichier `.env` local, lisible par vous seul, et ignoré par git. Les
réglages ne se modifient que depuis la machine qui fait tourner l'appli.

Pour la détection d'animaux (facultatif, environ 8 Go avec PyTorch et les poids du modèle) :

```bash
./ml/setup.sh
```

Ensuite ça tourne tout seul. Les nouveaux clips sont pris en compte toutes les 10 minutes, et tout
l'historique de la carte est traité petit à petit, du plus récent au plus ancien. La lecture passe
toujours avant le travail de fond, et une seule connexion à la fois est ouverte vers la caméra,
elle n'en supporte pas plus.

Réglages utiles dans `.env` : `TAPO_DATA_DIR` (mettre les vidéos sur un autre disque),
`TAPO_ANALYZE_KEEP` (`all`, `animals` ou `none`), `TAPO_ANALYZE_AUTO=0` pour n'analyser qu'à la demande.

## Remarques

* Testé avec une Tapo C510W, firmware 1.3.4. Les autres Tapo récentes devraient marcher pareil. Dites-moi si c'est le cas chez vous, ou pas.
* Tourne très bien sous WSL2. Si WSL s'endort quand on ferme le terminal, lancez l'appli depuis une tâche planifiée Windows.
* `python -m pytest tests -q` lance les tests hors ligne (fausse caméra, faux port média).
* L'appli n'écrit et ne supprime jamais rien sur la carte SD.

Mots-clés : caméra Tapo C510W, C500, C210, C220, C100, C310, API locale TP-Link Tapo, erreur -40211,
télécharger les vidéos de la carte SD Tapo sur PC, Tapo sans cloud, Tapo Home Assistant, caméra
de surveillance animaux jardin, piège photo, détection fouine renard hérisson chat, DeepFaune.

Licence MIT. Sans lien avec TP-Link. DeepFaune a sa propre licence (CeCILL, CC BY-SA pour les
poids) et est téléchargé par le script d'installation, il n'est pas inclus ici.
