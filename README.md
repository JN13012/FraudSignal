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

The baseline is TF-IDF with logistic regression, scored with 5-fold out-of-fold predictions. Gemini is `gemini-3.5-flash-lite` with temperature 0. Full results are in [results/evaluation.md](results/evaluation.md).

| Model | Accuracy | Macro F1 | Fraud precision | Fraud recall | Fraud F1 |
|---|---|---|---|---|---|
| Baseline | 0.844 | 0.845 | 0.730 | 0.900 | 0.806 |
| Gemini | 0.950 | 0.965 | 0.968 | 1.000 | 0.984 |

Gemini confusion matrix (rows are true labels):

| | fraud | spam | legitimate | uncertain |
|---|---|---|---|---|
| fraud | 60 | 0 | 0 | 0 |
| spam | 0 | 59 | 1 | 0 |
| legitimate | 2 | 0 | 52 | 6 |

Human review caught all 9 errors: 32 of 180 messages were flagged, and all 148 unflagged answers were correct.

**Error analysis**

- Gemini's false fraud alerts are the two genuine family emergency SMS messages, which combine urgency with a request to call.
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

The real weakness was not the verdict. Before hardening, two attacks made the recommendation tell the reader to call the attacker's number, with no human review.

Hardening added random per-request delimiters, a `prompt_injection` signal, stricter instructions, rejection of very short quotes, and a code check that replaces advice reusing a contact detail from the message.

These attacks are static and were written for this project, on one model. A result of 0/48 does not show that the system resists an adaptive attacker. The contact check also misses details that are spelled out or split.

## Latency and cost

Measured over the 180 messages with the Gemini API free tier:

- Latency per analysis: median 1.24 s, p95 1.71 s, max 2.08 s.
- Estimated cost: $0.000650 per message, from measured tokens and list prices read on 2026-11-01. No charge was incurred, so this is an estimate and not an invoice.

## Project status

| Part | Status |
|---|---|
| Core, CLI and automated tests | Done; tests run without network access |
| Evaluation and injection benchmark | Measured with the Gemini API |
| Answer language | Fix added after the measurements above, not yet fully re-measured |
| FastAPI service | Tested with a fake client; `/health` checked locally |
| Container and Vertex AI client | Written, not run |
| Cloud Run | Prepared in [docs/deploy.md](docs/deploy.md), not deployed |

The evaluated version flagged 61 of 180 answers as apparently written in the wrong language, using a rough stopword check. The answer language is now set by code from the message.

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
