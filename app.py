import base64
import csv
import io
import json
import os
import re
import secrets
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_session import Session
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
EMAIL_PATTERN = re.compile(r"{{\s*([A-Za-z_][\w.-]*)\s*}}")


def is_local_oauth():
    configured_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
    return configured_uri.startswith(
        ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
    )


if is_local_oauth():
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32)),
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.environ.get(
        "SESSION_FILE_DIR",
        "/tmp/mailroom_sessions" if os.environ.get("VERCEL") else str(BASE_DIR / ".flask_session"),
    ),
    SESSION_FILE_THRESHOLD=100,
    SESSION_FILE_MODE=0o600,
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)
Session(app)


def credentials_config():
    raw_config = os.environ.get("GOOGLE_CLIENT_CONFIG")
    if raw_config:
        return json.loads(raw_config)

    credentials_path = Path(
        os.environ.get("GOOGLE_CREDENTIALS_FILE", BASE_DIR / "credentials.json")
    )
    if not credentials_path.exists():
        raise RuntimeError(
            "Google OAuth credentials are not configured. Set "
            "GOOGLE_CLIENT_CONFIG or GOOGLE_CREDENTIALS_FILE."
        )
    with credentials_path.open(encoding="utf-8") as credentials_file:
        return json.load(credentials_file)


def redirect_uri():
    configured_uri = os.environ.get("GOOGLE_REDIRECT_URI")
    if configured_uri:
        return configured_uri
    return url_for("oauth_callback", _external=True)


def create_flow(state=None):
    flow = Flow.from_client_config(
        credentials_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri()
    return flow


def credentials_from_session():
    token_data = session.get("google_credentials")
    if not token_data:
        return None

    credentials = Credentials.from_authorized_user_info(token_data, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        session["google_credentials"] = json.loads(credentials.to_json())
    return credentials if credentials.valid else None


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if credentials_from_session() is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_csrf():
    return secrets.compare_digest(
        request.form.get("csrf_token", ""),
        session.get("csrf_token", ""),
    )


def parse_recipients(raw_csv):
    reader = csv.DictReader(io.StringIO(raw_csv.strip()))
    if not reader.fieldnames:
        raise ValueError("Add a CSV header row and at least one recipient.")
    fieldnames = [field.strip().lower() for field in reader.fieldnames]
    if any(not field for field in fieldnames):
        raise ValueError("Every CSV column must have a header.")
    if len(fieldnames) != len(set(fieldnames)):
        raise ValueError("CSV headers must be unique, ignoring capitalization.")
    if "email" not in fieldnames:
        raise ValueError("The CSV must contain an 'email' column.")

    recipients = []
    for row in reader:
        normalized = {
            (key.strip().lower() if key else ""): (value or "").strip()
            for key, value in row.items()
        }
        if normalized.get("email"):
            recipients.append(normalized)
    if not recipients:
        raise ValueError("No recipient rows with an email address were found.")
    return fieldnames, recipients


def read_text_input(form_name, file_name):
    uploaded_file = request.files.get(file_name)
    if uploaded_file and uploaded_file.filename:
        return uploaded_file.read().decode("utf-8-sig")
    return request.form.get(form_name, "")


def personalize(template, recipient):
    def replace(match):
        return recipient.get(match.group(1).lower(), match.group(0))

    return EMAIL_PATTERN.sub(replace, template)


def send_message(service, sender, recipient, subject, html):
    message = MIMEMultipart("alternative")
    message["to"] = recipient
    message["from"] = sender
    message["subject"] = subject
    message.attach(MIMEText(html, "html", "utf-8"))
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId="me", body={"raw": raw_message}
    ).execute()


@app.context_processor
def inject_template_values():
    return {"csrf_token": csrf_token(), "account_email": session.get("account_email")}


@app.get("/")
def index():
    if credentials_from_session() is None:
        return render_template("login.html")
    return render_template(
        "compose.html",
        template=session.get("draft_template", ""),
        recipients=session.get("draft_recipients", ""),
        subject=session.get("draft_subject", ""),
    )


@app.get("/login")
def login():
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(authorization_url)


@app.get("/oauth/callback")
def oauth_callback():
    state = session.pop("oauth_state", None)
    code_verifier = session.pop("oauth_code_verifier", None)
    if not state:
        flash("Your login session expired. Please try again.", "error")
        return redirect(url_for("index"))
    try:
        flow = create_flow(state)
        flow.code_verifier = code_verifier
        flow.fetch_token(authorization_response=request.url)
        session["google_credentials"] = json.loads(flow.credentials.to_json())
        service = build("gmail", "v1", credentials=flow.credentials)
        profile = service.users().getProfile(userId="me").execute()
        session["account_email"] = profile.get("emailAddress", "Logged-in Gmail account")
        session["csrf_token"] = secrets.token_urlsafe(32)
    except Exception:
        app.logger.exception("Gmail OAuth callback failed")
        flash("Gmail login failed. Check the OAuth redirect URI and try again.", "error")
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    if valid_csrf():
        session.clear()
    return redirect(url_for("index"))


@app.post("/preview")
@login_required
def preview():
    if not valid_csrf():
        flash("Invalid form session. Refresh the page and try again.", "error")
        return redirect(url_for("index"))
    try:
        recipients = read_text_input("recipients", "recipient_file")
        template = read_text_input("template", "template_file")
        fieldnames, recipient_rows = parse_recipients(recipients)
        session["draft_template"] = template
        session["draft_recipients"] = recipients
        session["draft_subject"] = request.form.get("subject", "")
        variables = sorted(set(EMAIL_PATTERN.findall(template)))
        return render_template(
            "compose.html",
            template=template,
            recipients=recipients,
            subject=request.form.get("subject", ""),
            fieldnames=fieldnames,
            recipient_rows=recipient_rows,
            variables=variables,
            preview_html=personalize(template, recipient_rows[0]),
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("index"))


@app.post("/send")
@login_required
def send():
    if not valid_csrf():
        flash("Invalid form session. Refresh the page and try again.", "error")
        return redirect(url_for("index"))
    try:
        recipients = read_text_input("recipients", "recipient_file")
        template = read_text_input("template", "template_file").strip()
        _, recipient_rows = parse_recipients(recipients)
        subject = request.form.get("subject", "").strip()
        if not template or not subject:
            raise ValueError("Subject and HTML template are required.")

        credentials = credentials_from_session()
        service = build("gmail", "v1", credentials=credentials)
        sender = session.get("account_email")
        sent = 0
        failures = []
        for recipient in recipient_rows:
            try:
                send_message(
                    service,
                    sender,
                    recipient["email"],
                    subject,
                    personalize(template, recipient),
                )
                sent += 1
                time.sleep(0.25)
            except Exception:
                app.logger.exception("Failed to send email to %s", recipient["email"])
                failures.append(recipient["email"])
        if failures:
            flash(
                f"Sent {sent} of {len(recipient_rows)} messages. Failed: "
                + ", ".join(failures),
                "error",
            )
        else:
            flash(f"Sent {sent} message(s) from {sender}.", "success")
        return redirect(url_for("index"))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
