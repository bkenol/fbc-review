# Deployment runbook

Stub. Claude Code fills this in while working through `DEPLOYMENT-PROMPT.md`.

It must end up containing, with real values rather than placeholders:

- Every `gcloud` and `firebase` command actually run, in order
- The GCP project id, Cloud Run service name and region, bucket name, Artifact Registry path
- Every IAM binding and which identity holds it, and why each one is needed
- Every environment variable the service reads, and where its value comes from
- How to add or remove someone from the email allowlist
- How to roll back to a previous Cloud Run revision
- The DNS records for `review.omniflexfitness.com` as they were actually entered
- Cold start latency as measured, and the monthly cost estimate at 50 reviews
- Anything decided during the build that the prompt did not cover
