<p align="center">
  <img src="static/logo.svg" alt="MedGemma local logo" width="96">
</p>

<h1 align="center">MedGemma local</h1>

<p align="center">
  A private medical assistant that runs entirely on your computer, built on
  Google's <a href="https://developers.google.com/health-ai-developer-foundations/medgemma">MedGemma 1.5</a>
  and <a href="https://ollama.com">Ollama</a>.
</p>

> [!WARNING]
> **Informational use only.** This project is not a medical device and does not replace a
> healthcare professional. MedGemma is not validated for clinical use. In an emergency,
> call 112 / 911.

## Features

- **Runs locally:** questions and images never leave your machine.
- **Health questions:** answers structured as *Summary*, *Possible causes*,
  *What you can do* and *Warning signs*. For vague questions it asks for the missing
  details first.
- **Medical images:** chest X-rays, skin photos, fundus photos, histology, single CT or MRI
  slices and lab reports. Images use their own system prompt, so tasks such as locating
  anatomy or listing lab values work.
- **Lab reports in PDF:** upload the PDF of a lab report and every value is read, classified
  (high, low, normal, dangerous) and explained in plain Spanish, then the model writes a
  summary with questions for the doctor. See [Lab reports](#lab-reports-pdf).
- **Modern web interface:** streamed answers, image upload (button, drag and drop or paste),
  stop button, copy button, dark mode and mobile layout. No external dependencies.
- **Evaluation scripts:** 20 text tests and 10 image tests with automatic checks and an
  HTML report.

## Requirements

- Python 3.10 or later
- [Ollama](https://ollama.com/download) running locally
- The MedGemma 1.5 4B model (about 3.3 GB):

```bash
ollama pull medgemma1.5:4b
```

## Installation

```bash
git clone <your-repo-url> medgemma-local
cd medgemma-local
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Usage

Start the server (Ollama must be running):

```bash
./venv/bin/uvicorn main:app --port 8000
```

Then open <http://localhost:8000>.

### From your phone

Start the server so it accepts connections from your local network:

```bash
./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://<your-computer-ip>:8000` on a phone connected to the same Wi-Fi. On macOS
you can get the IP with `ipconfig getifaddr en0`.

> [!CAUTION]
> The app has no login: with `--host 0.0.0.0` anyone on your network can use it. Only do this
> on a network you trust.

### Streamlit interface (optional)

The previous Streamlit interface still works. It needs the API running on port 8000:

```bash
./venv/bin/streamlit run app.py
```

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Status and model name |
| `POST` | `/chat` | Text question, full answer as JSON |
| `POST` | `/chat/stream` | Text question, streamed answer |
| `POST` | `/chat/image` | Question with an image (multipart form), streamed answer |
| `DELETE` | `/sessions/{id}` | Deletes a conversation |
| `POST` | `/auth/register`, `/auth/login`, `/auth/logout` | Accounts |
| `GET` | `/me` | Plan, free questions left and subscription |
| `GET` | `/plans` | Plans, prices and free questions |
| `POST` | `/billing/checkout` | Subscribes to a plan (test mode) or returns the Stripe payment page |
| `POST` | `/billing/cancel`, `/billing/resume` | Cancels at the end of the period, or undoes it |
| `GET` | `/billing/payments` | Payments of the current user |
| `POST` | `/billing/webhook` | Stripe events (signed) |
| `POST` | `/labs/analyze` | Lab report PDF (multipart): every value read and classified |
| `POST` | `/labs/{id}/summary` | Streams the model's summary of an analysed report |

Streamed responses return the conversation id in the `X-Session-Id` header. Send it back as
`session_id` to continue the conversation. Conversations are kept in memory and are lost
when the server restarts.

## Configuration

The main settings are at the top of [`main.py`](main.py):

| Setting | Default | Description |
|---|---|---|
| `MODEL` | `medgemma1.5:4b` | Ollama model |
| `OPTIONS` | `num_predict: 2000`, `temperature: 0.3`, … | Generation options. `num_predict` includes the model's hidden reasoning, so values that are too low cut answers off |
| `MAX_MESSAGES` | `8` | Previous messages sent to the model |
| `SYSTEM_PROMPT` | — | Prompt for text questions |
| `IMAGE_SYSTEM_PROMPT` | — | Prompt used when the conversation includes an image |

## Accounts, free questions and subscriptions

- A visitor without an account gets 1 answer, then is asked to sign up.
- A new account gets **4 questions and 1 image in total** (`FREE_QUESTIONS`, `FREE_IMAGES`),
  not per day. After that the plans window opens.
- Premium is a subscription that renews by itself: weekly (2,99 €), monthly (7,99 €) or
  yearly (49,99 €). Prices and intervals are in `PLANS` in [`config.py`](config.py).
  Each paid period moves `premium_until` forward, so access always matches what was paid.
  Months and years are calendar ones (31 Jan → 28 Feb → 31 Mar), like Stripe.
- Cancelling keeps Premium until the end of the paid period and stops the next charges.
  Changing plan applies at the next renewal.
- Emergency messages are always answered, with or without questions left.

| Variable | Default | Description |
|---|---|---|
| `BILLING_MODE` | `test` | `test` simulates every charge, renewals included (nothing is charged); `stripe` charges with Stripe; `off` disables checkout |
| `FREE_QUESTIONS` / `FREE_IMAGES` | `4` / `1` | Free questions and images per account, in total |
| `DB_PATH` | `data/app.db` | SQLite database |

In test mode the server checks every 10 minutes (and whenever a user comes back) for
subscriptions whose period has ended, and records one payment per period in `payments`.

### Charging with Stripe

Nothing extra to install: [`stripe_billing.py`](stripe_billing.py) uses the Stripe REST API.

1. In the Stripe dashboard, create a product with three **recurring** prices: 2,99 € weekly,
   7,99 € monthly and 49,99 € yearly. Copy their ids (`price_…`).
2. Add a webhook endpoint `https://<your-domain>/billing/webhook` with the events
   `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`,
   `customer.subscription.updated` and `customer.subscription.deleted`. Copy its signing
   secret (`whsec_…`).
3. Start the server with:

```bash
BILLING_MODE=stripe STRIPE_SECRET_KEY=sk_test_... STRIPE_WEBHOOK_SECRET=whsec_... STRIPE_PRICE_WEEKLY=price_... STRIPE_PRICE_MONTHLY=price_... STRIPE_PRICE_YEARLY=price_... PUBLIC_URL=https://<your-domain> ./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Stripe charges each period itself; every paid invoice reaches the webhook, is stored once
(repeated webhooks are ignored) and extends Premium to the end of that period. Use the
Stripe test keys (`sk_test_…`) and test cards until everything works, and the
[Stripe CLI](https://docs.stripe.com/stripe-cli) (`stripe listen --forward-to localhost:8000/billing/webhook`)
to receive webhooks locally.

## Lab reports (PDF)

The model never reads the PDF, because a 4B model misreads numbers in dense tables. Instead:

1. [`labs.py`](labs.py) extracts the text of the PDF with `pypdf` and finds lines such as
   `Glucosa 105 mg/dL 70 - 110`. It understands:
   - columns, dotted leaders, brackets and the range before the unit;
   - decimal commas, `250.000 /mm3`, mmol/L and other units;
   - `< 200` and `> 60` ranges, flags such as `H`, `L` or `*`;
   - urine tests (Negativo / Positivo) and reports of several pages.
2. Every value is classified in code against **the lab's own range**. The adult range in
   [`lab_tests.py`](lab_tests.py) is used only when the report has none, and never for
   children. Dangerous values (potassium, glucose, haemoglobin, platelets…) get a fixed
   urgent warning.
3. Each result gets a fixed explanation from `lab_tests.py` (about 70 tests): what it measures
   and what a high or low value can mean.
4. The model gets only the names and classifications, never the values or personal data, and
   writes a summary. `NumberGuard` removes any sentence with a number that is not in the report.
5. The results join the conversation, so the person can ask about them afterwards.

The PDF is read in memory and never stored. Name, ID and dates in the PDF are never parsed.
Scanned PDFs and photos have no text: the app says so. Free accounts get one report
(`FREE_LABS`); Premium has no limit.

### Upload security ([`pdf_guard.py`](pdf_guard.py))

Only real, passive PDF lab reports are processed:

1. **Raw bytes, in the server:**
   - size (10 MB) and per-route request limits;
   - `%PDF-` at byte 0 and `%%EOF` at the end;
   - declared type and extension;
   - PDFs with a ZIP or HTML glued on;
   - active content (JavaScript, launching programs, embedded files, forms that send data), also when its name is obfuscated (`/J#61vaScript`).
2. **Isolated reading:** the PDF is read by a separate, throw-away process
   (`python -I pdf_guard.py --worker`) with:
   - a 20 s time limit and a CPU limit, plus a memory limit on Linux;
   - 20 MB decompression limits;
   - a check of the internal structure, which also finds active content hidden in compressed object streams.

   If a crafted PDF hangs or crashes the reader, only that process dies.
3. **Is it a lab report?** The first 3 pages are checked first (known tests, lab vocabulary,
   clinical units). A non-medical document, or a medical one that is not a lab report
   (a discharge report, a prescription), stops there with its own message.
4. **Usage limits:** 20 uploads per hour per account, at most 2 readings at a time.

Refused files never use the free report. The PDF is never stored or served back.

Tests and evaluation:

```bash
./venv/bin/python tests/test_pdf_guard.py   # 33 checks with real malicious PDFs
./venv/bin/python tests/test_labs.py        # 239 checks, no Ollama needed
./venv/bin/python eval/lab_reports.py       # writes 15 invented PDFs to eval/labs/
./venv/bin/python eval/run_lab_eval.py      # real model on 10 reports, HTML report
```

## Evaluation

```bash
./venv/bin/python eval/run_eval.py         # 20 text questions
./venv/bin/python eval/run_image_eval.py   # 10 image tests
```

Each run writes an HTML report (and JSON) to `eval/results/`. Saved answers can be scored
again with the current checks, without calling the model:

```bash
./venv/bin/python eval/run_image_eval.py --rescore eval/results/image_eval_XXXX.json
```

The automatic checks are heuristics. Read the answers in the HTML report before drawing
conclusions.

### Known limitations

From the latest runs with `medgemma1.5:4b`:

- It missed a pneumothorax on a chest X-ray and described it as pneumonia.
- It reads lab values accurately but sometimes compares them wrongly with the reference
  range (for example, calling LDL 168 "below" `< 100`). Do the comparison in code if you
  build on this.
- It does not reliably tell the user to go to the emergency department when an image shows
  an urgent finding.
- Ollama receives single images, so CT and MRI are tested on one slice, not full 3D volumes.

## Project structure

```
main.py                 FastAPI app: API, prompts and Ollama calls
config.py               Free questions, plans, prices and Stripe settings
billing.py              Free questions, subscriptions, renewals and payments
stripe_billing.py       Stripe Checkout and webhook
labs.py                 Lab report PDFs: reading, classification and summary filter
lab_tests.py            About 70 lab tests with explanations, ranges and critical values
pdf_guard.py            Security layer for uploaded PDFs (checks and isolated reading)
static/                 Web interface (HTML, CSS, JS) and logo
app.py                  Optional Streamlit interface
eval/
  prompts.json          Text tests
  run_eval.py           Text evaluation
  image_prompts.json    Image tests and expected results
  run_image_eval.py     Image evaluation
  images/               Test images (see CREDITS.md)
```

## Credits and licences

- MedGemma is provided by Google under the
  [Health AI Developer Foundations terms of use](https://developers.google.com/health-ai-developer-foundations/terms).
  The model is not included in this repository.
- The test images come from Wikimedia Commons under their own licences. Authors and changes
  are listed in [`eval/images/CREDITS.md`](eval/images/CREDITS.md).
# med
# med
