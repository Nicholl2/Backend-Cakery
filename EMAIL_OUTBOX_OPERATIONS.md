# Email outbox operations

## Deployment model

`vercel.json` sends HTTP traffic to `api/index.py`; this is a serverless API deployment and does not provide an always-running process. Do not start the infinite polling worker inside an API function. This repository now supports a bounded invocation (`python -m app.services.email_worker --once --limit 20`) and includes `.github/workflows/email-outbox.yml`, which runs that batch every 15 minutes and can also be started manually. It connects directly to the same database and SMTP service; no public worker endpoint is added.

The repo also has a Docker image whose default command runs Uvicorn. On an always-on Docker host, deploy a second container from the same image with the worker command and `--restart unless-stopped`. Do not assume the Vercel runtime provides that process lifecycle. If GitHub Actions is unavailable, use another external scheduler that can run this bounded command with the required secrets; on Vercel Cron, first verify the account plan and function duration limits, and configure a protected invocation mechanism before using it.

Vercel's current Cron limits vary by plan (Hobby is daily; Pro and Enterprise can run as often as once per minute), and scheduled functions remain subject to function duration limits. See [Cron usage and pricing](https://vercel.com/docs/cron-jobs/usage-and-pricing) and [Function limits](https://vercel.com/docs/functions/limitations).

## Configuration

Configure SMTP/application values in the API deployment and the corresponding GitHub repository Actions secrets/variables. `EMAIL_OUTBOX_ADMIN_TOKEN` belongs only in the restricted operator environment used for the CLI; it is not needed by API requests or the scheduled worker. Never put credentials in this file or workflow source.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Same production database used by the API |
| `ENVIRONMENT=production` | Enables production settings validation |
| `SECRET_KEY`, `SERVICE_API_KEY` | Required non-default application secrets because app settings are loaded at process start |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server and port; workflow defaults port to 587 |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP authentication |
| `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | Sender identity; production requires sender email |
| `SMTP_USE_TLS` | Whether SMTP STARTTLS is enabled; defaults to true in workflow |
| `SMTP_OPERATIONAL_INBOX` | Optional operational seller inbox where configured by the existing event producer |
| `EMAIL_OUTBOX_MAX_ATTEMPTS` | Attempts before an event becomes `failed`; workflow defaults to 10 |
| `EMAIL_OUTBOX_POLL_SECONDS` | Delay for the continuous worker only; bounded batch mode does not poll |
| `EMAIL_OUTBOX_LEASE_SECONDS` | Claim lease lifetime; defaults to 300 seconds and must exceed expected SMTP send duration |
| `EMAIL_OUTBOX_ADMIN_TOKEN` | Required by the local operations CLI; store in the operator secret manager, not in API routes |

For a new, empty database, bootstrap it first using the documented command in `README.md`. For an existing database with a recorded Alembic revision, upgrade once before enabling scheduled delivery:

```sh
alembic upgrade head
```

The complete revision chain creates the outbox and audit tables, adds worker lease fields, and adds nullable `payments.pg_order_id` with a unique index. Existing payment rows remain `NULL`; payment callbacks and refreshes for those rows now fail closed until their exact order reference is reconciled from a trusted Midtrans record and backfilled. Do not synthesize a reference from the invoice number or expected format. For a database without an Alembic revision, follow the existing-database adoption procedure in `README.md`; do not run the chain from its historical root. Downgrade of the payment migration drops the reference column and permanently loses values written after upgrade; take a database backup and stop payment writes before any downgrade.

## Start and restart

For the configured Vercel-style deployment, place the `Email outbox batch` workflow on the default branch, enable Actions, and set its required Actions secrets and variables. Each run processes no more than 20 due events and exits. Failed sends are recorded by the worker and retried according to the existing backoff; a subsequent scheduled run can pick them up when due. Actions scheduling is best-effort, so monitor workflow run failures and schedule delays.

For a persistent Docker host, use the same built image and inject the same runtime environment as the API. Example (replace the image reference with the deployed image):

```sh
docker run -d --name toti-email-worker --restart unless-stopped \
  --env-file /secure/path/toti-backend.env \
  ghcr.io/your-org/toti-backend:release \
  python -m app.services.email_worker
```

The existing image's healthcheck targets the API, so it is not a worker healthcheck. Monitor the worker container process and database backlog separately. Do not publish the operations CLI or outbox details as an HTTP endpoint.

## Backlog monitoring and controlled recovery

Run these commands from a trusted operator shell with production database access and `EMAIL_OUTBOX_ADMIN_TOKEN` configured for the CLI. Each command prompts for the token without echoing it. Restrict shell/database access to authorized operators. Aggregate status includes `pending`, `sending`, `failed`, their oldest event ages, and oldest event age; it does not print recipient or payload.

```sh
python -m app.services.email_outbox_cli status
python -m app.services.email_outbox_cli failed --limit 20
python -m app.services.email_outbox_cli failed --id 123
python -m app.services.email_outbox_cli retry-failed --id 123 --reason "SMTP credentials rotated; verified recipient"
```

The `failed` command reveals recipient, payload, and the stored delivery error, so use it only in a restricted terminal and avoid copying its output into public logs/tickets. `retry-failed` only accepts an event currently in `failed`; the status reset and audit record commit together. The CLI does not accept retry requests over HTTP. Keep the audit table under normal database access controls and periodically review retry actor/reason entries.

Alert on nonzero failed count, pending count exceeding the normal delivery rate, nonzero `expired_sending` or increasing `oldest_expired_lease_age_seconds`, oldest pending age exceeding the scheduler interval plus expected SMTP delay, and oldest failed age increasing. `oldest_sending_age_seconds` is event age; use expired lease metrics to detect stuck claims. Also alert on a missing/failed scheduled workflow run. Do not use recipient, payload, or SMTP error text as metric labels.

## Delivery semantics

Workers claim an event in a short transaction, commit the lease, send SMTP without a database transaction or row lock, then persist success/failure in a second short transaction. Every claim counts as an attempt, including one recovered after a crash; when an expired lease has exhausted the maximum, it becomes `failed` with delivery outcome marked unknown. A worker that dies leaves a `sending` event reclaimable after `EMAIL_OUTBOX_LEASE_SECONDS`; a later worker uses a new lease token, so an older worker cannot overwrite the newer claim. SMTP send timeout is capped at half the configured lease (up to 120 seconds) to reduce lease overlap.

SMTP cannot provide exactly-once delivery. If the SMTP server accepted a message and the worker crashes before `sent` commits, the lease expires and the message may be sent again. The deterministic `Message-ID` can help downstream systems recognize duplicates, but it does not guarantee deduplication. Delivery is therefore at-least-once in this crash window.
