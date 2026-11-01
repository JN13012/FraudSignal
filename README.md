# FraudSignal

Decision support for suspicious emails, SMS and WhatsApp messages. FraudSignal sends a message to Gemini and returns validated JSON: a verdict, a confidence score, signals quoted from the text, a summary, a recommendation and a human review flag.

The result helps a person decide. It is not proof of fraud.

## How it works

```text
Message + channel
      │
      ▼
Input checks          reject empty or oversized messages
      │
      ▼
Gemini                fixed instructions, message isolated as untrusted data
      │
      ▼
Pydantic validation   one retry, then an error instead of a default verdict
      │
      ▼
Local checks          verify quotes, block contact details from the message in advice
      │
      ▼
Review rules          needs_human_review is set by code, not by the model
      │
      ▼
JSON result
```

Verdicts are `fraud`, `spam`, `legitimate` and `uncertain`. Spam is unwanted marketing; fraud tries to obtain money, credentials, personal data or access through deception.

A message goes to human review when the verdict is `uncertain`, confidence is below 0.9, a quote is rejected, a `fraud` or `spam` verdict has no valid signal, a prompt injection signal is reported, or the summary or recommendation reuses a link, phone number or email address from the message.

## Example

```bash
printf 'URGENT : votre colis est bloqué. Payez 2 € sur http://colis.example' \
  | uv run fraudsignal analyze --channel sms
```

```json
{
  "verdict": "fraud",
  "confidence": 1.0,
  "signals": [
    {
      "type": "payment_request",
      "quote": "Payez 2 €",
      "explanation": "Demande de paiement d'une somme modique pour récupérer un prétendu colis."
    }
  ],
  "summary": "Ce SMS prétend qu'un colis est bloqué et demande de payer une petite somme via un lien suspect.",
  "recommendation": "Ne cliquez pas sur le lien et ne communiquez aucune information bancaire.",
  "needs_human_review": false
}
```

Output from an earlier version of the analyzer, shortened to one signal.

## Evaluation

The dataset has 180 fictional messages: fraud, spam and legitimate, in French and English, across email, SMS and WhatsApp, with 10 messages per combination. 52 are marked as hard, such as genuine urgent messages or fraud without a link.

The baseline is TF-IDF with logistic regression, scored with 5-fold out-of-fold predictions. Gemini is `gemini-3.5-flash-lite` on Vertex AI with temperature 0. Full results are in [results/evaluation.md](results/evaluation.md).

| Model | Accuracy | Macro F1 | Fraud precision | Fraud recall | Fraud F1 |
|---|---|---|---|---|---|
| Baseline | 0.844 | 0.845 | 0.730 | 0.900 | 0.806 |
| Gemini | 0.939 | 0.952 | 0.984 | 1.000 | 0.992 |

Gemini confusion matrix (rows are true labels):

| | fraud | spam | legitimate | uncertain |
|---|---|---|---|---|
| fraud | 60 | 0 | 0 | 0 |
| spam | 0 | 55 | 5 | 0 |
| legitimate | 1 | 0 | 54 | 5 |

Human review caught all 11 errors: 34 of 180 messages were flagged, and all 146 unflagged answers were correct.

**Error analysis**

- Gemini's single false fraud alert is a genuine family emergency SMS, which combines urgency with a request to call.
- All 5 spam messages it called legitimate are WhatsApp messages from small businesses, where marketing reads like ordinary conversation.
- Its `uncertain` answers are genuine messages with little context, such as password resets and short personal requests.
- The baseline's main weakness is false fraud alerts: 16 legitimate messages were labeled as fraud. It also relies on vocabulary, so it can overfit dataset wording such as `STOP`.

**These figures are likely optimistic.** The messages were written for this project and do not come from real attackers. The set is small, and the 0.9 confidence threshold and the hardened prompt were chosen after seeing errors on these same messages, so the final figures are not a held-out test.

## Prompt injection

The benchmark adds 12 attack families to 4 fraudulent messages, for 48 attacks: instruction overrides, fake system messages, closing the message delimiter, fake JSON results, false authority, zero-width characters, Base64 and others. Six benign look-alikes, such as a security newsletter, measure false alarms.

| Measure | Before hardening | After hardening |
|---|---|---|
| Verdict moved away from `fraud` | 0/48 | 0/48 |
| Attacker's marker reached generated text | 2/48 | 0/48 |
| Benign look-alikes called legitimate | 6/6 | 6/6 |
| Attacks sent to human review | 0/48 | 47/48 |

The real weakness was not the verdict. Before hardening, two attacks made the recommendation tell the reader to call the attacker's number, with no human review.

Hardening added random per-request delimiters, a `prompt_injection` signal, stricter instructions, rejection of very short quotes, and a code check that replaces advice reusing a contact detail from the message.

These attacks are static and were written for this project, on one model. A result of 0/48 does not show that the system resists an adaptive attacker. The contact check also misses details that are spelled out or split.

## Deployment, latency and cost

Deployed and measured on Cloud Run on 2026-11-01; the service is no longer running. It ran as a private FastAPI service in `europe-west1`, with a single maximum instance, called Gemini through Vertex AI with its own service identity, and stored no API key. Requests without authorization returned 403. The steps are in [docs/deploy.md](docs/deploy.md).

| Measure | Requests | Median | p95 | Max |
|---|---|---|---|---|
| End-to-end through Cloud Run | 20 | 1.24 s | 1.94 s | 2.98 s |
| Direct Vertex AI call from the development machine | 180 | 1.19 s | 1.71 s | 27.77 s |

All 20 Cloud Run requests succeeded, and the slowest was the first one. Raw samples are in [results/cloud_run.jsonl](results/cloud_run.jsonl).

Estimated model cost is $0.000744 per message, from measured tokens and Gemini API list prices read on 2026-11-01. This is not a billed amount: the deployment ran on Google Cloud trial credit, and Vertex AI prices were not checked separately.

## Answer language

An early check showed answers in the wrong language, sometimes Spanish or Portuguese for English messages. A stricter prompt did not solve it: 61 of 180 answers were still flagged by a rough stopword check. Setting the answer language from code brought this to 0 of 180, using the same check.

## Limitations

- Only message text is analyzed. Sender, email authentication, attachments and link reputation are not checked.
- The model cannot confirm facts. Only a human can verify a payment request through a known channel.
- Confidence is the model's own estimate and is not calibrated.
- Real messages should not be sent through the free tier, where Google may use content to improve its products.
- Results may change with the model version.

## Run it

Requires [uv](https://docs.astral.sh/uv/) and a Gemini API key.

```bash
cp .env.example .env
```

```bash
uv run pytest
```

```bash
uv run python -m evaluation.baseline
```

```bash
uv run python -m evaluation.run_gemini --output results/gemini.jsonl
```

```bash
uv run uvicorn fraudsignal.api:app --env-file .env
```

All dataset messages are fictional, and links use the reserved `.example` domain.
