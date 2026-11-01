# Deploying to Cloud Run

These steps were run on 2026-11-01. The service was measured and then removed.

Every command names the project explicitly, because the active `gcloud` configuration
may point elsewhere. Vertex AI and Cloud Run require billing on the project.

```bash
export PROJECT=fraudsignal
export REGION=europe-west1
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
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
  --role roles/aiplatform.user --condition=None
```

Allow the default build identity to build the image:

```bash
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role roles/cloudbuild.builds.builder --condition=None
```

Deploy privately, with a low instance cap. The model was available through the
`global` Vertex AI location, not `europe-west1`:

```bash
gcloud run deploy fraudsignal --source . --project "$PROJECT" --region "$REGION" \
  --no-allow-unauthenticated --max-instances 1 --min-instances 0 \
  --service-account "fraudsignal-run@$PROJECT.iam.gserviceaccount.com" \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global"
```

Measure latency against the private service:

```bash
export FRAUDSIGNAL_API_TOKEN="$(gcloud auth print-identity-token)"
```

```bash
uv run python -m evaluation.measure_api \
  --url "$(gcloud run services describe fraudsignal --project "$PROJECT" --region "$REGION" --format 'value(status.url)')" \
  --count 20 --delay 2 --output results/cloud_run.jsonl
```

Remove everything afterwards:

```bash
gcloud run services delete fraudsignal --project "$PROJECT" --region "$REGION"
```

```bash
gcloud artifacts repositories delete cloud-run-source-deploy --project "$PROJECT" --location "$REGION"
```

```bash
gcloud storage rm -r "gs://run-sources-$PROJECT-$REGION"
```

```bash
gcloud iam service-accounts delete "fraudsignal-run@$PROJECT.iam.gserviceaccount.com" --project "$PROJECT"
```

```bash
gcloud billing projects unlink "$PROJECT"
```
