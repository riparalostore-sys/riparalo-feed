#!/usr/bin/env python3
"""
Genera un feed Google Shopping (RSS 2.0 + namespace g:) leggendo
l'endpoint pubblico products.json di Shopify per riparalo.store.

Filtri applicati:
- Esclude varianti con valore capacita non valido (N/D, n/d, vuoto, -1, malformato)
- Esclude varianti con inventario disponibile <= 0
- Include un prodotto solo se ha almeno una variante valida e disponibile
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

SHOP_DOMAIN = "www.riparalo.store"
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

VALID_CAPACITY_RE = re.compile(r"^\d+\s*(GB|TB)$", re.IGNORECASE)


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


def fetch_with_retry(url, max_retries=4):
    """Esegue la richiesta con retry e attesa crescente in caso di blocco temporaneo."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("products", [])
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (403, 429) and attempt < max_retries:
                wait_seconds = 10 * attempt
                print(f"Tentativo {attempt} fallito ({e.code}), riprovo tra {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue
            raise
    raise last_error


def get_option_value(variant, product, option_name):
    for i, opt in enumerate(product.get("options", []), start=1):
        if opt.get("name", "").strip().lower() == option_name.strip().lower():
            key = f"option{i}"
            return variant.get(key)
    return None


def build_item_xml(product, variant, capacity_value):
    variant_id = variant["id"]
    product_handle = product["handle"]
    title = f"{product['title']} {variant.get('title', '')}".replace(" / ", " ").strip()
    link = f"https://{SHOP_DOMAIN}/products/{product_handle}?variant={variant_id}"

    image = ""
    if variant.get("featured_image") and variant["featured_image"].get("src"):
        image = variant["featured_image"]["src"]
    elif product.get("images"):
        image = product["images"][0]["src"]

    price = variant.get("price", "0.00")
    availability = "in_stock"
    brand = product.get("vendor", "").strip() or "Generico"
    description = re.sub("<[^<]+?>", "", product.get("body_html", "")).strip()
    description = description[:5000] if description else title

    item = f"""
    <item>
      <g:id>{escape(str(variant_id))}</g:id>
      <title>{escape(title)}</title>
      <description>{escape(description)}</description>
      <link>{escape(link)}</link>
      <g:image_link>{escape(image)}</g:image_link>
      <g:condition>refurbished</g:condition>
      <g:availability>{availability}</g:availability>
      <g:price>{price} EUR</g:price>
      <g:brand>{escape(brand)}</g:brand>
      <g:identifier_exists>no</g:identifier_exists>
      <g:google_product_category>267</g:google_product_category>
    </item>"""
    return item


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_FILE
    products = fetch_all_products()
    items_xml = []
    total_variants = 0
    skipped_capacity = 0
    skipped_stock = 0

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


if __name__ == "__main__":
    main()
