#!/bin/sh
# A backup of the data folder: backups/benchwarmers-<UTC time>.tar.gz beside
# this file, the 14 newest kept. By hand, or daily from cron (HOSTING.md):
#
#   ./backup.sh
#
# Benchwarmers is stopped for the seconds the copy takes, so no file is caught
# half-written, and started again however the copy ends. The archive holds
# llm/keys.json when keys were pasted on the dashboard, so it is made readable
# by its owner only. Copy it off this server too: a backup on the same disk
# goes when the disk does.
set -eu
cd "$(dirname "$0")"
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)

docker compose stop benchwarmers
trap 'docker compose start benchwarmers' EXIT
docker compose run --rm --no-deps -T -v "$PWD/backups:/backups" --entrypoint tar \
  benchwarmers czf "/backups/benchwarmers-$stamp.tar.gz" -C /data .

ls -1t backups/benchwarmers-*.tar.gz | tail -n +15 | xargs -r rm --
echo "backups/benchwarmers-$stamp.tar.gz"
