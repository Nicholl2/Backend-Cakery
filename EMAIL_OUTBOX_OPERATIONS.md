# Email outbox operations

## Deployment model

The lab server runs the API in Docker and has access to the production database. The email worker runs as a background task in the API process when `EMAIL_WORKER_ENABLED=true`. It is disabled by default, including in tests. Do not enable it in serverless deployments that do not provide a persistent process lifecycle. The worker uses the API container's existing database and SMTP environment; no public worker endpoint or scheduled batch workflow is used.

## Configuration

Configure SMTP/application values in the API container's restricted environment file. `EMAIL_OUTBOX_ADMIN_TOKEN` belongs only in the restricted operator environment used for the CLI; it is not needed by API requests or the background worker. Never put credentials in this file or source control.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Same production database used by the API |
| `ENVIRONMENT=production` | Enables production settings validation |
| `SECRET_KEY`, `SERVICE_API_KEY` | Required non-default application secrets because app settings are loaded at process start |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server and port; workflow defaults port to 587 |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP authentication |
| `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | Sender identity; production requires sender email |
| `SMTP_USE_TLS` | Whether SMTP STARTTLS is enabled; defaults to true |
| `SMTP_OPERATIONAL_INBOX` | Optional operational seller inbox where configured by the existing event producer |
| `EMAIL_WORKER_ENABLED` | Set to `true` on the lab Docker API deployment to start the background worker; defaults to `false` |
| `EMAIL_OUTBOX_MAX_ATTEMPTS` | Attempts before an event becomes `failed`; defaults to 10 |
| `EMAIL_OUTBOX_POLL_SECONDS` | Delay between checks when no event is ready; defaults to 5 seconds |
| `EMAIL_OUTBOX_LEASE_SECONDS` | Claim lease lifetime; defaults to 300 seconds and must exceed expected SMTP send duration |
| `EMAIL_OUTBOX_ADMIN_TOKEN` | Required by the local operations CLI; store in the operator secret manager, not in API routes |

For a new, empty database, bootstrap it first using the documented command in `README.md`. For an existing database with a recorded Alembic revision, upgrade once before enabling scheduled delivery:

```sh
alembic upgrade head
```

The complete revision chain creates the outbox and audit tables, adds worker lease fields, and adds nullable `payments.pg_order_id` with a unique index. Existing payment rows remain `NULL`; payment callbacks and refreshes for those rows now fail closed until their exact order reference is reconciled from a trusted Midtrans record and backfilled. Do not synthesize a reference from the invoice number or expected format. For a database without an Alembic revision, follow the existing-database adoption procedure in `README.md`; do not run the chain from its historical root. Downgrade of the payment migration drops the reference column and permanently loses values written after upgrade; take a database backup and stop payment writes before any downgrade.

## Start and restart

On the lab Docker deployment, add `EMAIL_WORKER_ENABLED=true` to the API container's environment file and recreate/redeploy that container. The existing image starts Uvicorn; the worker is started and stopped with the API application's lifecycle. Keep the flag unset or `false` in tests and other deployments. After deployment, monitor container health and outbox backlog through the restricted operator CLI. Do not publish the operations CLI or outbox details as an HTTP endpoint.

## Backlog monitoring and controlled recovery

Run these commands from a trusted operator shell with production database access and `EMAIL_OUTBOX_ADMIN_TOKEN` configured for the CLI. Each command prompts for the token without echoing it. Restrict shell/database access to authorized operators. Aggregate status includes `pending`, `sending`, `failed`, their oldest event ages, and oldest event age; it does not print recipient or payload.

```sh
python -m app.services.email_outbox_cli status
python -m app.services.email_outbox_cli failed --limit 20
python -m app.services.email_outbox_cli failed --id 123
python -m app.services.email_outbox_cli retry-failed --id 123 --reason "SMTP credentials rotated; verified recipient"
```

The `failed` command reveals recipient, payload, and the stored delivery error, so use it only in a restricted terminal and avoid copying its output into public logs/tickets. `retry-failed` only accepts an event currently in `failed`; the status reset and audit record commit together. The CLI does not accept retry requests over HTTP. Keep the audit table under normal database access controls and periodically review retry actor/reason entries.

Alert on nonzero failed count, pending count exceeding the normal delivery rate, nonzero `expired_sending` or increasing `oldest_expired_lease_age_seconds`, oldest pending age exceeding expected SMTP delay, and oldest failed age increasing. `oldest_sending_age_seconds` is event age; use expired lease metrics to detect stuck claims. Also monitor the API container and confirm the worker flag is enabled in its deployed environment. Do not use recipient, payload, or SMTP error text as metric labels.

## Delivery semantics

Workers claim an event in a short transaction, commit the lease, send SMTP without a database transaction or row lock, then persist success/failure in a second short transaction. Every claim counts as an attempt, including one recovered after a crash; when an expired lease has exhausted the maximum, it becomes `failed` with delivery outcome marked unknown. A worker that dies leaves a `sending` event reclaimable after `EMAIL_OUTBOX_LEASE_SECONDS`; a later worker uses a new lease token, so an older worker cannot overwrite the newer claim. SMTP send timeout is capped at half the configured lease (up to 120 seconds) to reduce lease overlap.

SMTP cannot provide exactly-once delivery. If the SMTP server accepted a message and the worker crashes before `sent` commits, the lease expires and the message may be sent again. The deterministic `Message-ID` can help downstream systems recognize duplicates, but it does not guarantee deduplication. Delivery is therefore at-least-once in this crash window.
