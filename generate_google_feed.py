#!/usr/bin/env python3
"""
Genera un feed Google Shopping (RSS 2.0 + namespace g:) leggendo
l'endpoint pubblico products.json di Shopify per riparalo.com.

Filtri applicati:
- Esclude varianti con valore capacita non valido (N/D, n/d, vuoto, -1, malformato)
- Esclude varianti non disponibili (campo "available" di products.json)
- Include un prodotto solo se ha almeno una variante valida e disponibile

Ottimizzazioni per Google Shopping:
- Titolo: modello + memoria + colore in italiano + "Ricondizionato Premium - Batteria Nuova"
  (solo per i prodotti ricondizionati, cioe' con "Ricondizionato" nel titolo Shopify)
- g:product_highlight: punti di forza (Premium, batteria nuova, garanzia 24 mesi, spedizione, Klarna)
- g:color in italiano, g:item_group_id per raggruppare le varianti dello stesso modello
- g:custom_label_0 = fascia di prezzo, g:custom_label_1 = famiglia prodotto (per segmentare le campagne)
- g:shipping gratuita per l'Italia

ID prodotto (g:id):
- Formato "shopify_ZZ_{ID prodotto}_{ID variante}", identico all'item_id che Shopify
  invia a GA4 (es. shopify_ZZ_15738167361870_59997794140494).
  Serve al remarketing dinamico di Google Ads: gli annunci mostrano il prodotto
  visto dall'utente solo se l'ID del feed coincide con l'item_id degli eventi GA4.
- "ZZ" e' il codice paese che Shopify usa oggi negli eventi GA4. Se in futuro
  Shopify passa a "shopify_IT_...", cambia solo GA4_ITEM_ID_COUNTRY qui sotto.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

SHOP_DOMAIN = "www.riparalo.com"
PRODUCTS_JSON_URL = f"https://{SHOP_DOMAIN}/products.json?limit=250"
DEFAULT_OUTPUT_FILE = "docs/google.xml"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

# Codice paese usato da Shopify nell'item_id degli eventi GA4 (vedi docstring)
GA4_ITEM_ID_COUNTRY = "ZZ"

VALID_CAPACITY_RE = re.compile(r"^\d+\s*(GB|TB)$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Testi di marketing (modificabili qui senza toccare il resto dello script)
# ---------------------------------------------------------------------------

# Suffisso del titolo per i ricondizionati. Google vieta testi promozionali nei
# titoli (prezzi, spedizione, "offerta"...), quindi qui solo attributi del prodotto.
TITLE_REFURB_MAIN = "Ricondizionato Premium"
TITLE_REFURB_TAIL = "Batteria Nuova"

# Punti di forza mostrati sotto l'annuncio (max 10, max 150 caratteri ciascuno)
HIGHLIGHTS_REFURB = [
    "Condizioni Premium: grado Best Prime, estetica come nuova",
    "Batteria nuova inclusa, autonomia al 100%",
    "Garanzia 24 mesi",
    "Spedizione Express gratuita e tracciata in 24/72 ore",
    "Pagamento anche in 3 rate con Klarna",
]

# Frase aggiunta in testa alla descrizione dei ricondizionati
DESCRIPTION_INTRO_REFURB = (
    "Ricondizionato in condizioni Premium (grado Best Prime) con batteria nuova al 100% "
    "e garanzia di 24 mesi. Spedizione Express gratuita e tracciata in 24/72 ore."
)

# Spedizione gratuita in Italia (deve coincidere con quanto mostrato sul sito)
SHIPPING_COUNTRY = "IT"
SHIPPING_PRICE = "0.00 EUR"

# Fasce di prezzo per custom_label_0 (limite superiore escluso)
PRICE_BANDS = [
    (400, "fino-400"),
    (600, "400-599"),
    (800, "600-799"),
    (1000, "800-999"),
]
PRICE_BAND_TOP = "1000-plus"

# ---------------------------------------------------------------------------
# Colori: inglese -> italiano (chiavi in minuscolo, senza "(product)")
# ---------------------------------------------------------------------------

COLOR_MAP = {
    # Titanio (Pro 15 / 16 / 17)
    "natural titanium": "Titanio Naturale",
    "black titanium": "Titanio Nero",
    "white titanium": "Titanio Bianco",
    "blue titanium": "Titanio Blu",
    "titanium blue": "Titanio Blu",
    "desert titanium": "Titanio Sabbia",
    # Colori Apple storici
    "midnight": "Mezzanotte",
    "starlight": "Galassia",
    "space black": "Nero Siderale",
    "space gray": "Grigio Siderale",
    "space grey": "Grigio Siderale",
    "graphite": "Grafite",
    "deep purple": "Viola Scuro",
    "pacific blue": "Blu Pacifico",
    "sierra blue": "Azzurro Sierra",
    "alpine green": "Verde Alpino",
    "sky blue": "Azzurro Cielo",
    "cloud white": "Bianco Nuvola",
    "light gold": "Oro Chiaro",
    "deep blue": "Blu Intenso",
    "cosmic orange": "Arancione Cosmico",
    "mist blue": "Blu Nebbia",
    "sage": "Salvia",
    "lavender": "Lavanda",
    "ultramarine": "Blu Oltremare",
    "teal": "Verde Acqua",
    "red": "Rosso",
    "product red": "Rosso",
    # Colori base
    "black": "Nero",
    "white": "Bianco",
    "silver": "Argento",
    "gold": "Oro",
    "blue": "Blu",
    "green": "Verde",
    "pink": "Rosa",
    "yellow": "Giallo",
    "purple": "Viola",
    "gray": "Grigio",
    "grey": "Grigio",
    "orange": "Arancione",
    # Samsung
    "phantom black": "Nero Phantom",
    "phantom black/lavender": "Nero Phantom / Lavanda",
    "cobalt violet": "Viola Cobalto",
    "onyx black": "Nero Onice",
    "marble grey": "Grigio Marmo",
    "amber yellow": "Giallo Ambra",
    "titanium gray": "Titanio Grigio",
    "titanium black": "Titanio Nero",
    "titanium violet": "Titanio Viola",
    "titanium yellow": "Titanio Giallo",
    "cream": "Crema",
}


def translate_color(value: str) -> str:
    """Traduce il colore in italiano; se non e' in mappa lo lascia com'e'."""
    if not value:
        return ""
    v = value.strip()
    if v.lower() in ("n/d", "nd", "-1"):
        return ""
    key = re.sub(r"\(?product\)?", "product", v, flags=re.IGNORECASE)
    key = re.sub(r"\s+", " ", key).strip().lower()
    if key in COLOR_MAP:
        return COLOR_MAP[key]
    # "(Product) Red" -> "product red", "Product (Red)" -> "product red"
    key_clean = re.sub(r"[()]", "", key).strip()
    key_clean = re.sub(r"\s+", " ", key_clean)
    if key_clean in COLOR_MAP:
        return COLOR_MAP[key_clean]
    return v


def is_valid_capacity(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    if v.lower() in ("n/d", "nd", "-1", ""):
        return False
    if v.upper().count("GB") > 1 or v.upper().count("TB") > 1:
        return False
    return bool(VALID_CAPACITY_RE.match(v))


def fetch_all_products():
    products = []
    page = 1
    while True:
        url = f"{PRODUCTS_JSON_URL}&page={page}"
        batch = fetch_with_retry(url)
        if not batch:
            break
        products.extend(batch)
        page += 1
    return products


# Codici su cui vale la pena ritentare: rate limit (429), forbidden temporaneo (403)
# e tutti gli errori server, incluso il 503 con cui Shopify blocca gli IP cloud (GitHub).
RETRY_STATUS = {403, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def fetch_with_retry(url, max_retries=6):
    """Esegue la richiesta con retry e attesa crescente in caso di blocco temporaneo."""
    last_error = None
    wait_seconds = 8
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("products", [])
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in RETRY_STATUS and attempt < max_retries:
                print(f"Tentativo {attempt} fallito (HTTP {e.code}), riprovo tra {wait_seconds}s...")
                time.sleep(wait_seconds)
                wait_seconds = min(wait_seconds * 2, 90)
                continue
            raise
        except urllib.error.URLError as e:
            last_error = e
            if attempt < max_retries:
                print(f"Tentativo {attempt} fallito (rete: {e.reason}), riprovo tra {wait_seconds}s...")
                time.sleep(wait_seconds)
                wait_seconds = min(wait_seconds * 2, 90)
                continue
            raise
    raise last_error


def get_option_value(variant, product, *option_names):
    """Valore dell'opzione cercata per nome (accetta piu' nomi alternativi)."""
    wanted = {n.strip().lower() for n in option_names}
    for i, opt in enumerate(product.get("options", []), start=1):
        if str(opt.get("name", "")).strip().lower() in wanted:
            return variant.get(f"option{i}")
    return None


def is_refurbished(product) -> bool:
    return "ricondizionat" in product.get("title", "").lower()


def base_model_name(product_title: str) -> str:
    """'Apple iPhone 15 Pro Max Ricondizionato' -> 'Apple iPhone 15 Pro Max'."""
    name = re.sub(r"\bricondizionat[oaie]\b", "", product_title, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip(" -")


def build_title(product, capacity, color_it) -> str:
    # Ordine pensato per il troncamento negli annunci (Google mostra i primi 30-70
    # caratteri): prima modello e memoria, poi "Ricondizionato Premium", poi il colore
    # e per ultima "Batteria Nuova", che compare comunque negli highlight.
    model = base_model_name(product["title"])
    parts = [model, capacity.replace(" ", "")]
    if is_refurbished(product):
        parts.append(TITLE_REFURB_MAIN)
    if color_it:
        parts.append(color_it)
    title = " ".join(p for p in parts if p)
    if is_refurbished(product):
        title = f"{title} - {TITLE_REFURB_TAIL}"
    return title[:150]


def price_band(price: float) -> str:
    for limit, label in PRICE_BANDS:
        if price < limit:
            return label
    return PRICE_BAND_TOP


def product_family(product) -> str:
    """Famiglia per custom_label_1: iphone-pro-max, iphone-pro, iphone-plus, iphone, samsung-fold, ..."""
    t = product.get("title", "").lower()
    if "iphone" in t:
        if "pro max" in t:
            return "iphone-pro-max"
        if " pro" in t:
            return "iphone-pro"
        if "plus" in t:
            return "iphone-plus"
        if "air" in t:
            return "iphone-air"
        if "mini" in t:
            return "iphone-mini"
        return "iphone"
    if "galaxy" in t or "samsung" in t:
        if "fold" in t:
            return "samsung-fold"
        if "flip" in t:
            return "samsung-flip"
        return "samsung"
    if "macbook" in t:
        return "macbook"
    if "ipad" in t:
        return "ipad"
    return "altro"


def build_feed_id(product, variant) -> str:
    """ID del feed allineato all'item_id di GA4: shopify_ZZ_{prodotto}_{variante}."""
    return f"shopify_{GA4_ITEM_ID_COUNTRY}_{product['id']}_{variant['id']}"


def build_item_xml(product, variant, capacity_value):
    variant_id = variant["id"]
    feed_id = build_feed_id(product, variant)
    product_handle = product["handle"]
    refurb = is_refurbished(product)

    color_raw = get_option_value(variant, product, "Color", "Colore") or ""
    color_it = translate_color(color_raw)

    title = build_title(product, capacity_value, color_it)
    link = f"https://{SHOP_DOMAIN}/products/{product_handle}?variant={variant_id}"

    image = ""
    if variant.get("featured_image") and variant["featured_image"].get("src"):
        image = variant["featured_image"]["src"]
    elif product.get("images"):
        image = product["images"][0]["src"]

    price = variant.get("price", "0.00")
    try:
        price_value = float(price)
    except (TypeError, ValueError):
        price_value = 0.0

    brand = product.get("vendor", "").strip() or "Generico"

    description = re.sub("<[^<]+?>", " ", product.get("body_html", "") or "")
    description = re.sub(r"\s+", " ", description).strip()
    if refurb:
        description = f"{DESCRIPTION_INTRO_REFURB} {description}".strip()
    description = description[:5000] if description else title

    highlights_xml = ""
    if refurb:
        highlights_xml = "".join(
            f"\n      <g:product_highlight>{escape(h[:150])}</g:product_highlight>"
            for h in HIGHLIGHTS_REFURB[:10]
        )

    color_xml = f"\n      <g:color>{escape(color_it)}</g:color>" if color_it else ""

    item = f"""
    <item>
      <g:id>{escape(feed_id)}</g:id>
      <g:item_group_id>{escape(str(product["id"]))}</g:item_group_id>
      <title>{escape(title)}</title>
      <description>{escape(description)}</description>
      <link>{escape(link)}</link>
      <g:image_link>{escape(image)}</g:image_link>
      <g:condition>refurbished</g:condition>
      <g:availability>in_stock</g:availability>
      <g:price>{price} EUR</g:price>
      <g:brand>{escape(brand)}</g:brand>{color_xml}
      <g:identifier_exists>no</g:identifier_exists>
      <g:google_product_category>267</g:google_product_category>{highlights_xml}
      <g:shipping>
        <g:country>{SHIPPING_COUNTRY}</g:country>
        <g:price>{SHIPPING_PRICE}</g:price>
      </g:shipping>
      <g:custom_label_0>{price_band(price_value)}</g:custom_label_0>
      <g:custom_label_1>{product_family(product)}</g:custom_label_1>
      <g:custom_label_2>{escape(base_model_name(product["title"]))}</g:custom_label_2>
    </item>"""
    return item


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_FILE
    products = fetch_all_products()
    items_xml = []
    total_variants = 0
    skipped_capacity = 0
    skipped_stock = 0
    not_refurb_titles = set()

    for product in products:
        for variant in product.get("variants", []):
            total_variants += 1
            capacity_value = get_option_value(variant, product, "Memoria")

            if not is_valid_capacity(capacity_value):
                skipped_capacity += 1
                continue

            available = variant.get("available", False)
            if not available:
                skipped_stock += 1
                continue

            if not is_refurbished(product):
                not_refurb_titles.add(product.get("title", ""))

            items_xml.append(build_item_xml(product, variant, capacity_value))

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
  <channel>
    <title>Riparalo Store - Feed Google Shopping</title>
    <link>https://{SHOP_DOMAIN}</link>
    <description>Feed prodotti ricondizionati per Google Merchant Center</description>
    {''.join(items_xml)}
  </channel>
</rss>
"""

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Prodotti letti: {len(products)}")
    print(f"Varianti totali: {total_variants}")
    print(f"Escluse per capacita non valida: {skipped_capacity}")
    print(f"Escluse per stock/non disponibili: {skipped_stock}")
    print(f"Varianti incluse nel feed: {len(items_xml)}")
    if not_refurb_titles:
        print("Attenzione - prodotti senza 'Ricondizionato' nel titolo (titolo base, niente testi Premium):")
        for t in sorted(not_refurb_titles):
            print(f"  - {t}")


if __name__ == "__main__":
    main()
