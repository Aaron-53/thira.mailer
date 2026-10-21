# Mailroom

Mailroom is a small web app for sending personalized HTML email through the
Gmail account used to sign in. It provides one browser flow:

1. Sign in with Google.
2. Enter an email subject.
3. Paste an HTML template.
4. Paste a CSV recipient list.
5. Review detected variables and a rendered preview.
6. Send from the signed-in Gmail address.

The sender address is always taken from the authenticated Gmail profile. It is
not entered or configured in the UI.

## Run locally

Create an environment and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Set a stable secret for the Flask session:

```powershell
$env:FLASK_SECRET_KEY = "replace-with-a-long-random-value"
```

The app automatically loads a `.env` file from the project directory when it
starts. Restart Flask after changing `.env`.

Start the app:

```powershell
python app.py
```

Open `http://127.0.0.1:5000`. For local testing, add this exact OAuth
redirect URI to the Google OAuth client:

```text
http://127.0.0.1:5000/oauth/callback
```

Keep `SESSION_COOKIE_SECURE=0` for local HTTP testing. The app permits OAuth
insecure transport only when `GOOGLE_REDIRECT_URI` is a loopback HTTP address.
Hosted deployments must use HTTPS and should set `SESSION_COOKIE_SECURE=1`.

The existing `credentials.json` is read by default. You can use another file:

```powershell
$env:GOOGLE_CREDENTIALS_FILE = "C:\path\to\credentials.json"
```

## Hosted deployment

## Deploy on Vercel

This project is ready for Vercel. Vercel detects the Flask `app` instance in
[`app.py`](./app.py), and [`vercel.json`](./vercel.json) configures the
serverless function timeout. Static assets are also copied to `public/` for
Vercel's CDN.

### 1. Import the project

1. Push this folder to a private GitHub repository, or deploy it with the
   Vercel CLI.
2. In Vercel, choose **Add New → Project**.
3. Import the repository.
4. Leave the framework preset as **Other** if Vercel does not select Python
   automatically.
5. Do not add a custom build command.
6. Deploy after adding the environment variables below.

With the Vercel CLI, from this folder:

```powershell
npm install -g vercel
vercel login
vercel
```

Use `vercel --prod` for a production deployment.

### 2. Add `mailer.x.in`

In the Vercel project dashboard:

1. Open **Settings → Domains**.
2. Add:

   ```text
   mailer.x.in
   ```

3. Vercel will show the DNS record required by your domain provider.
4. At the provider managing `x.in` DNS, create the record Vercel requests. For
   a typical setup this is:

   ```text
   Type: CNAME
   Name: mailer
   Value: cname.vercel-dns.com
   ```

5. Wait for DNS verification and HTTPS certificate provisioning.

Do not move the existing `x.in` record. This adds only the `mailer` subdomain
to this separate Vercel project.

### 3. Configure Vercel environment variables

Open **Settings → Environment Variables** and add these for **Production**:

```text
FLASK_SECRET_KEY=<long-random-secret>
GOOGLE_REDIRECT_URI=https://mailer.x.in/oauth/callback
SESSION_COOKIE_SECURE=1
```

For Google credentials, add `GOOGLE_CLIENT_CONFIG` as a **Secret**. Its value
must be the complete, single-line contents of a Google OAuth **Web application**
client JSON file, for example:

```text
{"web":{"client_id":"...","project_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","client_secret":"...","redirect_uris":["https://mailer.x.in/oauth/callback"],"javascript_origins":["https://mailer.x.in"]}}
```

Do not add `GOOGLE_CREDENTIALS_FILE` in Vercel. Do not commit or upload
`.env`, `credentials.json`, or any OAuth secret to GitHub. After changing
environment variables, create a new deployment because Vercel applies changes
to new deployments.

### 4. Configure Google OAuth

Use a Google OAuth client with application type **Web application**:

```text
Authorized JavaScript origins:
https://mailer.x.in

Authorized redirect URI:
https://mailer.x.in/oauth/callback
```

The redirect URI must match `GOOGLE_REDIRECT_URI` exactly. Enable the Gmail API,
configure the OAuth consent screen, add the Gmail `gmail.send` scope, and add
your Google account as a test user while the app is in testing mode.

### 5. Deploy again

After adding the variables and configuring DNS/OAuth, deploy again from Vercel.
Then open:

```text
https://mailer.x.in
```

The Vercel deployment uses `/tmp` for short-lived server-side sessions. This
is sufficient for the OAuth and compose flow, but Vercel Functions are
serverless and should not be used for long-running background campaigns.

## Other hosted deployment

Use a Google OAuth **Web application** client for any other hosted deployment.
Add the exact public callback URL to the client's authorized redirect URIs:

```text
https://your-domain.example/oauth/callback
```

Configure these environment variables on the host:

- `FLASK_SECRET_KEY`: a long, random, persistent value.
- `GOOGLE_REDIRECT_URI`: the public callback URL above.
- `GOOGLE_CREDENTIALS_FILE`: path to the OAuth client JSON, or
  `GOOGLE_CLIENT_CONFIG`: the complete JSON configuration as a secret.
- `SESSION_FILE_DIR`: a private writable directory for server-side sessions
  when the host does not provide a default writable filesystem.

Do not commit OAuth client JSON, access tokens, or session files. The existing
client file in this folder is a local secret and should be rotated if it has
ever been shared.

On a Linux host, a typical process command is:

```bash
gunicorn --bind 0.0.0.0:8000 app:app
```

Use HTTPS in production. The OAuth callback, session cookie, and Gmail
authorization should not be exposed over plain HTTP.

## Input format

The recipient data is pasted as CSV and must contain an `email` column. Any
other column can be used as a template variable:

```csv
email,name,company
person@example.com,Alex,Example Inc.
another@example.com,Sam,Example Co.
```

Templates use double-brace variables matching CSV column names:

```html
<h1>Hi {{name}}</h1>
<p>Thanks for being part of {{company}}.</p>
```

The app shows detected template variables, the CSV columns, and a preview
personalized for the first recipient before sending.

## Gmail authorization

The app requests only the Gmail `gmail.send` scope. On the first sign-in, the
user grants access and Gmail returns an OAuth token. Credentials are kept in
server-side Flask sessions rather than placed directly in the browser cookie.
The app refreshes an expired token when a refresh token is available.
The OAuth PKCE verifier is also kept in the server-side session until the
callback completes.

Google Cloud setup:

1. Enable the Gmail API.
2. Configure the OAuth consent screen.
3. Create a Web application OAuth client for hosted use.
4. Add the callback URL to authorized redirect URIs.
5. Keep the OAuth client configuration in an environment secret or a private
   file on the host.

## Existing files

The previous command-line sender and document conversion assets remain in the
folder for reuse:

- `app.py`: Flask web application.
- `templates/`: login and compose pages.
- `static/style.css`: web UI styles.
- `bulk_email_sender.py`: previous command-line Gmail sender.
- `bulk_email_sender_simple.py`: previous duplicate sender implementation.
- `docxToHTML.py`: DOCX-to-HTML conversion utility.
- `requirements.txt`: Python dependencies.
- `24KGMail.html`, `email_template.html`, and `template.html`: reusable HTML
  templates.
- `sample_recipients.csv` and `earlybird.csv`: existing recipient data.
- `template.docx`: existing DOCX source.

The website does not use the old hardcoded sender, recipient file, or subject
settings. They can be removed later after the web flow is accepted.
