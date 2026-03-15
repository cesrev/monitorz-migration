"""
Vinted routes: hashtag generation, WTS generation, article listing, sell times.
"""

import logging
import re as _re
from flask import Blueprint, session, request, jsonify
from googleapiclient.discovery import build
import anthropic
import database as db
from helpers import login_required, _get_sheet_data_cached, paginate_list, get_google_credentials
from config import ANTHROPIC_API_KEY
from data.vinted_hashtags import (
    BRAND_ALIASES, MODEL_ALIASES, SNEAKER_KEYWORDS, UNIVERSE_TAGS,
    COLOR_ALIASES, _BLOCKED_PATTERN, MAX_HASHTAGS, MIN_HASHTAGS,
    FALLBACK_SNEAKER_TAGS, FALLBACK_CLOTHING_TAGS,
    FALLBACK_ACCESSORY_TAGS, FALLBACK_GAMING_TAGS,
)

logger = logging.getLogger(__name__)

vinted_bp = Blueprint("vinted", __name__)


def _word_match(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word (not substring) in text."""
    if len(keyword) <= 3:
        pattern = r'(?:^|[\s,/\-])' + _re.escape(keyword) + r'(?:$|[\s,/\-])'
        return bool(_re.search(pattern, text))
    return keyword in text


def _is_sneaker(title_lower: str) -> bool:
    """Detect if the article is a sneaker based on title keywords."""
    padded = title_lower + " "
    return any(kw in padded for kw in SNEAKER_KEYWORDS)


def _detect_universes(title_lower: str) -> list[str]:
    """Detect style/era universes from title."""
    found = []
    universe_keywords = {
        "vintage": ["vintage", "retro", "old", "90s", "80s", "70s", "archive"],
        "y2k": ["y2k", "2000", "millenium"],
        "streetwear": ["supreme", "palace", "stussy", "carhartt", "bape", "off-white"],
        "techwear": ["gore-tex", "goretex", "therma", "tech", "utility", "waterproof", "coupe-vent", "windbreaker", "salomon", "arcteryx"],
        "oldmoney": ["ralph lauren", "barbour", "lacoste", "polo", "preppy", "marina yachting", "nautica", "gant"],
        "hiphop": ["pelle pelle", "dada", "fubu", "ecko", "rocawear", "sean john"],
        "outdoor": ["patagonia", "columbia", "barbour", "north face"],
        "luxe": ["gucci", "louis vuitton", "hermes", "balenciaga", "dior", "prada", "celine", "loewe", "bottega", "ysl", "yves saint laurent", "fendi"],
        "skate": ["sb", "palace", "dunk sb", "vans"],
        "british": ["barbour", "burberry", "fred perry", "harrington", "tartan"],
        "merch": ["merch", "concert", "tour", "band", "slipknot", "metallica", "shakira", "kanye"],
        "preppy": ["ralph lauren", "polo", "lacoste", "gant", "half zip", "col v"],
        "sportswear": ["tracksuit", "jogging", "track", "jersey", "maillot", "survetement", "windbreaker"],
        "japanese": ["japanese", "japan", "evisu", "bape", "comme des garcons", "cdg", "yohji", "mihara"],
        "uk": ["corteiz", "crtz", "trapstar"],
        "gaming": ["nintendo", "switch", "playstation", "ps5", "ps4", "xbox", "gameboy", "wii", "sega", "n64", "manette", "controller"],
        "tcg": ["pokemon", "pokémon", "yu-gi-oh", "yugioh", "magic the gathering", "mtg", "trading card", "carte", "booster", "etb", "display", "coffret", "tin", "blister", "ev9", "ev8", "ev7", "ev6", "dilga", "151", "ecarlate", "scarlet"],
        "retrogaming": ["gameboy", "game boy", "sega", "n64", "snes", "nes", "megadrive", "game cube", "gamecube", "retrogaming"],
    }
    for universe, keywords in universe_keywords.items():
        if any(kw in title_lower for kw in keywords):
            found.append(universe)
    return found


def _detect_article_type(title_lower: str) -> str:
    """Detect broad article type: sneaker, clothing, accessory, or gaming."""
    if _is_sneaker(title_lower):
        return "sneaker"
    gaming_kw = [
        "nintendo", "switch", "playstation", "ps5", "ps4", "ps3", "ps2",
        "xbox", "gameboy", "game boy", "wii", "sega", "n64",
        "manette", "controller", "amiibo", "jeux", "jeu video",
        "yu-gi-oh", "yugioh", "magic the gathering", "mtg",
        "trading card", "carte", "booster", "etb", "display",
        "tcg", "coffret", "tin", "blister",
        "digimon", "one piece card", "dragon ball",
        "pokemon", "pokémon",
        "ev9", "ev8", "ev7", "ev6", "ev5", "ev4", "ev3", "ev2", "ev1",
        "151", "ecarlate", "scarlet", "paldea", "obsidienne",
        "tempete argentee", "origine perdue", "couronne zenith",
        "astres radieux", "evolutions", "soleil et lune", "epee et bouclier",
        "flammes obsidiennes", "forces temporelles", "faille paradoxe",
        "mascarade crepusculaire", "destinees de paldea",
        "funko", "figurine", "lego", "bearbrick",
        "pikachu", "dracaufeu", "charizard", "tortank", "blastoise",
    ]
    if any(kw in title_lower for kw in gaming_kw):
        return "gaming"
    accessory_kw = ["sac", "bag", "lunettes", "sunglasses", "casquette", "cap",
                    "bonnet", "beanie", "ceinture", "belt", "montre", "watch",
                    "echarpe", "scarf", "banner", "drapeau", "bijou"]
    if any(kw in title_lower for kw in accessory_kw):
        return "accessory"
    return "clothing"


def generate_hashtags(title: str, custom_tags: list = None) -> list[str]:
    """Generate hashtags for a Vinted item. 3-layer system, 10 min / 15 max."""
    title_lower = title.lower().strip()
    layer1 = []  # Marque + Modele (priority)
    layer2 = []  # Style / Epoque / Univers
    layer3 = []  # Descripteurs specifiques (couleur, type)

    # === COUCHE 1 : Marque + Modele ===
    matched_brand = None
    # Sort by length desc to match "ralph lauren" before "ralph"
    for brand in sorted(BRAND_ALIASES.keys(), key=len, reverse=True):
        if _word_match(brand, title_lower):
            layer1.extend(BRAND_ALIASES[brand])
            matched_brand = brand
            break

    # Fallback: if no known brand, extract first word as raw brand tag
    if not matched_brand:
        words = title.strip().split()
        if words:
            raw_brand = words[0].lower().strip()
            # Only use if it looks like a brand (not a generic word)
            generic_words = {"le", "la", "les", "un", "une", "des", "de", "du",
                            "lot", "pack", "set", "paire", "pair", "air", "t-shirt",
                            "tee", "veste", "pantalon", "pull", "sac", "short",
                            "jean", "chemise", "hoodie", "sweat", "jogging",
                            "casquette", "bonnet", "maillot", "chaussure",
                            "lunettes", "robe", "manteau", "doudoune", "polo",
                            "cardigan", "bermuda", "cargo", "col", "blouson",
                            "gilet", "parka", "trench", "survetement",
                            "debardeur", "polaire", "coffret", "cap"}
            if raw_brand not in generic_words and len(raw_brand) > 2:
                layer1.append(f"#{raw_brand}")
                # Also try brand + second word combo
                if len(words) > 1:
                    second = words[1].lower().strip()
                    if second not in generic_words and len(second) > 2:
                        layer1.append(f"#{raw_brand}{second}")

    # Model detection (match all models, longest first, max 2)
    model_matches = 0
    for model in sorted(MODEL_ALIASES.keys(), key=len, reverse=True):
        if model in title_lower:
            layer1.extend(MODEL_ALIASES[model])
            model_matches += 1
            if model_matches >= 2:
                break

    # === COUCHE 2 : Style / Epoque / Univers ===
    article_type = _detect_article_type(title_lower)
    is_sneaker = article_type == "sneaker"
    universes = _detect_universes(title_lower)

    if is_sneaker and "sneakers" not in universes:
        universes.insert(0, "sneakers")

    # Check for vintage indicators
    if any(w in title_lower for w in ["vintage", "retro", "old", "90s", "80s"]):
        if "vintage" not in universes:
            universes.append("vintage")

    for universe in universes[:3]:  # Max 3 universes
        tags = UNIVERSE_TAGS.get(universe, [])
        layer2.extend(tags[:3])  # Max 3 tags per universe

    # === COUCHE 3 : Descripteurs (couleur, type article, specifiques) ===
    # Couleur (max 2 couleurs)
    color_count = 0
    for color in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        if _word_match(color, title_lower):
            layer3.extend(COLOR_ALIASES[color])
            color_count += 1
            if color_count >= 2:
                break

    # Sneaker-specific: extract colorway/collab name
    if is_sneaker:
        colorways = [
            "cement", "bred", "chicago", "shadow", "royal", "obsidian",
            "mocha", "travis", "og", "anatomy", "koston", "safari",
            "panda", "university", "denim", "infrared", "neon",
        ]
        for cw in colorways:
            if cw in title_lower:
                layer3.append(f"#{cw}")
    else:
        # Clothing/Accessory: descriptive compound tags
        clothing_descriptors = {
            "veste": ["#veste"], "jacket": ["#jacket"],
            "hoodie": ["#hoodie"], "sweat": ["#sweat"],
            "t-shirt": ["#tshirt"], "tee": ["#tee"],
            "pantalon": ["#pantalon"], "pants": ["#pants"],
            "jogging": ["#jogger", "#trackpants"],
            "jogger": ["#jogger", "#trackpants"],
            "chemise": ["#chemise"], "shirt": ["#shirt"],
            "pull": ["#pull", "#sweater"],
            "col roule": ["#colroule"],
            "half zip": ["#halfzip", "#sweathalfzip"],
            "zip": ["#zipup"],
            "maillot": ["#maillot"],
            "jersey": ["#jersey"],
            "short": ["#short"],
            "jean": ["#denim", "#jeans"],
            "cargo": ["#cargo"],
            "sac": ["#sac", "#backpack"],
            "lunettes": ["#lunettes", "#sunglasses"],
            "casquette": ["#cap"],
            "bonnet": ["#beanie"],
            "doudoune": ["#puffer"],
            "manteau": ["#manteau", "#coat"],
            "polo": ["#polo"],
            "cardigan": ["#cardigan"],
            "robe": ["#robe", "#dress"],
            "banner": ["#banner", "#drapeau"],
            "matelasse": ["#vestehiver", "#vesteautomne"],
            "imperme": ["#vesteimpermeable"],
            "col velours": ["#colvelourscotele"],
            "tartan": ["#tartan"],
            "brode": ["#logobrodé"],
            "blouson": ["#blouson", "#jacket"],
            "cuir": ["#cuir", "#leather"],
            "polaire": ["#polaire", "#fleece"],
            "survetement": ["#survetement", "#tracksuit"],
            "gilet": ["#gilet", "#vest"],
            "parka": ["#parka"],
            "trench": ["#trench", "#trenchcoat"],
            "crop": ["#croptop"],
            "debardeur": ["#debardeur", "#tanktop"],
            "bomber": ["#bomber", "#bomberjacket"],
            "blazer": ["#blazer"],
            "cravate": ["#cravate", "#tie"],
            "mocassin": ["#mocassin", "#loafer"],
        }
        for kw in sorted(clothing_descriptors.keys(), key=len, reverse=True):
            if _word_match(kw, title_lower):
                layer3.extend(clothing_descriptors[kw])

    # === POKEMON RULES ===
    _pokemon_names = {
        "pikachu", "dracaufeu", "charizard", "tortank", "blastoise",
        "florizarre", "venusaur", "mewtwo", "mew", "evoli", "eevee",
        "rondoudou", "jigglypuff", "ronflex", "snorlax", "leviator", "gyarados",
    }
    _pokemon_tcg_triggers = {
        "etb", "booster", "coffret", "display", "tin", "blister",
        "ev9", "ev8", "ev7", "ev6", "ev5", "ev4", "ev3", "ev2", "ev1",
        "151", "ecarlate", "scarlet", "paldea", "obsidienne",
    }
    _is_pokemon_article = (
        "pokemon" in title_lower or "pokémon" in title_lower
        or any(name in title_lower for name in _pokemon_names)
        or any(kw in title_lower for kw in _pokemon_tcg_triggers)
    )
    if _is_pokemon_article:
        # Rule 1: #pokemon obligatoire
        if "#pokemon" not in [t.lower() for t in layer1 + layer2 + layer3]:
            layer1.insert(0, "#pokemon")
        # Rule 2: premier mot du titre en hashtag
        first_word = title.strip().split()[0].lower() if title.strip() else ""
        first_tag = f"#{first_word}"
        if first_word and len(first_word) > 1 and first_tag not in [t.lower() for t in layer1]:
            layer1.insert(0, first_tag)

    # === ASSEMBLAGE ===
    all_tags = layer1 + layer2 + layer3

    # Add custom user tags
    if custom_tags:
        for tag in custom_tags:
            tag = tag.strip()
            if tag and not tag.startswith("#"):
                tag = f"#{tag}"
            if tag:
                all_tags.append(tag)

    # Deduplicate (case-insensitive), preserve order
    seen = set()
    unique = []
    for h in all_tags:
        h_clean = h.lower().strip()
        if not h_clean or h_clean == "#":
            continue
        # Blocklist filter
        tag_text = h_clean.lstrip("#")
        if _BLOCKED_PATTERN.search(tag_text):
            continue
        if h_clean not in seen:
            seen.add(h_clean)
            unique.append(h_clean)

    # === MINIMUM 10 TAGS : fallback si pas assez ===
    if len(unique) < MIN_HASHTAGS:
        if article_type == "sneaker":
            fallback_pool = FALLBACK_SNEAKER_TAGS
        elif article_type == "gaming":
            fallback_pool = FALLBACK_GAMING_TAGS
        elif article_type == "accessory":
            fallback_pool = FALLBACK_ACCESSORY_TAGS
        else:
            fallback_pool = FALLBACK_CLOTHING_TAGS

        for fb in fallback_pool:
            if len(unique) >= MIN_HASHTAGS:
                break
            fb_clean = fb.lower().strip()
            tag_text = fb_clean.lstrip("#")
            if fb_clean not in seen and not _BLOCKED_PATTERN.search(tag_text):
                seen.add(fb_clean)
                unique.append(fb_clean)

    # Trim to MAX_HASHTAGS
    return unique[:MAX_HASHTAGS]


def _clean_cell(value):
    """Clean a cell value from Google Sheets (strip, handle None)."""
    if not value:
        return ""
    return str(value).strip()


def _parse_unsold_tickets(sheets_service, spreadsheet_id):
    """Read unsold tickets from Commandes sheet and return as list of dicts."""
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="Commandes!A:J",
    ).execute()
    rows = result.get("values", [])

    unsold = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        # Column 8 = Prix Vente
        price_vente = _clean_cell(row[8] if len(row) > 8 else "")
        if price_vente:  # Skip if sold
            continue

        unsold.append({
            "event": _clean_cell(row[0] if len(row) > 0 else ""),
            "category": _clean_cell(row[1] if len(row) > 1 else ""),
            "lieu": _clean_cell(row[2] if len(row) > 2 else ""),
            "date": _clean_cell(row[3] if len(row) > 3 else ""),
            "prix_achat": _clean_cell(row[4] if len(row) > 4 else ""),
            "numero": _clean_cell(row[5] if len(row) > 5 else ""),
            "lien": _clean_cell(row[6] if len(row) > 6 else ""),
            "compte": _clean_cell(row[7] if len(row) > 7 else ""),
        })
    return unsold


def _build_wts_text(unsold_items):
    """Build a WTS (Want To Sell) announcement text from unsold items."""
    if not unsold_items:
        return ""

    lines = ["BILLETS EN VENTE - TICKETS FOR SALE", "", "Available tickets:"]
    for item in unsold_items:
        line = f"- {item['event']}"
        if item['category']:
            line += f" ({item['category']})"
        if item['date']:
            line += f" - {item['date']}"
        if item['prix_achat']:
            line += f" - {item['prix_achat']}"
        lines.append(line)

    return "\n".join(lines)


@vinted_bp.route("/api/generate-hashtags", methods=["POST"])
@login_required
def generate_hashtags_route():
    """Generate hashtags for Vinted items."""
    data = request.get_json(silent=True) or {}

    title = (data.get("title") or "").strip()
    custom_tags = data.get("custom_tags", [])

    if not title:
        return jsonify({"success": False, "error": "Title requis"}), 400

    tags = generate_hashtags(title, custom_tags)
    return jsonify({"success": True, "hashtags": tags})


@vinted_bp.route("/api/hashtag-categories")
@login_required
def hashtag_categories():
    """Return hashtag categories and fallback tags."""
    return jsonify({
        "brands": BRAND_ALIASES,
        "models": MODEL_ALIASES,
        "universes": UNIVERSE_TAGS,
        "colors": COLOR_ALIASES,
    })


@vinted_bp.route("/api/generate-wts", methods=["GET", "POST"])
@login_required
def generate_wts():
    """Generate WTS text from unsold tickets.
    GET: Return raw unsold items for the frontend form.
    POST: Return generated WTS text.
    """
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    sheets = db.get_spreadsheets(user_id, monitoring_type="tickets")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]
        unsold = _parse_unsold_tickets(sheets_service, spreadsheet_id)

        if request.method == "GET":
            # Return raw items for the frontend WTS form
            return jsonify({
                "success": True,
                "items": unsold,
                "unsold_count": len(unsold),
            })

        # POST: return generated WTS text
        wts_text = _build_wts_text(unsold)
        return jsonify({
            "success": True,
            "wts": wts_text,
            "unsold_count": len(unsold),
        })
    except Exception as exc:
        logger.error("Failed to generate WTS: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la generation du WTS"}), 500


@vinted_bp.route("/api/generate-wts-ai", methods=["GET", "POST"])
@login_required
def generate_wts_ai():
    """Generate a creative WTS announcement with AI."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "tickets":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs tickets"}), 403

    sheets = db.get_spreadsheets(user_id, monitoring_type="tickets")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]
        unsold = _parse_unsold_tickets(sheets_service, spreadsheet_id)

        if not unsold:
            return jsonify({"success": True, "wts": ""})

        # Use Claude to generate creative WTS
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        items_text = "\n".join([
            f"- {u['event']} ({u['category']}) - {u['date']} - {u['prix_achat']}"
            for u in unsold[:10]
        ])

        prompt = f"""Generate a creative, engaging "Want To Sell" announcement for these unsold tickets.
Keep it short (2-3 sentences), professional but friendly. Include a call-to-action.

Tickets:
{items_text}

Respond in French. Don't use hashtags."""

        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )

        wts_text = message.content[0].text

        return jsonify({
            "success": True,
            "wts_text": wts_text,
            "unsold_count": len(unsold),
        })
    except Exception as exc:
        logger.error("Failed to generate AI WTS: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors de la generation IA"}), 500


@vinted_bp.route("/api/vinted-articles")
@login_required
def vinted_articles():
    """List all Vinted articles from the user's sheet with pagination support."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "vinted":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs vinted"}), 403

    sheets = db.get_spreadsheets(user_id, monitoring_type="vinted")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:I")

        if len(rows) < 2:
            return jsonify({
                "success": True,
                "data": [],
                "pagination": {
                    "page": 1,
                    "per_page": 50,
                    "total": 0,
                    "total_pages": 0,
                    "has_next": False,
                    "has_prev": False
                }
            })

        articles = []
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            articles.append({
                "title": _clean_cell(row[0]),
                "purchase_price": _clean_cell(row[1] if len(row) > 1 else ""),
                "purchase_date": _clean_cell(row[2] if len(row) > 2 else ""),
                "sale_price": _clean_cell(row[3] if len(row) > 3 else ""),
                "sale_date": _clean_cell(row[4] if len(row) > 4 else ""),
                "profit": _clean_cell(row[5] if len(row) > 5 else ""),
                "roi": _clean_cell(row[6] if len(row) > 6 else ""),
                "stock_days": _clean_cell(row[7] if len(row) > 7 else ""),
                "account": _clean_cell(row[8] if len(row) > 8 else ""),
            })

        # Get pagination parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)

        # Apply pagination
        paginated = paginate_list(articles, page, per_page)

        return jsonify({
            "success": True,
            "data": paginated["data"],
            "pagination": paginated["pagination"]
        })
    except Exception as exc:
        logger.error("Failed to load vinted articles: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors du chargement des articles"}), 500


@vinted_bp.route("/api/vinted-sell-times")
@login_required
def vinted_sell_times():
    """Return average sell times and statistics for Vinted articles."""
    user_id = session["user_id"]
    user = db.get_user_by_id(user_id)

    if not user or user.get("monitoring_type") != "vinted":
        return jsonify({"success": False, "error": "Feature reservee aux utilisateurs vinted"}), 403

    sheets = db.get_spreadsheets(user_id, monitoring_type="vinted")
    if not sheets:
        return jsonify({"success": False, "error": "Aucun Google Sheet configure"}), 400

    creds, primary, err = get_google_credentials(user_id)
    if err:
        return err

    try:
        sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        spreadsheet_id = sheets[0]["spreadsheet_id"]

        rows = _get_sheet_data_cached(sheets_service, spreadsheet_id, "Commandes!A:I")

        if len(rows) < 2:
            return jsonify({"success": True, "stats": {}})

        stock_days = []
        for row in rows[1:]:
            if len(row) > 7 and row[7]:
                try:
                    days = float(str(row[7]).replace(" j", "").strip())
                    if days >= 0:
                        stock_days.append(days)
                except (ValueError, TypeError):
                    continue

        if not stock_days:
            return jsonify({"success": True, "stats": {"count": 0}})

        avg_days = sum(stock_days) / len(stock_days)
        min_days = min(stock_days)
        max_days = max(stock_days)

        return jsonify({
            "success": True,
            "stats": {
                "count": len(stock_days),
                "average_days": round(avg_days, 1),
                "min_days": round(min_days, 1),
                "max_days": round(max_days, 1),
            },
        })
    except Exception as exc:
        logger.error("Failed to get sell times: %s", exc)
        return jsonify({"success": False, "error": "Erreur lors du calcul des delais"}), 500
