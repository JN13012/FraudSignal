# Deploying to Cloud Run

Not executed yet. Every command names the project explicitly, because the active
`gcloud` configuration may point elsewhere. Vertex AI and Cloud Run require billing
on the project.

```bash
export PROJECT=fraudsignal
export REGION=europe-west1
```

Enable the services and create a dedicated, least-privilege runtime identity:

```bash
gcloud services enable run.googleapis.com aiplatform.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project "$PROJECT"
```

```bash
gcloud iam service-accounts create fraudsignal-run --project "$PROJECT"
```

```bash
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:fraudsignal-run@$PROJECT.iam.gserviceaccount.com" \
  --role roles/aiplatform.user
```

Deploy privately, with a low instance cap to bound cost:

```bash
gcloud run deploy fraudsignal --source . --project "$PROJECT" --region "$REGION" \
  --no-allow-unauthenticated --max-instances 1 --min-instances 0 \
  --service-account "fraudsignal-run@$PROJECT.iam.gserviceaccount.com" \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION"
```

Measure latency against the private service:

```bash
export FRAUDSIGNAL_API_TOKEN="$(gcloud auth print-identity-token)"
```

```bash
uv run python -m evaluation.measure_api \
  --url "$(gcloud run services describe fraudsignal --project "$PROJECT" --region "$REGION" --format 'value(status.url)')" \
  --count 20 --output results/cloud_run.jsonl
```

The real cost is read in the project's billing reports, one to two days later. Check
that the model is available in the chosen region before deploying.
