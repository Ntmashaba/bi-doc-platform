# Deploying the library to Azure (B13)

This runbook takes a client from an empty resource group to a signed-in library. It uses
`deploy/azure/main.bicep`, which creates:

| Resource | Pilot setting |
|---|---|
| Container Apps environment and app (Consumption) | 0–1 replicas, 0.25 vCPU / 0.5 GiB, https only |
| User-assigned managed identity | Storage Table and Blob Data Contributor on the one account; AcrPull when a registry is named |
| Storage account (or an existing one) | Keys disabled (managed identity only), TLS 1.2, no public blobs, 14-day blob and container soft delete |
| Log Analytics workspace | 30-day retention by default |
| Built-in authentication (Easy Auth) with Microsoft Entra ID | Sign-in for everything except health probes and the token-only publishing and worker APIs |
| Container Apps job | `python -m bidoc_library cleanup` daily: expires job leases, deletes raw job sources after their retention (R3) |
| Monthly budget | Email at 80 % actual and 100 % forecast. Budgets notify; they do not cap spending |

It creates no AKS cluster, database server, paid search tier or virtual machine.

## 1. Before you start

- An Azure subscription and a resource group. The deployer needs Owner, or Contributor
  plus User Access Administrator, because the template assigns roles.
- A container registry (Azure Container Registry is assumed) and a machine with Docker.
- Permission in Microsoft Entra ID to create an app registration and assign users to it.
- The Azure CLI (`az`), signed in to the right tenant.

## 2. Register the application in Entra ID

1. **App registrations → New registration.** Name it "BI Documentation Library" and choose
   single tenant. Leave the redirect URI empty for now; step 5 adds it.
2. **App roles:** create three roles for users/groups, with these values:
   - `BiDoc.Viewer`: read documentation;
   - `BiDoc.Publisher`: import and publish, manage metadata and manual links, issue their
     own publishing tokens;
   - `BiDoc.Admin`: everything, including installer releases and revoking anyone's tokens.

   To use other names, set `entraRoleMap`, for example `Readers=viewer,Editors=publisher`.
3. **Enterprise applications → the app → Properties:** set *Assignment required* to Yes.
   Only assigned users and groups can then sign in. Assign each person or group one of the
   roles.
4. **Certificates & secrets:** create a client secret. It is passed to the deployment on
   the command line and stored as a Container App secret, never in a file.
5. **Authentication:** enable *ID tokens*.

A signed-in user with no role gets 403. For every assigned user to be a viewer without
naming the role, deploy with `entraDefaultRoles=viewer`.

## 3. Build and push the image

```sh
docker build -t <registry>.azurecr.io/bidoc-library:<version> .
az acr login -n <registry>
docker push <registry>.azurecr.io/bidoc-library:<version>
```

The image runs as a non-root user and includes the Azure SDKs. The engines are built from
`components/` (see `components/README.md`).

## 4. Deploy

Copy `deploy/azure/example.parameters.json`, fill in the placeholders, then run:

```sh
az deployment group create -g <resource-group> -f deploy/azure/main.bicep \
  -p @my.parameters.json -p entraClientSecret="$SECRET"
```

The outputs are:

- `url`, the library address;
- `redirectUri`;
- `storageAccount`;
- `identityClientId`.

To keep documents in an existing account, pass `existingStorageAccountName`. It must be in
the same resource group, and the template adds the table, the container and the role
assignments.

## 5. Finish sign-in

In the app registration, add the `redirectUri` output as a **Web** redirect URI. Then open
`url`: you are sent to the Microsoft sign-in page and come back to the library.

## 6. Go-live verification

Record each result in the release checklist (`docs/release-checklist-r2.md`).

1. **Sign-in:**
   - an assigned viewer can browse but cannot import;
   - a publisher can import;
   - an unassigned account is refused by Entra;
   - a private browser window with no session is sent to sign-in.
2. **The platform principal is trusted, and only from the platform.** The library accepts
   the signed-in identity (`X-MS-CLIENT-PRINCIPAL`) only from `trustedProxies`, which
   defaults to loopback, where the authentication sidecar forwards from.
   - If every signed-in request returns *401 request did not come through the configured
     gateway*, the platform forwards from another address. Find it in the Log Analytics
     console logs, set `trustedProxies` to exactly that range, and redeploy.
   - Never set `0.0.0.0/0`.
3. **Forged identity is ignored.** While signed in as a viewer, send a request with your
   own `X-MS-CLIENT-PRINCIPAL` header claiming the admin role:

   ```sh
   curl -H "X-MS-CLIENT-PRINCIPAL: <forged>" --cookie "AppServiceAuthSession=<yours>" <url>/api/v1/capabilities
   ```

   `can_administer` must stay `false`.
4. **Publishing through the ingress (A36).** On a Windows machine with the generator:
   1. Issue a token in the library (**Publishing tokens**).
   2. Run `bidoc connect <url>`, then `bidoc publish <file>`.
   3. Publish the same file again: the result must be `duplicate`.
   4. Publish a newer file of the same document: the result must be a new version.
   5. Revoke the token: the next `bidoc publish` must fail with `CREDENTIAL_REJECTED`.

   CI proves the same sequence through a TLS nginx ingress (`check_ingress.py`). This step
   repeats it on the client's real ingress.
5. **Backup:** run a first backup and a restore into a scratch deployment
   (`docs/backup-restore.md`).

## Access management

- **Add a person:** assign them a role on the enterprise application.
- **Remove a publisher:** remove the role assignment, then revoke the tokens they issued.
  Publishing tokens are not tied to their sign-in, so this second step is required. An
  admin calls the API from a signed-in session, using the session cookie as in step 6.3.
  There is no screen for this yet.

  ```sh
  curl -X POST <url>/api/v1/publish-tokens/revoke-subject --cookie "AppServiceAuthSession=<admin's>" \
    -H "X-Requested-With: bidoc" -H "Content-Type: application/json" -d '{"subject": "<their object ID>"}'
  ```

  Subjects are Entra object IDs, never display names, so renaming someone does not change
  their history.
- **Tokens** last 1–30 days. Every issue, use and revocation is audited.

## Cold start and capacity

With `minReplicas=0`, the app stops after a few idle minutes. The first request after that
waits for a container to start: typically 10–30 seconds, including loading the engines
and reconciling the catalogue. Readiness waits for storage.

- Set `minReplicas=1` for instant responses, at the cost of paying for an always-on
  replica.
- The pilot runs one replica (`maxReplicas=1`). The Azure store is safe under concurrency,
  but derived rebuilds run in-process and are sized for one replica.
- Large-document costs are in `docs/performance.md`.

## Cost worksheet

Fill it in from the Azure pricing calculator for the client's region and agreement. Prices
change, so none are written here.

| Item | Driver | Pilot estimate |
|---|---|---|
| Container Apps (Consumption) | vCPU-seconds and GiB-seconds while a replica runs, plus requests; the monthly free grant applies first | hours active per month × 3600 × (0.25 vCPU, 0.5 GiB) |
| Always-on replica (only when `minReplicas=1`) | idle-rate vCPU and memory, 24×7 | 730 h × 3600 × (0.25, 0.5) |
| Table Storage | GB stored plus transactions | a few MB; transactions per action are in `docs/azure-storage.md` |
| Blob Storage (Hot, LRS) | GB stored plus operations | sum of artifact sizes, about 1–25 MB each, plus derived snapshots |
| Soft-deleted blobs | GB kept 14 days | only after deletions; blobs are normally never deleted |
| Log Analytics | GB ingested plus retention past the free period | console and system logs; low at pilot traffic |
| Container Registry | Basic tier per day plus storage | one image per release |
| Backups | storage for backup folders | about the size of the library, per retained backup |

## Upgrades

1. Take a backup.
2. Push the new image and redeploy with the new `containerImage`. Catalogue migrations
   run on start.
3. Run step 6.1 again.

To roll back, redeploy the previous image. If a migration changed the catalogue, restore
the backup into a new table and container instead.
