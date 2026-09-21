# ReportKit — Setup Guide

AI client progress report generator. First report free, then $1.99/report or $12/mo.

---

## Deploy in 10 minutes (Railway — free tier works)

### 1. Push to GitHub
```bash
cd reportkit
git init && git add . && git commit -m "initial"
gh repo create reportkit --public --push
```
_(or use GitHub Desktop — drag the `reportkit` folder in)_

### 2. Deploy on Railway
1. Go to **railway.app** → New Project → Deploy from GitHub repo
2. Select your `reportkit` repo
3. Railway auto-detects the Dockerfile and deploys

### 3. Set environment variables in Railway
In your project → **Variables** tab, add:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `STRIPE_SECRET_KEY` | dashboard.stripe.com → Developers → API keys |
| `STRIPE_PRICE_ID` | See step 4 below |
| `APP_URL` | Your Railway public URL (e.g. `https://reportkit.railway.app`) |
| `SECRET_KEY` | Any random 32-char string (use: `openssl rand -hex 16`) |

### 4. Create your Stripe product
1. **dashboard.stripe.com** → Products → Add product
2. Name: "ReportKit Report" → Price: **$1.99** → One time
3. Add a second price: **$12.00** → Recurring → Monthly
4. Copy the Price ID (`price_xxxxx`) for the $1.99 one → paste as `STRIPE_PRICE_ID`
5. For the subscription: update `STRIPE_PRICE_ID` when you're ready to offer it

### 5. Get your domain
Railway gives you a free `.railway.app` domain. In Railway → Settings → Domains → Generate domain.
Custom domain: add a CNAME record pointing your domain to Railway's DNS.

---

## Get your first customers (no ads)

**Where to post** (you press send — pick one to start):

1. **r/freelance** — "I built a tool that writes your client reports in 60 seconds. First one's free." Link + screenshot. Post Tuesday–Thursday, 9am–12pm UTC.
2. **IndieHackers** — Post in "Show IH" with revenue goal and backstory. IH loves "I built this for my own problem" stories.
3. **Designer/dev Facebook groups** — Same pitch, image of the example report.

**What works:** Show the example report (screenshot or link to the preview on the landing page). The visual does the selling.

---

## What the tool does

- User fills in: client name, period, work done, metrics, wins, next steps
- Claude generates a full HTML report with header, executive summary, metric cards, wins, next steps
- User prints to PDF or downloads HTML
- First report: free (session-based, no account needed)
- Subsequent reports: Stripe checkout → $1.99 → unlocked for session

---

## Revenue math

| Customers | Monthly revenue |
|---|---|
| 50 reports/mo | ~$100 |
| 10 subscribers | $120/mo |
| 50 subscribers | $600/mo |
| 200 subscribers | $2,400/mo |

Average freelancer has 5-15 clients. If they use it monthly = 5-15 reports/mo each.
