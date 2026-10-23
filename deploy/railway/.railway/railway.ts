// Benchwarmers on Railway, as Railway's Infrastructure as Code. HOSTING.md has
// the steps; in short, with the Railway CLI (5.42.1 or later) and Node 22+:
//
//   cd deploy/railway
//   npm install railway            // this file's types; nothing else uses them
//   railway init                   // a new, empty project
//   railway config apply           // the service and its volume, from this file
//
// then, on the service: set BENCHWARMERS_ADMIN_TOKEN (16+ characters) and one
// BENCHWARMERS_KEY_<NAME> per endpoint key under Variables, and generate a
// domain under Networking (a generated domain cannot be declared here).
//
// Not railway.json or railway.toml: those cannot declare a volume, new
// services cannot opt in to them, and Railway stops reading them on
// 2026-12-01. Railway reads this file only when `railway config apply` runs,
// never during a deploy.
//
// Railway passes PORT and the domain (RAILWAY_PUBLIC_DOMAIN);
// deploy/container.py turns them into BENCH_PORT and BENCHWARMERS_PUBLIC_URL.
// With a custom domain, set BENCHWARMERS_PUBLIC_URL as well.

import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

// Your fork or copy of the repository, which Railway's GitHub app can read.
const REPO = "WendellResearch-AI/benchwarmers";

export default defineRailway(() => {
  // The data folder: endpoints, collections, verdicts, probe history. It is
  // the product, so it lives on a volume (turn on its backups on the volume's
  // Backups tab).
  const data = volume("benchwarmers-data", { sizeMB: 1024 });

  const app = service("benchwarmers", {
    source: github(REPO, { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    deploy: {
      healthcheckPath: "/health",
      healthcheckTimeout: 120,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      // One replica: the volume belongs to one, and the scheduler must run once.
      numReplicas: 1,
      // Never sleep. The scheduler (daily probes, weekly speed runs, retiring
      // models that stopped answering) runs inside the server, and a sleeping
      // service runs none of it.
      sleepApplication: false,
    },
    env: {
      // Set on the service's Variables page; preserve() keeps that value.
      BENCHWARMERS_ADMIN_TOKEN: preserve(),
    },
    volumeMounts: { "/data": data },
  });

  return project("benchwarmers", { resources: [app, data] });
});
