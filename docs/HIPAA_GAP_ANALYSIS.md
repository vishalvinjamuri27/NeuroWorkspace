# HIPAA gap analysis

## Current statement

NeuroWorkspace v0.1 is not HIPAA compliant, is not offered as a HIPAA-ready service,
and must not be used for patient care or identifiable PHI. The hosted alpha is for
public, synthetic, or appropriately deidentified research data. This document is a
technical planning aid, not legal advice or a compliance determination.

## Why code is insufficient

HIPAA obligations depend on roles, data flows, contracts, deployment, people,
policies, and ongoing operations. Encryption and access control alone do not create
compliance. A covered entity/business associate analysis and qualified legal/
compliance review are prerequisites.

## Gaps before handling PHI

- Executed BAAs with customers and every qualifying infrastructure/subprocessor;
  inventory and review of data locations and onward transfers.
- Formal risk analysis/risk-management program, policies, assigned security/privacy
  responsibility, workforce training, sanctions, and periodic evaluation.
- Production identity with MFA as appropriate, unique users, least privilege,
  joiner/mover/leaver process, emergency access, session policy, and access review.
- Complete tamper-evident audit coverage, protected retention, review procedures,
  and customer-accessible audit evidence.
- Validated encryption/key management, rotation/separation, network controls,
  hardened hosts/images, vulnerability/patch management, and penetration testing.
- Backup, disaster recovery, contingency operation, restore objectives/drills,
  integrity controls, and documented availability ownership.
- Configurable retention, legal hold where applicable, verified deletion including
  backups, media disposal, and data-return/termination procedures.
- Incident response and breach assessment/notification processes with contacts,
  evidence preservation, exercises, and regulatory/customer timelines.
- Data classification/minimum-necessary design, approved support workflows, no PHI
  in observability/analytics, secure support access, and customer configuration.
- Secure development/change control, production access approvals, vendor reviews,
  physical safeguards, policy evidence, and independent assessment as appropriate.
- Product/legal validation of deidentification workflows; do not assume removing
  obvious header fields makes a recording deidentified.

## Technical work beyond alpha

Database row-level security defense-in-depth, enterprise OIDC/SSO and provisioning,
fine-grained roles, immutable audit export, customer-managed/regional storage,
retention controls, controlled operator access with just-in-time approval, security
monitoring, DLP-aware support tooling, verified backup deletion, and deployment
reference architectures. Each must be tested in the actual hosted environment.

## Exit criteria for any future claim

Named legal/compliance owner, documented system boundary/data flows, signed BAAs,
completed risk analysis and remediation, implemented policies/training, production
control evidence, incident/restore drills, vendor review, independent assessment,
and precise marketing/contract language. Until then, every surface retains the
non-clinical/non-PHI notice.

