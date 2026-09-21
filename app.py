import os
import io
import stripe
import anthropic
from flask import (
    Flask, request, render_template, session,
    redirect, url_for, send_file
)
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def ai_generate_report(data: dict) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""You are a senior client communications consultant. Create a stunning, professional HTML client progress report.

DETAILS:
- Client: {data['client_name']}
- Reporting period: {data['period']}
- Prepared by: {data['your_name']}

WORK COMPLETED THIS PERIOD:
{data['work_done']}

KEY METRICS & RESULTS:
{data['metrics']}

HIGHLIGHTS / WINS:
{data['wins']}

CHALLENGES (if any):
{data.get('challenges', 'No major blockers this period.')}

NEXT STEPS:
{data['next_steps']}

Generate a complete, standalone HTML document. Requirements:
- Embed ALL CSS (no external stylesheets except Google Fonts via @import in <style>)
- Color palette: #1E293B (dark navy header), #2563EB (primary blue), #10B981 (green for wins), #F8FAFC (background), #334155 (body text)
- Fonts: Inter from Google Fonts
- Sections: Header with logo area + period, Executive Summary, Work Completed, Key Results (metric cards), Wins, Next Steps, Footer
- Metric cards: 3-4 cards with large number + label, styled with border-left accent
- Professional, polished — this should look like a $200/hr consultant's deliverable
- Print-ready (include @media print styles)
- NO markdown fences, NO ```html — return raw HTML starting with <!DOCTYPE html>
"""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["GET", "POST"])
def generate():
    if request.method == "GET":
        return render_template("generator.html", error=None)

    data = {
        "client_name":  request.form.get("client_name", "").strip(),
        "period":       request.form.get("period", "").strip(),
        "your_name":    request.form.get("your_name", "").strip(),
        "work_done":    request.form.get("work_done", "").strip(),
        "metrics":      request.form.get("metrics", "").strip(),
        "wins":         request.form.get("wins", "").strip(),
        "challenges":   request.form.get("challenges", "").strip(),
        "next_steps":   request.form.get("next_steps", "").strip(),
    }

    # Validation
    required = ["client_name", "period", "your_name", "work_done", "metrics", "wins", "next_steps"]
    if any(not data[k] for k in required):
        return render_template("generator.html", error="Please fill in all required fields.", prefill=data)

    free_used = session.get("free_used", False)
    paid      = session.get("paid", False)

    if not free_used or paid:
        try:
            report_html = ai_generate_report(data)
        except Exception as e:
            return render_template("generator.html", error=f"Generation error: {e}", prefill=data)

        session["free_used"] = True
        session["last_report"] = report_html
        session["last_client"] = data["client_name"]
        session.modified = True

        return render_template(
            "result.html",
            report_html=report_html,
            client_name=data["client_name"],
            paid=paid,
        )

    # Free report already used — save data and send to Stripe
    session["pending_data"] = data
    session.modified = True
    return redirect(url_for("checkout"))


@app.route("/checkout")
def checkout():
    if not stripe.api_key or not STRIPE_PRICE_ID:
        return render_template(
            "generator.html",
            error="Payment not configured yet. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in your .env file.",
        )
    try:
        cs = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="payment",
            success_url=APP_URL + "/payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=APP_URL + "/generate",
        )
        return redirect(cs.url, code=303)
    except stripe.error.StripeError as e:
        return render_template("generator.html", error=f"Payment error: {e.user_message}")


@app.route("/payment-success")
def payment_success():
    sid = request.args.get("session_id", "")
    if sid:
        try:
            cs = stripe.checkout.Session.retrieve(sid)
            if cs.payment_status == "paid":
                session["paid"] = True
                pending = session.pop("pending_data", None)
                session.modified = True

                if pending:
                    report_html = ai_generate_report(pending)
                    session["last_report"] = report_html
                    session["last_client"] = pending.get("client_name", "")
                    session.modified = True
                    return render_template(
                        "result.html",
                        report_html=report_html,
                        client_name=pending.get("client_name", ""),
                        paid=True,
                    )
        except Exception:
            pass
    return redirect(url_for("generate"))


@app.route("/download-pdf")
def download_pdf():
    """
    Client-side PDF via browser print is the primary path.
    This endpoint serves the raw HTML so the browser can print it.
    """
    report_html = session.get("last_report")
    if not report_html:
        return redirect(url_for("generate"))

    buf = io.BytesIO(report_html.encode("utf-8"))
    client_name = session.get("last_client", "client")
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"report-{client_name.lower().replace(' ', '-')}-{date_str}.html"

    return send_file(
        buf,
        download_name=filename,
        as_attachment=True,
        mimetype="text/html",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
