# Hosting

Benchwarmers runs well on your own machine. This page is for running it
somewhere else, always on, with a public HTTPS address: on Fly.io, Railway,
Render, or a VPS of your own. All four run the same Docker image.

These templates are checked by building and running the image and by
validating each file against its platform's published schema, not by
deploying them. If a step here no longer matches a platform's pages, the
platform's pages win; please open an issue.

## What it needs

- **Always on.** The scheduler (each model's daily probe, the weekly speed
  runs, retiring models that stopped answering) runs inside the server. A
  service that sleeps or stops when idle runs none of it, and the record gets
  gaps wherever nobody was looking. So: no free tiers that sleep, no
  serverless.
- **A persistent disk at `/data`.** Endpoints, collections, verdicts and probe
  history live there. The accumulated record is the product, so the disk must
  outlive deploys and restarts. A platform whose disk is wiped on restart
  (Hugging Face Spaces, for one) will not do.
- **One instance.** A disk belongs to one machine, and the scheduler must run
  once.
- **About 512 MB of memory.** An idle fresh install uses about 35 MB; the
  probes and the suite run more Python beside it.

## Compared

Prices checked on 2026-10-23, in US dollars unless marked, before tax, for the
smallest setup that meets the needs above: one small instance and a 1 GB disk.

| | Fly.io | Railway | Render | VPS (Caddy) |
| --- | --- | --- | --- | --- |
| **Cost a month** | about $3.84: shared-cpu-1x 512 MB $3.69 in iad/ewr, plus $0.15 for the volume | $5, the Hobby plan, which includes $5 of usage; this app should fit inside it | $7.25: the 0.5c-512mb ("Starter") instance $7, plus $0.25 for the disk | $6 at DigitalOcean (1 GiB); about €6.49 at Hetzner |
| **Free option** | none; a trial of 2 machine-hours or 7 days | a trial ($5 once, 30 days) and a Free plan, both with volumes of 0.5 GB at most | Free instances have no disk and sleep | none |
| **Sleep / auto-stop** | off in `deploy/fly.toml` | off in the template; a per-service "Serverless" setting | paid instances do not sleep; Free ones sleep after 15 idle minutes | never |
| **Persistent disk** | a volume, $0.15/GB; daily snapshots kept 5 days | a volume, about $0.15/GB; backups on a schedule you choose | a disk, paid instances only, $0.25/GB; daily snapshots kept 7 days | the server's own disk; `deploy/vps/backup.sh` |
| **HTTPS** | automatic, `<app>.fly.dev` or your domain | automatic, `<name>.up.railway.app` or your domain | automatic, `<name>.onrender.com` or your domain | Caddy, automatic, for your domain |
| **Deploys** | short gap: one machine | short gap: one replica with a volume | short gap: a service with a disk is stopped before the new one starts | short gap |
| **Template** | [deploy/fly.toml](deploy/fly.toml) | [deploy/railway/.railway/railway.ts](deploy/railway/.railway/railway.ts) | [render.yaml](render.yaml) and a button | [deploy/vps/](deploy/vps/) |

Sources: [Fly.io pricing](https://docs.fly.io/about/pricing),
[Fly.io volumes](https://docs.fly.io/volumes/overview),
[Railway pricing](https://railway.com/pricing),
[Railway plans](https://docs.railway.com/pricing/plans),
[Railway serverless](https://docs.railway.com/guides/cut-idle-costs-serverless),
[Railway volume backups](https://docs.railway.com/volumes/backups),
[Render pricing](https://render.com/pricing),
[Render disks](https://render.com/docs/disks),
[Render free instances](https://render.com/docs/free),
[DigitalOcean droplets](https://www.digitalocean.com/pricing/droplets),
[Hetzner Cloud](https://www.hetzner.com/cloud/regular-performance/). Prices
move: Render renamed its plans in August 2026, and Hetzner raised its prices
twice in 2026 (its cheapest CX23 and CAX11 plans showed as unavailable on the
day these were checked).

**Which suits whom:**

- **Fly.io**: the cheapest managed option, and the most control from one
  file. You need its CLI and a card.
- **Railway**: the least to configure in the browser, and the most
  predictable bill if you already pay for Hobby. Its volume and service are
  made by its CLI from the template, or by hand in a few clicks.
- **Render**: a one-click button, and the most familiar dashboard. The dearest
  of the four for this size.
- **A VPS**: the cheapest per gigabyte, and the data on a disk you control.
  You keep the server patched and the backups running.

## Before any of them

**The admin token.** Every hosted install needs `BENCHWARMERS_ADMIN_TOKEN`, 16
or more characters; without it the server refuses to start. Make one with:

    python -c "import secrets; print(secrets.token_urlsafe(24))"

Keep it in the platform's secrets or variables, never in a template or a
commit. With it, the dashboard asks for a login, and the gateway's routes
(`/v1`, `/bench`) take gateway keys you make on Settings.

**Provider keys.** Give each endpoint's key as a secret named
`BENCHWARMERS_KEY_<NAME>` (the key name upper-cased, `-` as `_`), or paste it
on the dashboard. A pasted key is kept on the disk (`/data/llm/keys.json`), so
it is in the disk's snapshots and backups too; a secret is not. Signing in
with a provider's own page needs a callback to the machine the browser is on,
so on a hosted server the dashboard signs in by code where the provider
offers that (GitHub, for one), and for the others you paste a key.

**What the image does for each platform.** `deploy/container.py` is the
image's entry point. It takes the platform's `PORT` as `BENCH_PORT`, and its
public address (`FLY_APP_NAME`, `RAILWAY_PUBLIC_DOMAIN`, `RENDER_EXTERNAL_URL`)
as `BENCHWARMERS_PUBLIC_URL` when that is not set, so the platform's host name
is accepted and the login cookie is `Secure`. With a custom domain, set
`BENCHWARMERS_PUBLIC_URL` to it yourself. Platform volumes are mounted owned
by root, so it hands `/data` to the app's user and drops to that user before
the server starts.

**`/health`** answers `{"ok": true}` to anyone, and nothing else. Every
other route needs the login or a gateway key.

## Fly.io

From a checkout, with [flyctl](https://docs.fly.io/flyctl/install/):

    cp deploy/fly.toml fly.toml
    fly launch --copy-config --no-deploy
    fly secrets set BENCHWARMERS_ADMIN_TOKEN=<token>
    fly secrets set BENCHWARMERS_KEY_OPENROUTER=sk-or-...
    fly deploy

`fly launch` asks for an app name and writes it into `fly.toml`; decline the
databases it offers. The first `fly deploy` makes the 1 GB volume named in
`[mounts]`. Open `https://<app>.fly.dev` and sign in with the token.

The file sets `auto_stop_machines = "off"` and `min_machines_running = 1`, so
the machine never stops and the scheduler keeps its hours. Keep it that way
unless the install is never scheduled. Keep one machine (`fly scale count 1`):
a second would have its own empty volume.

Fly snapshots the volume daily and keeps each for 5 days
(`fly volumes list`, then `fly volumes snapshots list <volume id>`).

## Railway

Railway's older `railway.json` and `railway.toml` cannot declare a volume, new
services cannot use them, and Railway stops reading them on 2026-12-01. The
template is Railway's Infrastructure as Code instead, which can. Railway
reads it only when you apply it with the CLI, never during a deploy.

With the [Railway CLI](https://docs.railway.com/cli) (5.42.1 or later) and
Node 22 or later, in a fork of this repository:

1. Put your fork's name in `REPO` in
   [deploy/railway/.railway/railway.ts](deploy/railway/.railway/railway.ts).
2. Make the project and its service and volume:

       cd deploy/railway
       npm install railway
       railway init
       railway config plan
       railway config apply

3. On the service, under Variables: `BENCHWARMERS_ADMIN_TOKEN`, and a
   `BENCHWARMERS_KEY_<NAME>` per endpoint key.
4. Under Networking: generate a domain (a generated domain cannot be declared
   in the file). Open it and sign in with the token.
5. On the volume, under Backups: turn on a daily schedule.

**Without the CLI**, the same in the browser: a new project from your GitHub
fork (Railway finds the `Dockerfile`); on the service, attach a volume at
`/data`, set the variables above, set the health check path to `/health`, and
generate a domain. Leave Serverless off.

There is no Deploy on Railway button here: a button needs a template made in
a Railway account, and this repository does not publish one.

## Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/WendellResearch-AI/benchwarmers)

The button makes a Blueprint from [render.yaml](render.yaml): one web service
on the 0.5c-512mb instance with a 1 GB disk at `/data`, a health check on
`/health`, and an admin token Render generates. It is at the top of the
repository, not under `deploy/`, because the button reads it from there. To
deploy your fork instead, use New, Blueprint in the Render dashboard.

After it starts: read `BENCHWARMERS_ADMIN_TOKEN` on the service's Environment
page and sign in at `https://<name>.onrender.com`. Add provider keys on the
same page.

A disk needs a paid instance type: on Free, the filesystem is wiped on each
deploy and restart, and the service sleeps after 15 idle minutes. Render
snapshots the disk daily and keeps each for 7 days. The Blueprint sets
`autoDeployTrigger: off`, so a new version deploys when you choose (Manual
Deploy), not on every commit to this repository.

## A VPS

Any Linux server with Docker and its compose plugin, and a domain whose DNS
points at it. [deploy/vps/](deploy/vps/) holds a compose file with Caddy in
front, which gets and renews the certificate itself.

    git clone https://github.com/WendellResearch-AI/benchwarmers
    cd benchwarmers/deploy/vps
    cp env.example .env
    chmod 600 .env                  # then fill in BENCH_DOMAIN, the token and keys
    docker compose up -d --build

Open `https://<your domain>` and sign in with the token. To update later:
`git pull`, then `docker compose up -d --build`.

**Firewall.** Allow SSH, 80 and 443 (443/udp too, for HTTP/3), and nothing
else. With ufw:

    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443
    ufw enable

Docker's published ports go around ufw's rules
([Docker's page on it](https://docs.docker.com/engine/network/packet-filtering-firewalls/)),
so a firewall alone does not keep a published port private. That is why only
Caddy publishes ports here: Benchwarmers has none, and Caddy reaches it over
the compose network. Keep it that way if you edit the file.

**Backups.** [deploy/vps/backup.sh](deploy/vps/backup.sh) stops Benchwarmers
for the seconds it takes, writes `backups/benchwarmers-<time>.tar.gz`, starts
it again, and keeps the 14 newest. Daily at 04:15, from the server's crontab:

    15 4 * * * /path/to/benchwarmers/deploy/vps/backup.sh >/dev/null

Copy the archives off the server too; a backup on the same disk goes when the
disk does. They can hold pasted keys, so the script makes them readable by
their owner only. To restore one:

    docker compose stop benchwarmers
    docker compose run --rm --no-deps -T -v "$PWD/backups:/backups:ro" --entrypoint sh \
      benchwarmers -c 'find /data -mindepth 1 -delete && tar xzf /backups/<archive> -C /data'
    docker compose start benchwarmers

Keep the `caddy-data` volume as well: it holds the certificates, and getting
them again too often hits Let's Encrypt's rate limits.
