# Guide de configuration Google Cloud Console

**Projet :** Billets & Vinted Monitor MVP
**Date :** 2026-02-25
**Projet Google Cloud ID :** 367559837862

Ce guide explique pas a pas comment configurer Google Cloud Console pour que l'application puisse utiliser Google OAuth 2.0 (connexion), Gmail API (lecture des emails), Google Sheets API (ecriture des commandes) et Google Drive API (creation de fichiers).

---

## Table des matieres

1. [Projet Google Cloud](#1-projet-google-cloud)
2. [Activer les APIs](#2-activer-les-apis)
3. [Ecran de consentement OAuth](#3-ecran-de-consentement-oauth)
4. [Creer les identifiants OAuth](#4-creer-les-identifiants-oauth)
5. [Recuperer les credentials](#5-recuperer-les-credentials)
6. [Configurer le .env](#6-configurer-le-env)
7. [Mode test](#7-mode-test)
8. [Publication et verification Google](#8-publication-et-verification-google)
9. [Depannage](#9-depannage)

---

## 1. Projet Google Cloud

### Option A : Utiliser le projet existant (recommande)

Le projet Google Cloud a deja ete cree avec le numero de projet **367559837862**.

1. Aller sur [Google Cloud Console](https://console.cloud.google.com/)
2. En haut a gauche, cliquer sur le **selecteur de projet** (a cote du logo Google Cloud)
3. Dans la fenetre qui s'ouvre, chercher le projet avec l'ID `367559837862`
4. Le selectionner pour travailler dedans

> **Verification :** L'ID du projet s'affiche en haut de la page. Confirmer que le numero correspond.

### Option B : Creer un nouveau projet

Si vous preferez repartir de zero :

1. Aller sur [Google Cloud Console](https://console.cloud.google.com/)
2. Cliquer sur le **selecteur de projet** > **Nouveau projet**
3. Remplir :
   - **Nom du projet :** `billets-monitor-mvp`
   - **Organisation :** laisser "Aucune organisation" si compte personnel
   - **Emplacement :** laisser par defaut
4. Cliquer sur **Creer**
5. Attendre la creation (~10 secondes), puis selectionner le nouveau projet

---

## 2. Activer les APIs

L'application a besoin de 4 APIs Google. Il faut les activer une par une.

### Methode rapide (liens directs)

Cliquer sur chaque lien ci-dessous (en etant connecte au bon projet) et cliquer **Activer** :

1. **Gmail API** : https://console.cloud.google.com/apis/library/gmail.googleapis.com
2. **Google Sheets API** : https://console.cloud.google.com/apis/library/sheets.googleapis.com
3. **Google Drive API** : https://console.cloud.google.com/apis/library/drive.googleapis.com
4. **Google People API** : https://console.cloud.google.com/apis/library/people.googleapis.com

> **Note :** La People API (ou UserInfo) est necessaire pour recuperer l'email, le nom et la photo de profil lors de la connexion OAuth.

### Methode manuelle

1. Dans le menu lateral gauche, aller dans **APIs et services** > **Bibliotheque**
2. Pour chaque API ci-dessous, rechercher dans la barre de recherche, cliquer dessus, puis cliquer **Activer** :

| API a activer | Ce qu'elle permet |
|---|---|
| **Gmail API** | Lire les emails (confirmations de commande) |
| **Google Sheets API** | Creer et modifier les feuilles de calcul |
| **Google Drive API** | Creer des fichiers dans le Drive de l'utilisateur |
| **Google People API** | Recuperer le profil (email, nom, photo) |

### Verification

Apres activation, aller dans **APIs et services** > **APIs et services actives**. Les 4 APIs doivent apparaitre dans la liste :

```
Gmail API                 Activee
Google Sheets API         Activee
Google Drive API          Activee
Google People API         Activee
```

---

## 3. Ecran de consentement OAuth

L'ecran de consentement est ce que l'utilisateur voit quand il connecte son compte Google a l'application. Il faut le configurer avant de pouvoir creer des identifiants OAuth.

### Etape 3.1 : Acceder a la configuration

1. Dans le menu lateral, aller dans **APIs et services** > **Ecran de consentement OAuth**
2. Selectionner **Externe** comme type d'utilisateur
   - "Externe" signifie que n'importe quel compte Google pourra se connecter (pas seulement les comptes d'une organisation Google Workspace)
3. Cliquer **Creer**

### Etape 3.2 : Informations sur l'application

Remplir les champs suivants :

| Champ | Valeur |
|---|---|
| **Nom de l'application** | `Billets Monitor` |
| **Adresse e-mail d'assistance utilisateur** | Votre email Google |
| **Logo de l'application** | (optionnel, peut etre ajoute plus tard) |

Dans la section **Domaine de l'application** (optionnel en mode test, obligatoire pour la publication) :

| Champ | Valeur |
|---|---|
| **Page d'accueil de l'application** | `https://VOTRE-DOMAINE.com` |
| **Lien vers les regles de confidentialite** | `https://VOTRE-DOMAINE.com/privacy` |
| **Lien vers les conditions d'utilisation** | `https://VOTRE-DOMAINE.com/terms` |

Dans **Domaines autorises** :
- Ajouter votre domaine de production (ex: `votre-domaine.com`)

| Champ | Valeur |
|---|---|
| **Coordonnees du developpeur** | Votre email |

Cliquer **Enregistrer et continuer**.

### Etape 3.3 : Champs d'application (Scopes)

C'est ici qu'on declare les permissions que l'application demande aux utilisateurs.

1. Cliquer sur **Ajouter ou supprimer des champs d'application**
2. Ajouter les scopes suivants en les recherchant ou en les collant dans le champ "Ajouter manuellement" :

#### Scopes non sensibles

| Scope | API | Description |
|---|---|---|
| `openid` | - | Authentification OpenID Connect |
| `https://www.googleapis.com/auth/userinfo.email` | People API | Voir l'adresse email |
| `https://www.googleapis.com/auth/userinfo.profile` | People API | Voir le profil (nom, photo) |

#### Scopes sensibles

| Scope | API | Description | Sensibilite |
|---|---|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Gmail API | Lire les emails | **Sensible** |
| `https://www.googleapis.com/auth/spreadsheets` | Sheets API | Lire/ecrire les feuilles de calcul | **Sensible** |
| `https://www.googleapis.com/auth/drive.file` | Drive API | Creer/modifier les fichiers crees par l'app | Restreint au fichiers de l'app |

3. Cliquer **Mettre a jour** pour confirmer la selection
4. Cliquer **Enregistrer et continuer**

> **Important :** `gmail.readonly` est classe comme **scope sensible** par Google. Cela signifie que :
> - En mode test, seuls les utilisateurs de test pourront se connecter
> - Pour un usage en production, il faudra soumettre l'application a une verification Google (voir section 8)

### Etape 3.4 : Utilisateurs de test

Cette etape permet d'ajouter des comptes Google qui pourront tester l'application tant qu'elle n'est pas verifiee.

1. Cliquer **Ajouter des utilisateurs**
2. Entrer les adresses email des testeurs (maximum 100 en mode test)
3. Cliquer **Ajouter**
4. Cliquer **Enregistrer et continuer**

> **Note :** Vous DEVEZ ajouter votre propre adresse email ici pour pouvoir tester.

### Etape 3.5 : Recapitulatif

Verifier le recapitulatif et cliquer **Revenir au tableau de bord**.

---

## 4. Creer les identifiants OAuth

### Etape 4.1 : Creer un Client ID OAuth 2.0

1. Dans le menu lateral, aller dans **APIs et services** > **Identifiants**
2. Cliquer **+ Creer des identifiants** > **ID client OAuth**
3. Remplir les champs :

| Champ | Valeur |
|---|---|
| **Type d'application** | Application Web |
| **Nom** | `Billets Monitor Web Client` |

### Etape 4.2 : Configurer les origines JavaScript autorisees

Dans la section **Origines JavaScript autorisees**, ajouter :

```
http://localhost:5000
```

Pour la production, ajouter egalement :

```
https://VOTRE-DOMAINE.com
```

### Etape 4.3 : Configurer les URI de redirection autorises

C'est la partie la plus importante. L'application utilise deux callbacks OAuth differents.

Dans la section **URI de redirection autorises**, ajouter ces 4 URIs :

#### Developpement (localhost)

```
http://localhost:5000/oauth/callback
http://localhost:5000/oauth/add-gmail/callback
```

#### Production

```
https://VOTRE-DOMAINE.com/oauth/callback
https://VOTRE-DOMAINE.com/oauth/add-gmail/callback
```

> **Attention :** Les URIs doivent correspondre EXACTEMENT a ce que l'application envoie a Google. Pas de slash final, pas de difference de casse. Le moindre caractere different provoquera une erreur `redirect_uri_mismatch`.

### Etape 4.4 : Creer

Cliquer **Creer**. Une fenetre s'affiche avec :
- **Votre ID client** (format : `XXXXXXXXXXXX-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX.apps.googleusercontent.com`)
- **Votre code secret client** (format : `GOCSPX-XXXXXXXXXXXXXXXXXXXXXXXXXX`)

**Copier immediatement ces deux valeurs.** Le code secret ne sera plus visible en entier apres fermeture de cette fenetre (il faudra en regenerer un si vous le perdez).

---

## 5. Recuperer les credentials

### Si vous avez ferme la fenetre

1. Aller dans **APIs et services** > **Identifiants**
2. Dans la section **ID clients OAuth 2.0**, trouver `Billets Monitor Web Client`
3. Cliquer sur l'icone **crayon** (modifier) a droite
4. Les informations sont affichees :
   - **ID client** : toujours visible
   - **Code secret du client** : cliquer sur l'icone oeil pour le reveler, ou cliquer **Reinitialiser le code secret** pour en generer un nouveau

### Telecharger le fichier JSON (optionnel)

1. Sur la meme page d'identifiants, cliquer sur l'icone **telecharger** (fleche vers le bas) a cote de votre client OAuth
2. Cela telecharge un fichier `client_secret_XXXXXX.json`
3. Ce fichier contient toutes les informations de configuration OAuth

> **Securite :** Ne commitez JAMAIS ce fichier dans git. Ajoutez `client_secret*.json` a votre `.gitignore`.

---

## 6. Configurer le .env

Creer le fichier `.env` dans le dossier `backend/` a partir du template `.env.example` :

```bash
cp backend/.env.example backend/.env
```

Puis editer `backend/.env` avec vos valeurs :

```bash
# Flask
SECRET_KEY=votre-cle-secrete-generee-aleatoirement-64-caracteres

# Google OAuth
GOOGLE_CLIENT_ID=367559837862-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-XXXXXXXXXXXXXXXXXXXXXXXXXX

# Application
APP_URL=http://localhost:5000
```

### Generer une SECRET_KEY aleatoire

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copier la sortie et la coller comme valeur de `SECRET_KEY`.

### Valeurs par environnement

| Variable | Developpement | Production |
|---|---|---|
| `SECRET_KEY` | Cle generee | Cle generee (differente !) |
| `GOOGLE_CLIENT_ID` | Identique | Identique |
| `GOOGLE_CLIENT_SECRET` | Identique | Identique |
| `APP_URL` | `http://localhost:5000` | `https://VOTRE-DOMAINE.com` |

> **Important :** La variable `APP_URL` est utilisee par l'application pour construire les `redirect_uri` envoyees a Google. Elle doit correspondre exactement aux URIs configurees dans la console (section 4.3). En production, il faut changer `APP_URL` pour pointer vers le domaine reel.

### Verification de la configuration

L'application charge les variables via `config.py` :

```python
# backend/config.py
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_URL = os.getenv("APP_URL", "http://localhost:5000")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
```

Pour verifier que tout est correct, lancer :

```bash
cd backend
python3 -c "from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_URL; print(f'Client ID: {GOOGLE_CLIENT_ID[:20]}...'); print(f'Secret: {GOOGLE_CLIENT_SECRET[:10]}...'); print(f'URL: {APP_URL}')"
```

Vous devriez voir les debut de vos credentials et l'URL correcte.

---

## 7. Mode test

### Fonctionnement du mode test

Tant que l'application est en statut **"Test"** dans Google Cloud Console (ce qui est le cas par defaut), les restrictions suivantes s'appliquent :

| Restriction | Detail |
|---|---|
| **Nombre d'utilisateurs** | Maximum 100 utilisateurs de test |
| **Qui peut se connecter** | Uniquement les emails ajoutes manuellement dans la liste des testeurs |
| **Expiration des tokens** | Les refresh tokens expirent apres **7 jours** |
| **Ecran d'avertissement** | Google affiche un ecran "Cette application n'est pas verifiee" avec un bouton "Parametres avances" > "Acceder a Billets Monitor (non securise)" |

### Ajouter des utilisateurs de test

1. Aller dans **APIs et services** > **Ecran de consentement OAuth**
2. Descendre jusqu'a la section **Utilisateurs test**
3. Cliquer **+ Ajouter des utilisateurs**
4. Entrer les adresses email Google des personnes qui doivent tester l'application
5. Cliquer **Enregistrer**

### Ce que l'utilisateur de test verra

Lors de la premiere connexion, l'utilisateur verra cet ecran :

```
+--------------------------------------------------+
|                                                    |
|  Google n'a pas verifie cette application           |
|                                                    |
|  Cette application demande l'acces a des           |
|  donnees sensibles de votre compte Google.         |
|                                                    |
|  [Retour a un endroit sur]                        |
|                                                    |
|  Parametres avances v                              |
|                                                    |
+--------------------------------------------------+
```

L'utilisateur doit :
1. Cliquer sur **Parametres avances** (en bas)
2. Cliquer sur **Acceder a Billets Monitor (non securise)**
3. Cocher les permissions demandees
4. Cliquer **Continuer**

> **Impact sur le developpement :** Les tokens de refresh expirent apres 7 jours en mode test. Cela signifie que les scans automatiques (cron) cesseront de fonctionner apres 7 jours si l'utilisateur ne se reconnecte pas. Ce probleme disparait une fois l'application verifiee et publiee.

### Conseils pour le developpement

- Ajoutez votre propre email comme utilisateur de test immediatement
- Si vous testez avec plusieurs comptes Gmail (pour la feature "ajouter un compte"), ajoutez-les tous
- Quand un refresh token expire (erreur `invalid_grant`), il suffit de se reconnecter via `/auth/google`

---

## 8. Publication et verification Google

### Pourquoi la verification est necessaire

L'application utilise le scope `gmail.readonly` qui est classe comme **sensible** par Google. Sans verification :
- Limite a 100 utilisateurs de test
- Ecran d'avertissement effrayant
- Tokens qui expirent apres 7 jours
- Impossible d'utiliser en production pour de vrais clients

### Etape 8.1 : Preparer les pre-requis

Avant de soumettre la demande de verification, preparer :

| Pre-requis | Detail | Obligatoire |
|---|---|---|
| **Domaine verifie** | Votre domaine doit etre verifie dans Google Search Console | Oui |
| **Page d'accueil** | URL publiquement accessible | Oui |
| **Politique de confidentialite** | Page expliquant comment les donnees sont utilisees | Oui |
| **Conditions d'utilisation** | Page avec les CGU | Recommande |
| **Video de demonstration** | Video montrant comment l'app utilise chaque scope | Oui (pour scopes sensibles) |
| **Justification ecrite** | Explication de pourquoi chaque scope est necessaire | Oui |

### Etape 8.2 : Verifier le domaine

1. Aller dans **APIs et services** > **Ecran de consentement OAuth**
2. Dans la section **Domaines autorises**, votre domaine doit etre verifie
3. Cliquer sur le lien vers **Google Search Console** pour verifier le domaine
4. Suivre les instructions (ajout d'un enregistrement DNS TXT ou d'un fichier HTML)

### Etape 8.3 : Preparer la justification des scopes

Pour chaque scope sensible, Google demande une justification. Voici ce qu'il faut ecrire :

#### gmail.readonly

```
Billets Monitor scans the user's Gmail inbox to automatically find order
confirmation emails from ticket platforms (Ticketmaster, Roland-Garros,
Stade de France) and Vinted transaction emails. The app only reads email
subjects and bodies to extract order details (event name, date, price,
order number). No emails are modified, deleted, or stored — only the
extracted order data is written to the user's own Google Sheet.

The app uses Gmail API messages.list with specific queries (e.g.,
from:ticketmaster.fr subject:confirmation) to find relevant emails only.
Full email bodies are parsed in-memory and never persisted on our servers.
```

#### spreadsheets

```
Billets Monitor creates and writes to Google Sheets in the user's own
Google Drive. The app creates a spreadsheet with predefined columns
(event, price, date, etc.) and appends rows as new orders are found
in Gmail. Users can also link an existing spreadsheet. The app only
reads/writes to sheets it created or that the user explicitly linked.
```

#### drive.file

```
Billets Monitor uses drive.file (restricted to files created by the app)
to create the initial Google Sheet in the user's Drive. This scope is
limited — the app cannot access any other files in the user's Drive.
Only spreadsheets created by the app are accessible.
```

### Etape 8.4 : Enregistrer la video de demonstration

Google exige une video non listee sur YouTube montrant :

1. **L'ecran de consentement** : montrer ce que l'utilisateur voit
2. **Chaque scope en action** :
   - Connexion OAuth (openid, userinfo)
   - Lecture des emails Gmail (gmail.readonly) — montrer le scan
   - Creation du Google Sheet (spreadsheets, drive.file) — montrer la feuille creee
   - Ecriture des donnees dans le Sheet (spreadsheets) — montrer les lignes ajoutees
3. **Le flux complet** : connexion > scan > resultat dans le Sheet

> **Duree recommandee :** 3 a 5 minutes. Pas besoin de montage elabore, un screencast suffit.

### Etape 8.5 : Soumettre la demande

1. Aller dans **APIs et services** > **Ecran de consentement OAuth**
2. Verifier que toutes les informations sont completes :
   - Nom de l'application
   - Email d'assistance
   - Logo
   - Domaine verifie
   - Liens (accueil, confidentialite, conditions)
   - Scopes configures
3. Cliquer sur **Publier l'application**
4. Google affiche un formulaire de verification. Remplir :
   - **Lien vers la video YouTube** (non listee)
   - **Justification pour chaque scope sensible** (textes ci-dessus)
   - **Informations supplementaires** si demande
5. Soumettre

### Etape 8.6 : Delais et suivi

| Phase | Delai estime |
|---|---|
| Examen initial | 1 a 3 jours ouvrables |
| Questions/corrections | Variable (Google peut demander des clarifications) |
| Verification complete | 2 a 6 semaines au total |

Pendant la verification :
- L'application reste en mode test
- Vous pouvez continuer a developper et tester avec les utilisateurs de test
- Google vous contactera par email pour toute question

### Etape 8.7 : Apres la verification

Une fois l'application verifiee :
- L'ecran d'avertissement "non verifiee" disparait
- N'importe quel compte Google peut se connecter (plus de limite a 100)
- Les refresh tokens n'expirent plus apres 7 jours
- L'application est prete pour la production

---

## 9. Depannage

### Erreurs courantes

#### `redirect_uri_mismatch`

```
Error 400: redirect_uri_mismatch
The redirect URI in the request does not match the ones authorized for the OAuth client.
```

**Cause :** L'URI de redirection envoyee par l'application ne correspond pas exactement a celles configurees dans la console.

**Solution :**
1. Verifier la variable `APP_URL` dans `.env`
2. Comparer avec les URIs dans **Identifiants** > **Billets Monitor Web Client** > **URI de redirection**
3. Attention aux differences : `http` vs `https`, presence ou absence de `/` final, port

#### `access_denied`

```
Error 403: access_denied
The developer hasn't given you access to this app.
```

**Cause :** L'utilisateur n'est pas dans la liste des testeurs (mode test uniquement).

**Solution :** Ajouter l'email de l'utilisateur dans **Ecran de consentement OAuth** > **Utilisateurs test**.

#### `invalid_grant`

```
google.auth.exceptions.RefreshError: ('invalid_grant: Token has been expired or revoked.')
```

**Cause :** Le refresh token a expire (7 jours en mode test) ou l'utilisateur a revoque l'acces.

**Solution :**
- L'utilisateur doit se reconnecter via `/auth/google`
- En mode test, c'est normal apres 7 jours

#### `insufficient_permissions` ou `PERMISSION_DENIED`

```
HttpError 403: Request had insufficient authentication scopes.
```

**Cause :** L'application tente d'utiliser une API sans le scope correspondant, ou l'API n'est pas activee.

**Solution :**
1. Verifier que les 4 APIs sont activees (section 2)
2. Verifier que tous les scopes sont ajoutes dans l'ecran de consentement (section 3.3)
3. L'utilisateur doit se reconnecter pour accorder les nouveaux scopes

#### `deleted_client`

```
Error: The OAuth client was not found.
```

**Cause :** Le Client ID utilise dans `.env` ne correspond a aucun client dans le projet Google Cloud.

**Solution :** Verifier `GOOGLE_CLIENT_ID` dans `.env` et le comparer avec la console.

### Verifier la configuration complete

Checklist rapide pour s'assurer que tout est en ordre :

```
[ ] Projet Google Cloud selectionne : 367559837862
[ ] 4 APIs activees : Gmail, Sheets, Drive, People
[ ] Ecran de consentement configure en "Externe"
[ ] 6 scopes ajoutes (openid, email, profile, gmail, sheets, drive)
[ ] Client OAuth 2.0 cree (type "Application Web")
[ ] 4 URIs de redirection configurees (2 dev + 2 prod)
[ ] .env cree avec GOOGLE_CLIENT_ID et GOOGLE_CLIENT_SECRET
[ ] APP_URL correspond au domaine utilise
[ ] Votre email ajoute comme utilisateur de test
[ ] Application Flask lancee sur le bon port
```

### Liens utiles

| Ressource | URL |
|---|---|
| Google Cloud Console | https://console.cloud.google.com/ |
| APIs actives | https://console.cloud.google.com/apis/dashboard |
| Identifiants OAuth | https://console.cloud.google.com/apis/credentials |
| Ecran de consentement | https://console.cloud.google.com/apis/credentials/consent |
| Documentation OAuth2 | https://developers.google.com/identity/protocols/oauth2 |
| Scopes Gmail API | https://developers.google.com/gmail/api/auth/scopes |
| Verification OAuth | https://support.google.com/cloud/answer/9110914 |
