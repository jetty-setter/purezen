"""
patch_cards.py — run on EC2 from any directory.
Updates service card HTML template in script.js and redesigns all card
CSS in styles.css to the rich editorial system.

Usage:
    python3 patch_cards.py
"""

import re

FRONTEND = "/home/ec2-user/purezen-frontend"
CARD_BG     = "#eae4de"
CARD_BG_HOV = "#e2dbd4"

# ─────────────────────────────────────────────
# 1. script.js — restructure card HTML template
# ─────────────────────────────────────────────
sjs = open(f"{FRONTEND}/script.js").read()

old_card = """      return `
        <article class="service-card">
          <div class="service-card-top">
            <div>
              <h3>${escapeHtml(name)}</h3>
              <p class="service-meta">${escapeHtml(description)}</p>
            </div>
            <span class="service-price">${escapeHtml(price)}</span>
          </div>

          <div class="service-tags">
            <span class="service-tag">${escapeHtml(duration)}</span>
            <span class="service-tag">${escapeHtml(category)}</span>
            ${roomType ? `<span class="service-tag">${escapeHtml(roomType)}</span>` : ""}
          </div>

          <p class="staff-line">${escapeHtml(consultation)}</p>
        </article>
      `;"""

new_card = """      const roomMeta = roomType ? ` · ${escapeHtml(roomType)}` : "";
      return `
        <article class="service-card">
          <div class="scard-cat">${escapeHtml(category)}</div>
          <h3 class="scard-name">${escapeHtml(name)}</h3>
          <p class="scard-desc">${escapeHtml(description)}</p>
          <div class="scard-foot">
            <span class="scard-duration">${escapeHtml(duration)}${roomMeta}</span>
            <span class="scard-price">${escapeHtml(price)}</span>
          </div>
        </article>
      `;"""

if old_card in sjs:
    sjs = sjs.replace(old_card, new_card, 1)
    print("script.js: card template patched")
else:
    print("script.js: card template NOT FOUND — skipping")

open(f"{FRONTEND}/script.js", "w").write(sjs)


# ─────────────────────────────────────────────
# 2. styles.css — unified card design system
# ─────────────────────────────────────────────
css = open(f"{FRONTEND}/styles.css").read()

# service-card block
css = re.sub(
    r'\.service-card \{.*?\}',
    f""".service-card {{
  background: {CARD_BG};
  border-radius: var(--radius-lg);
  padding: 32px 30px 28px;
  display: flex;
  flex-direction: column;
  min-height: 256px;
  transition: background 0.2s ease, transform 0.22s ease;
}}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .service-card replaced")

# service-card:hover
css = re.sub(
    r'\.service-card:hover \{.*?\}',
    f""".service-card:hover {{
  background: {CARD_BG_HOV};
  transform: translateY(-3px);
}}""",
    css, count=1
)
print("styles.css: .service-card:hover replaced")

# Remove old sub-elements no longer used
css = re.sub(r'\.service-card-top \{.*?\}', '', css, flags=re.DOTALL)
css = re.sub(r'\.service-card h3 \{[^}]*\}', '', css)
css = re.sub(r'\.service-tags \{.*?\}', '', css, flags=re.DOTALL)
css = re.sub(r'\.service-tag \{.*?\}', '', css, flags=re.DOTALL)
print("styles.css: old service-card sub-elements removed")

# Replace service-price
css = re.sub(
    r'\.service-price \{.*?\}',
    """.service-price, .scard-price {
  font-family: "Playfair Display", serif;
  font-size: 0.92rem;
  font-style: italic;
  color: #9a8f89;
  white-space: nowrap;
}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .service-price replaced")

# New scard classes — insert after .service-card:hover block
new_scard = """
.scard-cat {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--rose-deep);
  margin-bottom: 12px;
}

.scard-name {
  font-family: "Playfair Display", serif;
  font-size: 1.48rem;
  font-weight: 700;
  color: var(--heading);
  line-height: 1.18;
  margin-bottom: 14px;
}

.scard-desc {
  font-size: 0.84rem;
  line-height: 1.75;
  color: var(--charcoal-soft);
  flex: 1;
}

.scard-foot {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid rgba(43, 38, 36, 0.1);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.scard-duration {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #9a8f89;
}

"""

# Inject after .service-card:hover block
hover_match = re.search(r'\.service-card:hover \{[^}]*\}', css)
if hover_match:
    end = hover_match.end()
    css = css[:end] + new_scard + css[end:]
    print("styles.css: new scard classes injected")

# feature-card
css = re.sub(
    r'\.feature-card \{.*?\}',
    f""".feature-card {{
  background: {CARD_BG};
  border-radius: var(--radius-lg);
  padding: 34px 30px;
  transition: background 0.2s ease;
}}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .feature-card replaced")

# feature-number
css = re.sub(
    r'\.feature-number \{.*?\}',
    """.feature-number {
  display: block;
  margin-bottom: 18px;
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.22em;
  color: var(--rose-deep);
}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .feature-number replaced")

# Shared h3 selector — remove service-card from it, restyle
css = re.sub(
    r'\.feature-card h3,\s*\.ritual-content h3,\s*\.visit-card h3,\s*\.service-card h3 \{.*?\}',
    """.feature-card h3,
.ritual-content h3,
.visit-card h3 {
  font-family: "Playfair Display", serif;
  font-size: 1.28rem;
  font-weight: 700;
  color: var(--heading);
  line-height: 1.2;
  margin: 0 0 12px;
}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: shared h3 selector updated")

# visit-card
css = re.sub(
    r'\.visit-card \{.*?\}',
    f""".visit-card {{
  background: {CARD_BG};
  border-radius: var(--radius-lg);
  padding: 34px 30px;
  transition: background 0.2s ease;
}}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .visit-card replaced")

# point-card
css = re.sub(
    r'\.point-card \{.*?\}',
    f""".point-card {{
  padding: 18px 20px;
  background: {CARD_BG};
  border-radius: var(--radius-md);
  color: var(--heading);
  font-weight: 600;
  transition: background 0.2s ease;
}}""",
    css, flags=re.DOTALL, count=1
)
print("styles.css: .point-card replaced")

# Hover states for feature/visit/point
hover_block = f"""
.feature-card:hover,
.visit-card:hover,
.point-card:hover {{
  background: {CARD_BG_HOV};
}}

"""
css = css.replace('.ritual-grid {', hover_block + '.ritual-grid {', 1)
print("styles.css: hover states for feature/visit/point added")

open(f"{FRONTEND}/styles.css", "w").write(css)
print("\nAll done. Upload both files to S3:")
print(f"  aws s3 cp {FRONTEND}/styles.css s3://purezen-spa-site-087107998132-us-east-1-an/styles.css --cache-control 'no-cache, no-store, must-revalidate'")
print(f"  aws s3 cp {FRONTEND}/script.js s3://purezen-spa-site-087107998132-us-east-1-an/script.js --cache-control 'no-cache, no-store, must-revalidate'")
