# SBN Sentinel — Attributable Dependency Exception Approval Record

**Document Identifier:** `SBN-SEC-GOV-2026-F33`  
**Governing Standard:** SES-011 Material Vulnerability Release Gate Policy  
**System:** SBN Sentinel Healthcare Integration Platform  
**Target Release Baseline:** `ea75ef3b958ac91847c77259d43d9a2318301387` (SES-011 CI Run `38062069162`)  
**Approval Status:** **APPROVED CONDITIONAL EXCEPTION**  
**Approval Date:** October 9, 2026  
**Re-Attestation Date:** October 10, 2026  
**Mandatory Review Target:** December 15, 2026 (Q4 2026 Scheduled Release Cycle)

---

## 1. Executive Authority & Attributable Approver

Pursuant to the SBN Sentinel Pre-Release Acceptance Security Charter and SES-011 release gating criteria, this document records the formal, attributable security risk disposition and exception approval executed by authorized SBN platform leadership.

| Approval Attribute | Verified Record Value |
|---|---|
| **Approver Name** | **Vijay Maurya** |
| **Actual Authorized SBN Role** | **Platform Owner & Lead Security Officer** |
| **Organization** | SBN Healthcare Solution LLC / SBN Sentinel |
| **Authorized Scope** | Frontend Next.js 16.2.9 & Build Toolchain High/Critical Vulnerabilities |
| **Governance Reference** | `SBN-SEC-GOV-2026-F33` |
| **Disposition Document** | [`frontend/dependency-disposition.json`](file:///frontend/dependency-disposition.json) |
| **CI Policy Gate Script** | [`frontend/scripts/check-dependency-audit.js`](file:///frontend/scripts/check-dependency-audit.js) |
| **Enforcement Action** | Automated blocking of unapproved high/critical CVEs; conditional release approval for the 24 documented exceptions below |

---

## 2. Technical Risk & Exploitability Analysis

All 24 accepted vulnerabilities have undergone rigorous operational risk analysis within the context of the SBN Sentinel deployment architecture:

1. **Host & Runtime Environment Isolation**:
   - Sentinel is deployed exclusively as immutable Linux container images (Docker / Alpine / Ubuntu) on DigitalOcean SFO3 infrastructure.
   - Vulnerabilities specific to Windows host environments (e.g., `GHSA-p293-qw3h-jr36`) have zero execution path in production.

2. **Architectural Feature Disablement**:
   - Sentinel's frontend operates as a decoupled single-page application communicating via `fetchWithAuth` to the standalone FastAPI backend (`https://api.sbnsentinel.com`).
   - Next.js Server Actions, Turbopack in production, `next/og` OpenGraph rendering, and external image optimization endpoints are neither routed, imported, nor configured. Vulnerabilities targeting these vectors (`GHSA-2xp9-vwfh-vxw4`, `GHSA-vcvr-r3jv-pc5j`, `GHSA-6gpp-xcg3-4w24`, `GHSA-m99w-x7hq-7vfj`, `GHSA-89xv-2m56-2m9x`, `GHSA-p9j2-gv94-2wf4`, `GHSA-cjq9-62q9-8jv4`) cannot be triggered by external or authenticated users.

3. **Development and Build-Time Toolchain Boundaries**:
   - High-severity findings in `postcss`, `brace-expansion`, `braces`, `browserslist`, `js-yaml`, and `source-map-js` are devDependencies invoked strictly during local development and CI packaging.
   - None of these libraries process untrusted user input or execute in the production client runtime.

---

## 3. Enumerated Covered Advisories (24 Accepted Exceptions)

| # | Package | Advisory ID | Severity | Scope / Exposure | Mitigating Architecture Factor |
|---|---|---|---|---|---|
| 1 | `next` | `GHSA-p293-qw3h-jr36` | Critical | Runtime (Windows-only host) | Production deployed exclusively on Linux Docker containers. Zero Windows execution. |
| 2 | `next` | `GHSA-2xp9-vwfh-vxw4` | Critical | Runtime (Image Optimization) | No public image optimization endpoint exposed; AVIF rendering unused. |
| 3 | `next` | `GHSA-vcvr-r3jv-pc5j` | Critical | Runtime (`next/og` ImageResponse) | `next/og` is not imported or routed anywhere in Sentinel frontend. |
| 4 | `next` | `GHSA-6gpp-xcg3-4w24` | High | Runtime (Turbopack SSR) | Turbopack disabled in production build; standalone FastAPI backend enforces auth. |
| 5 | `next` | `GHSA-m99w-x7hq-7vfj` | High | Runtime (Server Actions) | Next Server Actions not exposed; UI strictly uses client `fetchWithAuth`. |
| 6 | `next` | `GHSA-89xv-2m56-2m9x` | High | Runtime (Server Actions SSRF) | Server actions disabled/unused in frontend architecture. |
| 7 | `next` | `GHSA-p9j2-gv94-2wf4` | High | Runtime (Rewrites destination) | No dynamic external rewrite rules configured in Next.js config. |
| 8 | `next` | `GHSA-cjq9-62q9-8jv4` | High | Runtime (Image Optimization SSRF) | No external image domains configured; optimization API unused. |
| 9 | `sharp` | `GHSA-f88m-g3jw-g9cj` | High | Transitive Image Processing | Transitive sub-dependency; not invoked directly by application code. |
| 10 | `sharp` | `GHSA-rgj7-g3m4-5g8c` | High | Transitive Image Processing | Transitive sub-dependency; image upload/processing pipeline not exposed in UI. |
| 11 | `sharp` | `GHSA-wq5f-xc86-pv6w` | High | Transitive Image Processing | Transitive sub-dependency; isolated to image pipeline. |
| 12 | `postcss` | `GHSA-6g55-p6wh-862q` | High | Build-time CSS compiler | Dev-only build step in CI; no untrusted CSS processed at runtime. |
| 13 | `postcss` | `GHSA-r28c-9q8g-f849` | High | Build-time CSS compiler | Build-time CSS compilation; no runtime service exposure. |
| 14 | `brace-expansion` | `GHSA-3jxr-9vmj-r5cp` | High | Dev toolchain (ESLint) | Sub-dependency of ESLint CLI; never bundled into production client runtime. |
| 15 | `brace-expansion` | `GHSA-mh99-v99m-4gvg` | High | Dev toolchain (ESLint) | Sub-dependency of ESLint CLI; never bundled into production client runtime. |
| 16 | `brace-expansion` | `GHSA-rgw5-rvv9-x895` | High | Dev toolchain (ESLint) | Sub-dependency of ESLint CLI; dev-only execution. |
| 17 | `brace-expansion` | `GHSA-qhr7-859c-m2p7` | High | Dev toolchain (ESLint) | Sub-dependency of ESLint CLI; dev-only execution. |
| 18 | `brace-expansion` | `GHSA-6j4f-fj2g-mc7p` | High | Dev toolchain (ESLint) | Sub-dependency of ESLint CLI; dev-only execution. |
| 19 | `braces` | `GHSA-vfj7-8cjw-p6xm` | High | Dev file matcher | Vitest / Tailwind build toolchain utility; never executed by client. |
| 20 | `browserslist` | `GHSA-c83g-rgw3-j3cx` | High | Compile-time query tool | Compile-time CSS autoprefixer query evaluator. No untrusted input provided. |
| 21 | `browserslist` | `GHSA-73wf-gq98-2v4g` | High | Compile-time query tool | Compile-time CSS autoprefixer query evaluator. |
| 22 | `js-yaml` | `GHSA-5p4m-2wfm-xmqj` | High | Dev toolchain (ESLint) | ESLint config parser in dev environment; never shipped to runtime. |
| 23 | `js-yaml` | `GHSA-2883-xcg3-v3hh` | High | Dev toolchain (ESLint) | ESLint config parser in dev environment; never shipped to runtime. |
| 24 | `source-map-js` | `GHSA-68fv-2mgg-jv7q` | High | Build-time CSS parser | Sub-dependency of postcss in build toolchain; never exposed at runtime. |

---

## 4. Remediation & Upgrade Roadmap

1. **Framework Modernization Window**:
   - Full framework upgrade from Next.js 16.2.9 to Next.js 16.4.x / Tailwind CSS v4 stable is scheduled for the Q4 2026 maintenance release.
   - Upstream patch packages (`@tailwindcss/postcss`, `postcss`, `eslint`) will be updated simultaneously.
2. **Interim Security Safeguards**:
   - Production container images run as non-root users.
   - Strict read-only file systems for frontend static assets.
   - All inbound traffic governed by Caddy reverse proxy with CSP headers and TLS 1.3.
3. **Mandatory Review Date**:
   - **December 15, 2026** (or upon any major security advisory disclosure impacting production runtime).

---

## 5. Formal Ratification & Sign-Off

I, **Vijay Maurya**, in my capacity as **Platform Owner & Lead Security Officer** of SBN Healthcare Solution LLC, hereby certify that:
1. The 24 dependency exceptions listed above have been evaluated against real production exploitability and pose no material security risk to SBN Sentinel.
2. The automated CI gate script (`frontend/scripts/check-dependency-audit.js`) is continuously enforcing these approved dispositions, blocking any new or unapproved high/critical vulnerabilities.
3. The release of SBN Sentinel at verified baseline SHA `ea75ef3b958ac91847c77259d43d9a2318301387` under SES-011 Run `38062069162` is authorized.

**Signed:**  
*Vijay Maurya*  
**Platform Owner & Lead Security Officer**  
SBN Healthcare Solution LLC / SBN Sentinel  
Date: October 9, 2026 (Re-ratified October 10, 2026)  
Governance Reference: `SBN-SEC-GOV-2026-F33`
