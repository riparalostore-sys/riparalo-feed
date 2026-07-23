#!/usr/bin/env python3
"""
Genera il feed XML per TradeTracker leggendo il catalogo dall'endpoint PUBBLICO
di Shopify (/products.json). Non serve nessun token, nessuna app, nessun secret.

Include SOLO i prodotti che hanno almeno una variante disponibile,
e per ognuno SOLO le varianti disponibili.

Uso:
    python generate_feed.py docs/riparalo.xml
"""

import os
import sys
import time
import requests
from xml.sax.saxutils import escape

STORE_URL = "https://www.riparalo.store"
BRAND_NAME = "Riparalo Store"
OUTPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "docs/riparalo.xml"

# Quando una variante e' disponibile, questo valore finisce in <stock>.
# A TradeTracker serve solo sapere che c'e' disponibilita'.
STOCK_DISPONIBILE = 1


# Shopify blocca le richieste che sembrano fatte da bot: usiamo header da browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


def get_con_retry(url, tentativi=5):
    """GET con ritentativi: Shopify a volte risponde 503 alle prime chiamate."""
    attesa = 5
    for n in range(1, tentativi + 1):
        try:
            resp = requests.get(url, timeout=45, headers=HEADERS)
            if resp.status_code == 200:
                return resp
            print(f"    tentativo {n}: HTTP {resp.status_code}, riprovo tra {attesa}s")
        except requests.RequestException as e:
            print(f"    tentativo {n}: errore di rete ({e}), riprovo tra {attesa}s")
        time.sleep(attesa)
        attesa = min(attesa * 2, 60)
    raise RuntimeError(f"Impossibile scaricare {url} dopo {tentativi} tentativi")


def fetch_all_products():
    """Scarica tutti i prodotti pubblicati, paginando finche' la pagina e' vuota."""
    products = []
    page = 1
    while True:
        url = f"{STORE_URL}/products.json?limit=250&page={page}"
        resp = get_con_retry(url)
        batch = resp.json().get("products", [])
        if not batch:
            break
        products.extend(batch)
        print(f"  pagina {page}: {len(batch)} prodotti")
        page += 1
        time.sleep(2)
        if page > 50:  # salvagente anti-loop
            break
    return products


def option_index(product, option_name):
    """Posizione (0-based) di un'opzione tra option1/option2/option3."""
    for opt in product.get("options", []):
        if str(opt.get("name", "")).strip().lower() == option_name.lower():
            return int(opt.get("position", 1)) - 1
    return None


def get_option_value(variant, index):
    if index is None:
        return ""
    return variant.get(f"option{index + 1}") or ""


def tag(name, value):
    """Restituisce <name>valore</name>, oppure <name/> se vuoto."""
    if value is None or str(value).strip() == "":
        return f"<{name}/>"
    return f"<{name}>{escape(str(value))}</{name}>"


def product_image(product):
    images = product.get("images") or []
    if images:
        return images[0].get("src", "")
    return ""


def build_variant_xml(variant, color, memoria):
    price = float(variant.get("price") or 0)
    compare_at = variant.get("compare_at_price")
    compare_at = float(compare_at) if compare_at else None

    in_saldo = compare_at is not None and compare_at > price
    from_price = f"{compare_at:.2f}" if in_saldo else ""
    discount = f"{compare_at - price:.2f}" if in_saldo else ""

    return f"""            <variant>
                <ID>{variant['id']}</ID>
                {tag('sku', variant.get('sku'))}
                <price>{price:.2f}</price>
                {tag('fromPrice', from_price)}
                <stock>{STOCK_DISPONIBILE}</stock>
                {tag('discount', discount)}
                <sale>{'Yes' if in_saldo else 'No'}</sale>
                {tag('color', color)}
                {tag('memoria', memoria)}
            </variant>"""


def build_product_xml(product):
    disponibili = [v for v in product.get("variants", []) if v.get("available")]
    if not disponibili:
        return None  # nessuna variante disponibile -> prodotto escluso dal feed

    idx_color = option_index(product, "Color")
    idx_memoria = option_index(product, "Memoria")

    variants_xml = "\n".join(
        build_variant_xml(
            v,
            get_option_value(v, idx_color),
            get_option_value(v, idx_memoria),
        )
        for v in disponibili
    )

    titolo = product.get("title", "")
    url_prodotto = f"{STORE_URL}/products/{product.get('handle', '')}"

    return f"""    <product>
        <ID>{product['id']}</ID>
        {tag('name', titolo)}
        {tag('description', product.get('body_html') or '')}
        {tag('productURL', url_prodotto)}
        {tag('imageURL', product_image(product))}
        {tag('categories', product.get('product_type') or titolo)}
        {tag('brand', BRAND_NAME)}
        {tag('model', titolo)}
        <variants>
{variants_xml}
        </variants>
    </product>"""


def main():
    print("Scarico il catalogo da Shopify (endpoint pubblico)...")
    prodotti = fetch_all_products()
    print(f"Prodotti pubblicati totali: {len(prodotti)}")

    blocchi = []
    n_varianti = 0
    for p in prodotti:
        blocco = build_product_xml(p)
        if blocco:
            blocchi.append(blocco)
            n_varianti += sum(1 for v in p.get("variants", []) if v.get("available"))

    print(f"Prodotti nel feed: {len(blocchi)}")
    print(f"Varianti disponibili nel feed: {n_varianti}")

    if not blocchi:
        print("ATTENZIONE: nessun prodotto disponibile, il feed non viene sovrascritto.")
        sys.exit(1)

    xml = "<?xml version='1.0' encoding='UTF-8'?>\n<products>\n" + "\n".join(blocchi) + "\n</products>\n"

    cartella = os.path.dirname(OUTPUT_PATH)
    if cartella:
        os.makedirs(cartella, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"Feed scritto in: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
