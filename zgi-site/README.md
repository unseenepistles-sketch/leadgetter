# Zenith Global Imports — website

A single-page, dark/premium marketing site built to feel like walking into the
HQ of the company that brings the world's best products to Kenya first. Pure
static HTML/CSS/JS — no build step, loads instantly, works on any static host.

## Run locally

```bash
cd zgi-site
python3 -m http.server 8000
# open http://localhost:8000
```

Deploy anywhere that serves static files (Netlify, Vercel, Cloudflare Pages,
GitHub Pages, or any nginx/Apache docroot). Just upload the `zgi-site/` folder.

## ⚙️ Configure before launch — one place

Open **`assets/js/main.js`** and edit the `CONFIG` block at the very top:

```js
const CONFIG = {
  whatsapp: "254700000000",   // ← your real WhatsApp number, digits only
  socials: { instagram: "...", tiktok: "...", facebook: "" }
};
```

The WhatsApp number powers **every button on the site** (each opens WhatsApp
with a pre-filled message naming the product/intent). Use full international
format, digits only: `0712 345 678` → `254712345678`.

## Things to swap for real data

| What | Where |
|------|-------|
| WhatsApp number & socials | `assets/js/main.js` → `CONFIG` |
| Trust stats (products, customers, etc.) | `index.html` → `.stats` section, `data-count` values. **Use your real numbers.** |
| Reviews | `index.html` → `.reviews` — replace placeholders with real WhatsApp-screenshot testimonials |
| Live stock chips ("24 left", "Sold out") | `index.html` → `.card__stock` on each product |
| FAQ answers (delivery zone, wholesale MOQ, returns) | `index.html` → `.faq` — confirm real policies before launch |
| Product / founder photos | `assets/img/` |
| Brand video | `assets/video/brand.mp4` |
| "Vote what we import next" options | `index.html` → `.soon` section |

## Assets

- `assets/img/` — product shots, founder portraits (auto-cropped), `favicon.svg`
- `assets/video/brand.mp4` — scroll-triggered brand video (autoplays muted in view)

## Notes

- **Pay-on-delivery** is the strongest trust lever — only advertise it where you
  can honour it every time. The copy currently scopes it to your delivery zone.
- Fonts load from Google Fonts with a graceful system-serif fallback.
- All motion respects `prefers-reduced-motion` and disables cursor/tilt effects
  on touch devices.
