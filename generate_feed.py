#!/usr/bin/env python3
"""
Genera il feed XML per TradeTracker leggendo il catalogo da Shopify Admin API.
Include SOLO prodotti che hanno almeno una variante con stock disponibile,
e per ognuno SOLO le varianti con stock > 0.

Richiede due variabili d'ambiente:
  SHOPIFY_STORE_DOMAIN   es. "xxxxx.myshopify.com" (il dominio *.myshopify.com, non riparalo.store)
  SHOPIFY_ADMIN_TOKEN    Admin API access token della custom app (scope: read_products)

Output: scrive il file XML nel path passato come primo argomento
(default: docs/riparalo.xml)
"""

import os
import sys
import time
import requests
from xml.sax.saxutils import escape

API_VERSION = "2024-10"
STORE_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
ACCESS_TOKEN = os.environ["SHOPIFY_ADMIN_TOKEN"]
OUTPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "docs/riparalo.xml"
PUBLIC_STORE_URL = os.environ.get("PUBLIC_STORE_URL", "https://www.riparalo.store")
BRAND_NAME = os.environ.get("BRAND_NAME", "Riparalo Store")

BASE_URL = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/products.json"
HEADERS = {"X-Shopify-Access-Token": ACCESS_TOKEN}


def fetch_all_products():
    """Scarica tutti i prodotti paginando con page_info (cursor-based)."""
    products = []
    url = BASE_URL
    params = {"limit": 250, "status": "active"}

    while url:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        products.extend(data.get("products", []))

        # Paginazione via header Link (rel="next")
        link_header = resp.headers.get("Link", "")
        next_url = None
        if link_header:
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
        url = next_url
        params = None  # i parametri sono già inclusi nell'URL di next_url
        time.sleep(0.5)  # rispetta i rate limit di Shopify

    return products


def option_index(product, option_name):
    """Trova la posizione (0-based) di un'opzione (es. 'Color') tra option1/2/3."""
    for opt in product.get("options", []):
        if opt.get("name", "").strip().lower() == option_name.lower():
            return opt.get("position", 1) - 1
    return None


def get_option_value(variant, index):
    if index is None:
        return ""
    key = f"option{index + 1}"
    return variant.get(key) or ""


def find_variant_image(product, variant):
    """Immagine specifica della variante, o featured image del prodotto come fallback."""
    image_id = variant.get("image_id")
    if image_id:
        for img in product.get("images", []):
            if img.get("id") == image_id:
                return img.get("src", "")
    images = product.get("images", [])
    if images:
        return images[0].get("src", "")
    return ""


def build_variant_xml(variant, color, memoria):
    price = float(variant.get("price") or 0)
    compare_at = variant.get("compare_at_price")
    compare_at = float(compare_at) if compare_at else None

    on_sale = compare_at is not None and compare_at > price
    from_price = f"{compare_at:.2f}" if on_sale else ""
    discount = f"{(compare_at - price):.2f}" if on_sale else ""
    sale_flag = "Yes" if on_sale else "No"
    sku = variant.get("sku") or ""

    return f"""            <variant>
                <ID>{variant['id']}</ID>
                <sku>{escape(sku)}</sku>
                <price>{price:.2f}</price>
                <fromPrice>{from_price}</fromPrice>
                <stock>{variant.get('inventory_quantity', 0)}</stock>
                <discount>{discount}</discount>
                <sale>{sale_flag}</sale>
                <color>{escape(color)}</color>
                <memoria>{escape(memoria)}</memoria>
            </variant>"""


def build_product_xml(product):
    color_idx = option_index(product, "Color")
    memoria_idx = option_index(product, "Memoria")

    available_variants = [
        v for v in product.get("variants", [])
        if (v.get("inventory_quantity") or 0) > 0
    ]
    if not available_variants:
        return None  # nessuna variante disponibile -> salta l'intero prodotto

    variants_xml = "\n".join(
        build_variant_xml(
            v,
            get_option_value(v, color_idx),
            get_option_value(v, memoria_idx),
        )
        for v in available_variants
    )

    handle = product.get("handle", "")
    product_url = f"{PUBLIC_STORE_URL}/products/{handle}"
    image_url = product.get("images", [{}])[0].get("src", "") if product.get("images") else ""
    description = product.get("body_html") or ""
    category = product.get("product_type") or product.get("title", "")

    return f"""    <product>
        <ID>{product['id']}</ID>
        <name>{escape(product.get('title', ''))}</name>
        <description>{escape(description)}</description>
        <productURL>{escape(product_url)}</productURL>
        <imageURL>{escape(image_url)}</imageURL>
        <categories>{escape(category)}</categories>
        <brand>{escape(BRAND_NAME)}</brand>
        <model>{escape(product.get('title', ''))}</model>
        <variants>
{variants_xml}
        </variants>
    </product>"""


def main():
    print("Scarico prodotti da Shopify...")
    products = fetch_all_products()
    print(f"Prodotti totali scaricati: {len(products)}")

    product_blocks = []
    for product in products:
        block = build_product_xml(product)
        if block:
            product_blocks.append(block)

    print(f"Prodotti con almeno una variante disponibile: {len(product_blocks)}")

    xml_content = "<?xml version='1.0'?>\n<products>\n" + "\n".join(product_blocks) + "\n</products>\n"

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(xml_content)

    print(f"Feed scritto in: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
