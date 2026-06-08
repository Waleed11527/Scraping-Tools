# Website Data Scraper

A small local web app for pasting a website link, extracting common listing data, and downloading the result as an Excel workbook.

## Run

```bash
python3 app.py
```

Open:

```text
http://127.0.0.1:8787
```

## Deploy On Railway

1. Create a Railway service from the GitHub repository.
2. Railway will read `railway.json` and run `python3 -u app.py`.
3. Open the service's **Settings > Networking** and click **Generate Domain**.

Railway provides `PORT` automatically. Configure these variables for paid plans:

- `STRIPE_SECRET_KEY`: Stripe secret key used to create and verify Checkout sessions.
- `BILLING_SECRET`: a long random secret used to sign paid-access cookies.

Configure these variables for Google login:

- `GOOGLE_CLIENT_ID`: OAuth 2.0 web client ID from Google Cloud.
- `GOOGLE_CLIENT_SECRET`: OAuth 2.0 web client secret from Google Cloud.
- `AUTH_SECRET`: a long random secret used to sign login sessions.
- `DATA_DIR`: persistent account-data directory. Use `/data` when a Railway volume is mounted there.

Add this authorized redirect URI to the Google OAuth web client:

```text
https://YOUR-RAILWAY-DOMAIN/auth/google/callback
```

The plan rules are enforced by the server:

- Free: 3 scrapes per Google account, with up to 50 listings per scrape.
- Category ($5/month): complete Single Category scrapes for one month.
- Full Access ($10/month): complete Overall Website and Single Category scrapes for one month.
- `Waleedk4pak@gmail.com`: lifetime Full Access.

For Railway persistence, add a volume mounted at `/data`, then set `DATA_DIR=/data`.

## What It Extracts

- Listing-like cards with title, price, image, URL, and description
- Product detail pages for each same-site listing URL
- Model number, SKU, MPN, brand, availability, detail price, and detail description where available
- Salla homepages and category pages through the public storefront API, including category discovery and infinite-scroll products
- Salla product API fields such as product ID, prices, category, stock flags, weight, GTIN, images, and raw product JSON
- Shopify stores and collection pages through their public JSON feeds, including pagination, all product images, variants, SKUs, barcodes, prices, and raw product JSON
- All page images
- All page links
- Metadata tags
- JSON-LD structured data

Some websites block automated scraping or render all listing data with JavaScript after the page loads. Salla storefront categories are handled with a dedicated API mode; other JavaScript-heavy sites may need a browser-based scraper upgrade.

The app visits up to 250 product detail pages per generic scrape, up to 500 Salla API products per category, and up to 5000 Salla or Shopify products total to avoid accidentally hammering a website.
