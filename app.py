import os
import atexit
from datetime import datetime, date
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import stripe
import sendgrid
from sendgrid.helpers.mail import Mail

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

database_url = os.environ.get('DATABASE_URL', 'sqlite:///purrebot.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')
APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'purrebot@purrebot.no')
FREE_LIMIT = 3


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    stripe_customer_id = db.Column(db.String(100))
    subscription_active = db.Column(db.Boolean, default=False)
    free_invoices_used = db.Column(db.Integer, default=0)
    bank_account = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    invoices = db.relationship('Invoice', backref='user', lazy=True, cascade='all, delete-orphan')


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    client_name = db.Column(db.String(200), nullable=False)
    client_email = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(500))
    amount_nok = db.Column(db.Float, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='unpaid')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reminders = db.relationship('ReminderLog', backref='invoice', lazy=True, cascade='all, delete-orphan')


class ReminderLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    reminder_type = db.Column(db.String(50), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def current_user():
    return User.query.get(session['user_id']) if 'user_id' in session else None


def send_email(to_email, subject, html_content):
    if not SENDGRID_API_KEY:
        print(f'[EMAIL] To: {to_email} | Subject: {subject}')
        return True
    try:
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(from_email=FROM_EMAIL, to_emails=to_email,
                       subject=subject, html_content=html_content)
        sg.send(message)
        return True
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')
        return False


def build_reminder_email(invoice, reminder_type, sender_email):
    amount = f'{invoice.amount_nok:,.0f}'.replace(',', ' ')
    due_str = invoice.due_date.strftime('%d.%m.%Y')
    desc_row = f'<tr><td class="l">Beskrivelse</td><td>{invoice.description}</td></tr>' if invoice.description else ''

    user = invoice.user
    bank_row = f'<tr><td class="l" style="padding:9px 14px;border:1px solid #e2e8f0;font-weight:600;color:#334155">Kontonummer</td><td style="padding:9px 14px;border:1px solid #e2e8f0;color:#334155"><strong>{user.bank_account}</strong></td></tr>' if user and user.bank_account else ''

    table = f'''
    <table style="border-collapse:collapse;width:100%;max-width:420px;margin:20px 0;font-size:14px">
      <tr style="background:#f8fafc"><td class="l" style="padding:9px 14px;border:1px solid #e2e8f0;font-weight:600;color:#334155">Kunde</td><td style="padding:9px 14px;border:1px solid #e2e8f0;color:#334155">{invoice.client_name}</td></tr>
      <tr><td class="l" style="padding:9px 14px;border:1px solid #e2e8f0;font-weight:600;color:#334155">Beløp</td><td style="padding:9px 14px;border:1px solid #e2e8f0;color:#334155"><strong>{amount} kr</strong></td></tr>
      <tr style="background:#f8fafc"><td class="l" style="padding:9px 14px;border:1px solid #e2e8f0;font-weight:600;color:#334155">Forfallsdato</td><td style="padding:9px 14px;border:1px solid #e2e8f0;color:#334155">{due_str}</td></tr>
      {desc_row}
      {bank_row}
    </table>'''

    templates = {
        'before_3': {
            'subject': f'Påminnelse: Faktura på {amount} kr forfaller om 3 dager',
            'heading': 'Faktura forfaller om 3 dager',
            'color': '#2563EB',
            'body': f'<p>Hei {invoice.client_name},</p><p>Dette er en vennlig påminnelse om at du har en utestående faktura som forfaller <strong>om 3 dager</strong>.</p>{table}<p>Vennligst sørg for betaling innen forfallsdato.</p>'
        },
        'due_today': {
            'subject': f'Faktura på {amount} kr forfaller i dag',
            'heading': 'Faktura forfaller i dag',
            'color': '#F59E0B',
            'body': f'<p>Hei {invoice.client_name},</p><p>Dette er en påminnelse om at følgende faktura forfaller <strong>i dag</strong>.</p>{table}<p>Vennligst betal innen i dag for å unngå forsinkelsesrente.</p>'
        },
        'overdue_7': {
            'subject': f'PURRING: Faktura på {amount} kr er forfalt',
            'heading': 'Faktura er forfalt — 1. purring',
            'color': '#EF4444',
            'body': f'<p>Hei {invoice.client_name},</p><p>Vi registrerer at følgende faktura ikke er betalt. Den er nå <strong>7 dager forfalt</strong>.</p>{table}<p>Vennligst betal snarest mulig. Kontakt oss om du har spørsmål.</p>'
        },
        'overdue_14': {
            'subject': f'SISTE PURRING: Faktura på {amount} kr — betaling kreves',
            'heading': 'Siste purring — 14 dager forfalt',
            'color': '#991B1B',
            'body': f'<p>Hei {invoice.client_name},</p><p>Dette er vår siste purring. Fakturaen er nå <strong>14 dager forfalt</strong>. Manglende betaling kan føre til inkasso.</p>{table}<p>Vennligst betal umiddelbart eller ta kontakt.</p>'
        }
    }

    t = templates[reminder_type]
    html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family:system-ui,sans-serif;background:#f8fafc;margin:0;padding:20px">
    <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:10px;border:1px solid #e2e8f0;overflow:hidden">
      <div style="background:{t['color']};padding:20px 28px">
        <p style="color:white;font-size:18px;font-weight:700;margin:0">{t['heading']}</p>
      </div>
      <div style="padding:24px 28px;color:#334155;font-size:15px;line-height:1.6">
        {t['body']}
        <p style="margin-top:24px;color:#64748b;font-size:13px">— Sendt via Purrebot på vegne av {sender_email}</p>
      </div>
    </div></body></html>'''

    return t['subject'], html


def check_and_send_reminders():
    with app.app_context():
        today = date.today()
        invoices = Invoice.query.filter_by(status='unpaid').all()
        sent = 0
        for invoice in invoices:
            days_diff = (invoice.due_date - today).days
            sent_types = {r.reminder_type for r in invoice.reminders}
            user = User.query.get(invoice.user_id)
            schedule = [
                ('before_3', days_diff == 3),
                ('due_today', days_diff == 0),
                ('overdue_7', days_diff == -7),
                ('overdue_14', days_diff == -14),
            ]
            for rtype, condition in schedule:
                if condition and rtype not in sent_types:
                    subject, html = build_reminder_email(invoice, rtype, user.email)
                    if send_email(invoice.client_email, subject, html):
                        db.session.add(ReminderLog(invoice_id=invoice.id, reminder_type=rtype))
                        sent += 1
        if sent:
            db.session.commit()
        print(f'[SCHEDULER] Checked {len(invoices)} invoices, sent {sent} reminders')


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=check_and_send_reminders, trigger='interval', hours=1, id='reminders')
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password:
            flash('Fyll inn alle felt.', 'error')
            return render_template('register.html')
        if len(password) < 8:
            flash('Passordet må være minst 8 tegn.', 'error')
            return render_template('register.html')
        if User.query.filter_by(email=email).first():
            flash('Denne e-postadressen er allerede registrert.', 'error')
            return render_template('register.html')
        user = User(email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        flash('Velkommen til Purrebot!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Feil e-post eller passord.', 'error')
            return render_template('login.html')
        session['user_id'] = user.id
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    today = date.today()
    invoices = Invoice.query.filter_by(user_id=user.id).order_by(Invoice.due_date).all()
    unpaid = [i for i in invoices if i.status == 'unpaid']
    paid = [i for i in invoices if i.status == 'paid']
    total_outstanding = sum(i.amount_nok for i in unpaid)
    free_left = max(0, FREE_LIMIT - user.free_invoices_used) if not user.subscription_active else None
    return render_template('dashboard.html', user=user, invoices=invoices,
                           unpaid=unpaid, paid=paid, today=today,
                           total_outstanding=total_outstanding, free_left=free_left)


@app.route('/invoices/add', methods=['GET', 'POST'])
@login_required
def add_invoice():
    user = current_user()
    if not user.subscription_active and user.free_invoices_used >= FREE_LIMIT:
        flash('Du har brukt de 3 gratis fakturaene. Oppgrader for ubegrenset tilgang.', 'error')
        return redirect(url_for('subscribe'))
    if request.method == 'POST':
        client_name = request.form.get('client_name', '').strip()
        client_email = request.form.get('client_email', '').strip()
        description = request.form.get('description', '').strip()
        amount_str = request.form.get('amount_nok', '').replace(',', '.').replace(' ', '')
        due_date_str = request.form.get('due_date', '')
        errors = []
        if not client_name: errors.append('Kundenavn mangler.')
        if not client_email: errors.append('Kundens e-post mangler.')
        if not amount_str: errors.append('Beløp mangler.')
        if not due_date_str: errors.append('Forfallsdato mangler.')
        amount_nok = None
        due_date = None
        if amount_str:
            try:
                amount_nok = float(amount_str)
                if amount_nok <= 0:
                    errors.append('Beløpet må være større enn 0.')
            except ValueError:
                errors.append('Ugyldig beløp.')
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                errors.append('Ugyldig dato.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('add_invoice.html')
        invoice = Invoice(user_id=user.id, client_name=client_name,
                          client_email=client_email, description=description,
                          amount_nok=amount_nok, due_date=due_date)
        db.session.add(invoice)
        if not user.subscription_active:
            user.free_invoices_used += 1
        db.session.commit()
        flash(f'Faktura for {client_name} lagt til. Purringer sendes automatisk.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('add_invoice.html')


@app.route('/invoices/<int:invoice_id>/paid', methods=['POST'])
@login_required
def mark_paid(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.user_id != session['user_id']:
        return '', 403
    invoice.status = 'paid'
    db.session.commit()
    flash(f'Faktura for {invoice.client_name} markert som betalt ✓', 'success')
    return redirect(url_for('dashboard'))


@app.route('/invoices/<int:invoice_id>/delete', methods=['POST'])
@login_required
def delete_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.user_id != session['user_id']:
        return '', 403
    name = invoice.client_name
    db.session.delete(invoice)
    db.session.commit()
    flash(f'Faktura for {name} slettet.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    user = current_user()
    if request.method == 'POST':
        bank_account = request.form.get('bank_account', '').strip().replace(' ', '').replace('.', '')
        if bank_account and not bank_account.isdigit():
            flash('Kontonummeret kan kun inneholde sifre.', 'error')
        else:
            user.bank_account = bank_account or None
            db.session.commit()
            flash('Innstillinger lagret.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', user=user)


@app.route('/subscribe')
@login_required
def subscribe():
    user = current_user()
    if user.subscription_active:
        return redirect(url_for('dashboard'))
    return render_template('subscribe.html', user=user)


@app.route('/create-checkout', methods=['POST'])
@login_required
def create_checkout():
    user = current_user()
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        db.session.commit()
    checkout = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=['card'],
        line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
        mode='subscription',
        success_url=f'{APP_URL}/subscribe/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{APP_URL}/subscribe',
    )
    return redirect(checkout.url)


@app.route('/subscribe/success')
@login_required
def subscribe_success():
    user = current_user()
    user.subscription_active = True
    db.session.commit()
    flash('Abonnement aktivert! Du har nå ubegrenset tilgang til Purrebot.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return '', 400
    etype = event['type']
    customer_id = event['data']['object'].get('customer')
    user = User.query.filter_by(stripe_customer_id=customer_id).first() if customer_id else None
    if user:
        if etype == 'customer.subscription.created':
            user.subscription_active = True
        elif etype in ('customer.subscription.deleted', 'customer.subscription.paused'):
            user.subscription_active = False
        db.session.commit()
    return '', 200


@app.route('/run-reminders')
def run_reminders():
    check_and_send_reminders()
    return 'Reminders checked.', 200


@app.route('/health')
def health():
    return 'ok', 200


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
