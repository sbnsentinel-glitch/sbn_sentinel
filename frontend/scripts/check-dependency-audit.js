#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-require-imports */

/**
 * F-33 / AT-33: Automated release policy gate for dependency vulnerabilities.
 * Fails CI if any material high/critical vulnerability is detected
 * that lacks a documented, approved disposition in dependency-disposition.json.
 */

const fs = require('fs');
const path = require('path');

const auditFile = path.resolve(__dirname, '../dependency-audit.json');
const dispositionFile = path.resolve(__dirname, '../dependency-disposition.json');

if (!fs.existsSync(auditFile)) {
  console.error('[F-33 GATE ERROR] dependency-audit.json artifact not found.');
  process.exit(1);
}

if (!fs.existsSync(dispositionFile)) {
  console.error('[F-33 GATE ERROR] dependency-disposition.json policy file not found.');
  process.exit(1);
}

const auditRaw = fs.readFileSync(auditFile, 'utf8').replace(/^\uFEFF/, '');
const auditData = JSON.parse(auditRaw);
const dispositionRaw = fs.readFileSync(dispositionFile, 'utf8').replace(/^\uFEFF/, '');
const dispositionData = JSON.parse(dispositionRaw);

const approvedAdvisories = new Set(
  dispositionData.dispositions.map(d => d.advisory.trim())
);

const unaccepted = [];
const accepted = [];

const vulns = auditData.vulnerabilities || {};

for (const [pkgName, pkgInfo] of Object.entries(vulns)) {
  const viaList = pkgInfo.via || [];
  for (const item of viaList) {
    if (typeof item === 'object' && item.url) {
      const advisoryId = item.url.split('/').pop().trim();
      const severity = item.severity || pkgInfo.severity;

      if (severity === 'high' || severity === 'critical') {
        if (approvedAdvisories.has(advisoryId)) {
          accepted.push({
            package: pkgName,
            advisory: advisoryId,
            severity,
            title: item.title,
          });
        } else {
          unaccepted.push({
            package: pkgName,
            advisory: advisoryId,
            severity,
            title: item.title,
          });
        }
      }
    }
  }
}

console.log('=====================================================');
console.log('  SES-011 F-33 Dependency Vulnerability Quality Gate ');
console.log('=====================================================');
console.log(`Approved High/Critical Exceptions Traceable: ${accepted.length}`);

if (unaccepted.length > 0) {
  console.error(`\n[RELEASE BLOCKED] ${unaccepted.length} unaccepted high/critical vulnerabilities found:`);
  unaccepted.forEach(u => {
    console.error(` - [${u.severity.toUpperCase()}] ${u.package}: ${u.advisory} - ${u.title}`);
  });
  console.error('\nEvery high/critical vulnerability must have a documented disposition in dependency-disposition.json.');
  process.exit(1);
} else {
  console.log('[RELEASE ALLOWED] All high/critical vulnerabilities match documented, approved dispositions.');
  process.exit(0);
}
