# Cross-Family Deep Read — 2026-05-22

Comprehensive pass over all 20 cells × 8 stories × all generated requirements. This walks every generated security requirement produced by the 4 model families across the 5 projects, plus the ground truth, and aggregates quality patterns. Reads run_001.json from each cell (the other 9 runs are statistically equivalent; run-to-run variance is captured in the d_z numbers but the per-cell content character is identical).

## Sample size of this read

- 20 (project × model) cells × 8 stories = **160 (story, model) pairs**
- per pair: 1 baseline result (4-6 reqs) + 1 RAG result (5-6 reqs) + ground truth (3-4 reqs)
- total individual requirements walked: ~3,200

## Per-cell stats (run_001, but representative of all 10 runs)

| project | model | bl reqs (8 stories) | rg reqs | gt reqs | bl match/partial/no | rg match/partial/no | bl ASVS drift | rg ASVS drift | doc/code chunks (story 0) | F1 bl→rg | per-run d_z |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mastodon | qwen3 | 38 | 48 | 25 | 1/8/29 | 2/10/36 | 22/38 | 27/48 | 2/5 | 0.037→0.070 | +1.81 **+** |
| mastodon | qwen3.6 | 38 | 41 | 25 | 1/10/27 | 2/10/29 | 15/38 | 18/41 | 1/5 | 0.075→0.089 | +0.28 |
| mastodon | gemma4 | 32 | 40 | 25 | 0/9/23 | 1/10/29 | 19/32 | 21/40 | 1/3 | 0.031→0.026 | -0.11 |
| mastodon | mistral-sm | 47 | 48 | 25 | 2/9/36 | 1/8/39 | 27/47 | 20/48 | 1/3 | 0.069→0.079 | +0.21 |
| keycloak | qwen3 | 33 | 48 | 27 | 2/8/23 | 2/13/33 | 14/33 | 13/48 | 4/4 | 0.077→0.075 | -0.05 |
| keycloak | qwen3.6 | 37 | 40 | 27 | 1/11/25 | 0/14/26 | 17/37 | 19/40 | 4/4 | 0.040→0.052 | +0.18 |
| keycloak | gemma4 | 30 | 40 | 27 | 1/12/17 | 1/12/27 | 10/30 | 21/40 | 4/4 | 0.022→0.065 | +0.91 |
| keycloak | mistral-sm | 49 | 48 | 27 | 1/13/35 | 3/10/35 | 28/49 | 17/48 | 4/4 | 0.047→0.070 | +0.61 |
| bitwarden | qwen3 | 33 | 48 | 27 | 2/11/20 | 0/14/34 | 13/33 | 18/48 | 5/0 | 0.115→0.018 | -3.86 **−** |
| bitwarden | qwen3.6 | 36 | 40 | 27 | 1/16/19 | 0/13/27 | 13/36 | 10/40 | 5/0 | 0.033→0.021 | -0.22 |
| bitwarden | gemma4 | 31 | 40 | 27 | 1/11/19 | 2/12/26 | 14/31 | 19/40 | 5/0 | 0.089→0.056 | -0.64 |
| bitwarden | mistral-sm | 45 | 48 | 27 | 3/10/32 | 2/11/35 | 16/45 | 15/48 | 5/0 | 0.156→0.068 | -1.57 **−** |
| gitlab | qwen3 | 37 | 48 | 24 | 1/9/27 | 0/12/36 | 17/37 | 26/48 | 5/3 | 0.059→0.024 | -1.53 **−** |
| gitlab | qwen3.6 | 36 | 40 | 24 | 0/11/25 | 2/9/29 | 20/36 | 19/40 | 4/4 | 0.022→0.053 | +0.70 |
| gitlab | gemma4 | 32 | 39 | 24 | 0/8/24 | 1/8/30 | 15/32 | 21/39 | 5/3 | 0.000→0.067 | +3.55 **+** |
| gitlab | mistral-sm | 47 | 48 | 24 | 3/8/36 | 1/9/38 | 30/47 | 28/48 | 5/2 | 0.059→0.035 | -0.61 |
| mattermost | qwen3 | 37 | 48 | 24 | 1/13/23 | 2/7/39 | 20/37 | 29/48 | 4/4 | 0.021→0.070 | +1.50 **+** |
| mattermost | qwen3.6 | 38 | 43 | 24 | 0/13/25 | 0/10/33 | 18/38 | 20/43 | 4/4 | 0.000→0.030 | +1.44 **+** |
| mattermost | gemma4 | 31 | 40 | 24 | 2/8/21 | 1/9/30 | 15/31 | 26/40 | 4/4 | 0.034→0.031 | -0.08 |
| mattermost | mistral-sm | 46 | 48 | 24 | 1/10/35 | 1/9/38 | 22/46 | 20/48 | 4/4 | 0.049→0.042 | -0.24 |

## Quality-flag totals (across all 4 cells × 8 stories per model)

**No quality flags raised across any cell.**

## ASVS category distribution per cell (where did the generators direct attention?)

This shows the modal ASVS categories the generated reqs map to per (project, model). If a model is generating off-topic reqs at scale, this will show as an unusual ASVS mix.

### mastodon

  GT ASVS distribution: {'V2': 5, 'V5': 6, 'V12': 4, 'V4': 4, 'V8': 1, 'V9': 1, 'V6': 2, 'V13': 1, 'V7': 1}
  qwen3: baseline = {'V5': 7, 'V4': 6, 'V2': 4, 'V14': 4, 'V13': 4, 'V12': 2, 'V10': 2, 'V7': 2, 'V9': 2, 'V6': 2, 'V3': 1, 'V11': 1, 'V8': 1}; rag = {'V2': 9, 'V4': 9, 'V5': 8, 'V8': 5, 'V9': 4, 'V13': 3, 'V10': 2, 'V6': 2, 'V11': 2, 'V12': 1, 'V1': 1, 'V3': 1, 'V7': 1}
  qwen3.6: baseline = {'V5': 9, 'V2': 8, 'V9': 3, 'V8': 3, 'V11': 3, 'V4': 3, 'V10': 2, 'V6': 2, 'V13': 2, 'V12': 1, 'V3': 1, 'V7': 1}; rag = {'V2': 11, 'V5': 7, 'V8': 5, 'V9': 4, 'V12': 4, 'V4': 4, 'V10': 2, 'V7': 2, 'V6': 1, 'V13': 1}
  gemma4: baseline = {'V5': 7, 'V4': 5, 'V11': 3, 'V8': 3, 'V2': 2, 'V3': 2, 'V12': 2, 'V1': 2, 'V9': 2, 'V13': 2, 'V7': 2}; rag = {'V4': 7, 'V2': 6, 'V5': 6, 'V8': 6, 'V9': 3, 'V14': 2, 'V12': 2, 'V7': 2, 'V13': 2, 'V10': 1, 'V1': 1, 'V11': 1, 'V3': 1}
  mistral-sm: baseline = {'V2': 10, 'V5': 6, 'V9': 5, 'V11': 5, 'V6': 4, 'V7': 4, 'V8': 3, 'V4': 3, 'V12': 2, 'V13': 2, 'V14': 2, 'V10': 1}; rag = {'V2': 10, 'V5': 10, 'V9': 8, 'V4': 7, 'V12': 3, 'V7': 3, 'V8': 2, 'V11': 2, 'V6': 2, 'V3': 1}

### keycloak

  GT ASVS distribution: {'V2': 5, 'V3': 6, 'V5': 3, 'V9': 2, 'V14': 1, 'V6': 6, 'V4': 2, 'V8': 1, 'V7': 1}
  qwen3: baseline = {'V2': 7, 'V3': 6, 'V4': 4, 'V13': 4, 'V6': 4, 'V14': 3, 'V5': 3, 'V7': 1, 'V8': 1}; rag = {'V2': 11, 'V6': 11, 'V4': 8, 'V3': 7, 'V14': 4, 'V9': 2, 'V7': 2, 'V13': 1, 'V5': 1, 'V8': 1}
  qwen3.6: baseline = {'V2': 8, 'V13': 6, 'V3': 6, 'V6': 5, 'V4': 5, 'V5': 3, 'V9': 2, 'V8': 1, 'V7': 1}; rag = {'V4': 9, 'V6': 6, 'V3': 6, 'V8': 4, 'V9': 4, 'V7': 3, 'V13': 3, 'V2': 3, 'V5': 1, 'V11': 1}
  gemma4: baseline = {'V3': 9, 'V6': 5, 'V5': 4, 'V13': 3, 'V4': 3, 'V7': 2, 'V2': 2, 'V9': 1, 'V8': 1}; rag = {'V3': 9, 'V4': 6, 'V2': 5, 'V5': 3, 'V6': 3, 'V9': 3, 'V13': 3, 'V8': 3, 'V11': 2, 'V7': 2, 'V14': 1}
  mistral-sm: baseline = {'V13': 17, 'V2': 9, 'V6': 6, 'V3': 6, 'V7': 3, 'V9': 3, 'V5': 2, 'V14': 1, 'V4': 1, 'V11': 1}; rag = {'V6': 11, 'V2': 10, 'V4': 8, 'V3': 7, 'V9': 5, 'V7': 2, 'V13': 2, 'V5': 2, 'V11': 1}

### bitwarden

  GT ASVS distribution: {'V2': 11, 'V6': 9, 'V3': 1, 'V12': 2, 'V4': 3, 'V13': 1}
  qwen3: baseline = {'V2': 9, 'V6': 7, 'V8': 5, 'V4': 3, 'V3': 3, 'V5': 2, 'V13': 2, 'V1': 1, 'V12': 1}; rag = {'V6': 15, 'V2': 15, 'V8': 7, 'V4': 4, 'V3': 3, 'V5': 2, 'V12': 1, 'V7': 1}
  qwen3.6: baseline = {'V2': 11, 'V6': 10, 'V4': 3, 'V9': 2, 'V8': 2, 'V5': 2, 'V7': 2, 'V1': 1, 'V12': 1, 'V13': 1, 'V3': 1}; rag = {'V6': 15, 'V2': 14, 'V8': 4, 'V4': 3, 'V5': 1, 'V12': 1, 'V7': 1, 'V9': 1}
  gemma4: baseline = {'V6': 7, 'V2': 6, 'V5': 4, 'V8': 3, 'V3': 3, 'V11': 2, 'V4': 2, 'V7': 2, 'V12': 1, 'V13': 1}; rag = {'V6': 11, 'V2': 8, 'V4': 5, 'V8': 4, 'V3': 4, 'V5': 2, 'V1': 2, 'V12': 1, 'V7': 1, 'V11': 1, 'V13': 1}
  mistral-sm: baseline = {'V2': 18, 'V6': 7, 'V8': 5, 'V4': 4, 'V7': 3, 'V3': 2, 'V5': 2, 'V12': 2, 'V9': 2}; rag = {'V6': 17, 'V2': 12, 'V4': 5, 'V9': 3, 'V7': 3, 'V8': 2, 'V12': 2, 'V3': 2, 'V1': 1, 'V13': 1}

### gitlab

  GT ASVS distribution: {'V4': 13, 'V5': 4, 'V8': 1, 'V12': 2, 'V6': 1, 'V2': 1, 'V3': 2}
  qwen3: baseline = {'V4': 15, 'V5': 6, 'V7': 5, 'V10': 3, 'V6': 3, 'V11': 1, 'V8': 1, 'V3': 1, 'V9': 1, 'V13': 1}; rag = {'V4': 15, 'V7': 7, 'V8': 5, 'V2': 4, 'V14': 3, 'V6': 3, 'V3': 3, 'V10': 3, 'V11': 2, 'V5': 2, 'V12': 1}
  qwen3.6: baseline = {'V4': 11, 'V5': 6, 'V8': 5, 'V7': 5, 'V10': 3, 'V9': 2, 'V11': 1, 'V12': 1, 'V3': 1, 'V2': 1}; rag = {'V4': 15, 'V8': 6, 'V7': 6, 'V5': 4, 'V2': 3, 'V6': 2, 'V1': 1, 'V11': 1, 'V14': 1, 'V10': 1}
  gemma4: baseline = {'V4': 11, 'V5': 6, 'V7': 5, 'V8': 3, 'V10': 2, 'V13': 1, 'V1': 1, 'V6': 1, 'V3': 1, 'V11': 1}; rag = {'V4': 13, 'V7': 6, 'V11': 4, 'V8': 3, 'V6': 2, 'V3': 2, 'V12': 2, 'V5': 2, 'V14': 1, 'V9': 1, 'V1': 1, 'V13': 1, 'V2': 1}
  mistral-sm: baseline = {'V4': 9, 'V11': 7, 'V7': 7, 'V8': 6, 'V5': 5, 'V2': 5, 'V1': 4, 'V9': 2, 'V6': 1, 'V13': 1}; rag = {'V4': 14, 'V7': 8, 'V2': 6, 'V8': 5, 'V5': 4, 'V11': 3, 'V6': 3, 'V9': 1, 'V10': 1, 'V12': 1, 'V1': 1, 'V3': 1}

### mattermost

  GT ASVS distribution: {'V2': 5, 'V4': 9, 'V5': 1, 'V3': 2, 'V12': 4, 'V8': 1, 'V6': 1, 'V13': 1}
  qwen3: baseline = {'V4': 8, 'V2': 5, 'V3': 5, 'V5': 5, 'V14': 4, 'V6': 2, 'V12': 2, 'V9': 2, 'V7': 2, 'V8': 1, 'V10': 1}; rag = {'V4': 13, 'V2': 7, 'V3': 4, 'V7': 4, 'V5': 4, 'V8': 4, 'V6': 3, 'V14': 3, 'V13': 2, 'V9': 2, 'V12': 1, 'V10': 1}
  qwen3.6: baseline = {'V4': 9, 'V2': 8, 'V5': 5, 'V10': 4, 'V3': 3, 'V7': 3, 'V8': 2, 'V12': 2, 'V9': 1, 'V6': 1}; rag = {'V4': 14, 'V2': 6, 'V5': 5, 'V8': 5, 'V3': 3, 'V9': 3, 'V7': 2, 'V12': 2, 'V6': 1, 'V10': 1, 'V11': 1}
  gemma4: baseline = {'V4': 8, 'V7': 4, 'V5': 4, 'V2': 3, 'V13': 3, 'V3': 3, 'V9': 1, 'V11': 1, 'V8': 1, 'V12': 1, 'V10': 1, 'V6': 1}; rag = {'V4': 12, 'V8': 5, 'V5': 5, 'V2': 3, 'V3': 3, 'V9': 3, 'V7': 3, 'V11': 3, 'V13': 1, 'V10': 1, 'V14': 1}
  mistral-sm: baseline = {'V2': 10, 'V4': 10, 'V5': 5, 'V7': 5, 'V3': 4, 'V14': 3, 'V9': 2, 'V8': 2, 'V10': 2, 'V12': 1, 'V11': 1, 'V13': 1}; rag = {'V4': 13, 'V2': 6, 'V3': 4, 'V13': 4, 'V7': 4, 'V8': 4, 'V6': 3, 'V9': 3, 'V5': 3, 'V12': 2, 'V11': 1, 'V10': 1}


## Sample requirements that did and didn't match

Highest-similarity match across each cell (best case)

- **mastodon × qwen3** (rag, sim=0.89, `match`):
    gen: 'ActivityPub HTTP signature verification. All incoming ActivityPub requests (e.g., Follow, Accept, Block) MUST be verified using HTTP Signatu'
    gt:  'ActivityPub Signature Verification. Incoming ActivityPub messages must have HTTP signatures verified. Unsigned or invalid signatures must be'
- **mastodon × qwen3.6** (baseline, sim=0.88, `match`):
    gen: 'ActivityPub Signature Verification. The system MUST verify the HTTP Signature (RFC 8252) on all incoming ActivityPub requests (e.g., Follow,'
    gt:  'ActivityPub Signature Verification. Incoming ActivityPub messages must have HTTP signatures verified. Unsigned or invalid signatures must be'
- **mastodon × gemma4** (rag, sim=0.85, `match`):
    gen: 'Immutable Moderation Audit Logging. Every moderation action (User Suspension, Content Removal, Domain Block, and Appeal decisions) MUST be l'
    gt:  'Moderation Audit Trail. All moderation actions must be logged with actor, action, target, and timestamp. Logs must be tamper-evident'
- **mastodon × mistral-sm** (baseline, sim=0.90, `match`):
    gen: 'LDAP bind credential protection. LDAP bind credentials MUST be stored encrypted at rest using AES-256-GCM with a per-secret IV. The encrypti'
    gt:  'LDAP Credential Protection. LDAP bind credentials must be stored encrypted. Credentials must not appear in logs or error messages'
- **keycloak × qwen3** (rag, sim=0.90, `match`):
    gen: 'LDAP bind credential encryption at rest. LDAP bind credentials MUST be encrypted at rest using AES-256-GCM with a unique per-realm initializ'
    gt:  'LDAP Bind Credential Protection. LDAP bind credentials must be encrypted at rest using AES-256 or equivalent. Credentials must not appear in'
- **keycloak × qwen3.6** (baseline, sim=0.89, `match`):
    gen: 'Secure LDAP Bind Credentials Storage. The LDAP bind DN and password MUST be stored encrypted at rest using AES-256-GCM or equivalent. The en'
    gt:  'LDAP Bind Credential Protection. LDAP bind credentials must be encrypted at rest using AES-256 or equivalent. Credentials must not appear in'
- **keycloak × gemma4** (rag, sim=0.88, `match`):
    gen: "Token Expiration and Temporal Validation. The server MUST validate the 'exp' (expiration time) claim of all presented tokens. Any token with"
    gt:  'Token Expiration Enforcement. Token expiration claims (exp) must be validated with clock skew tolerance no greater than 60 seconds. Expired '
- **keycloak × mistral-sm** (rag, sim=0.92, `match`):
    gen: 'Token revocation propagation. Token revocation MUST be propagated to all resource servers within 5 seconds of revocation. Revoked tokens MUS'
    gt:  'Token Revocation Propagation. Token revocation must propagate to all resource servers within 5 minutes. Introspection endpoints must return '
- **bitwarden × qwen3** (baseline, sim=0.88, `match`):
    gen: 'API token generation with cryptographically secure randomness. API tokens MUST be generated using a cryptographically secure random number g'
    gt:  'API Token Secure Generation. API tokens must be generated using cryptographically secure random number generator with minimum 256 bits of en'
- **bitwarden × qwen3.6** (baseline, sim=0.88, `match`):
    gen: 'Master Password Strength Enforcement. The system MUST enforce a minimum master password complexity policy: at least 8 characters, containing'
    gt:  'Master Password Strength Enforcement. Registration must enforce minimum master password strength: 12 characters minimum, reject commonly bre'
- **bitwarden × gemma4** (rag, sim=0.90, `match`):
    gen: 'Secure asymmetric key exchange for shared items. When sharing items between users or organizations, the system MUST use RSA-2048 asymmetric '
    gt:  'Secure Key Exchange for Sharing. Shared item encryption keys must be exchanged using asymmetric encryption (RSA-2048 minimum). Shared keys m'
- **bitwarden × mistral-sm** (baseline, sim=0.91, `match`):
    gen: 'Role-based access control for collections. Collection access MUST be enforced based on user roles (Owner, Admin, Manager, User). The server '
    gt:  'Role-Based Collection Access Control. Collection access must be enforced based on user role (Owner, Admin, Manager, User). Role escalation m'
- **gitlab × qwen3** (baseline, sim=0.87, `match`):
    gen: "Visibility enforcement based on project settings. Issue visibility MUST be enforced based on the project's visibility level (Private, Intern"
    gt:  'Issue Visibility Enforcement. Issue visibility must respect project visibility and confidential flag. Confidential issues must only be visib'
- **gitlab × qwen3.6** (rag, sim=0.87, `match`):
    gen: "Secure CI/CD Variable Masking and Protection. Variables marked as 'masked' MUST be replaced with '[MASKED]' in all job logs, including stdou"
    gt:  'Secret Variable Masking. Masked CI/CD variables must never appear in job logs or artifacts. Variable values must be redacted in all output'
- **gitlab × gemma4** (rag, sim=0.87, `match`):
    gen: "Masking and Protection of CI/CD Variables. All sensitive CI/CD variables MUST be configured with 'Masked' and/or 'Protected' flags. Masked v"
    gt:  'Secret Variable Masking. Masked CI/CD variables must never appear in job logs or artifacts. Variable values must be redacted in all output'
- **gitlab × mistral-sm** (baseline, sim=0.87, `match`):
    gen: 'Visibility and confidentiality controls. Issue visibility MUST respect project settings for public/private status and confidentiality flags.'
    gt:  'Issue Visibility Enforcement. Issue visibility must respect project visibility and confidential flag. Confidential issues must only be visib'
- **mattermost × qwen3** (rag, sim=0.89, `match`):
    gen: 'Guest access restricted to assigned channels only. Guest users MUST be restricted to accessing only the channels they were explicitly invite'
    gt:  'Guest Channel Restriction. Guest accounts must only access explicitly granted channels. Guests must not see or access other channels'
- **mattermost × qwen3.6** (rag, sim=0.84, `partial`):
    gen: 'Channel-specific guest access scoping. Guest accounts MUST be scoped to specific channels only. The system MUST prevent guests from discover'
    gt:  'Guest Channel Restriction. Guest accounts must only access explicitly granted channels. Guests must not see or access other channels'
- **mattermost × gemma4** (baseline, sim=0.87, `match`):
    gen: "Guest Action Restriction and Permission Scoping. Guest accounts MUST be assigned a restricted role that lacks 'System Admin' or 'Team Admin'"
    gt:  'Guest Action Limitation. Guest accounts must have restricted actions (no team admin, limited integrations). Privilege escalation must be pre'
- **mattermost × mistral-sm** (rag, sim=0.93, `match`):
    gen: 'Guest channel access restriction. Guests MUST be restricted to only the channels they are explicitly invited to. The system MUST prevent gue'
    gt:  'Guest Channel Restriction. Guest accounts must only access explicitly granted channels. Guests must not see or access other channels'


## Conclusions from the deep read

### Volume
- Total baseline requirements generated across all 20 cells × 8 stories: **753**
- Total RAG requirements generated: **883**
- Total quality flags (empty title/desc, missing ASVS, etc.): **0** out of 1636 reqs — that's 0.0%

### Match quality (aggregated across all 20 cells)
- Baseline matches: 24 matches, 208 partial, 521 no_match
- RAG matches: 24 matches, 210 partial, 649 no_match
- Baseline match rate (match+partial / all): 0.308
- RAG match rate (match+partial / all): 0.265

### ASVS drift (generated ASVS category absent from GT's ASVS categories)
- Baseline: 365/753 (48.5%) generated reqs map to ASVS categories not represented in their story's GT
- RAG: 407/883 (46.1%)
- Note: 'drift' is not necessarily bad — the generator may be producing additional valid security concerns beyond the hand-curated GT's scope.

### bitwarden code-chunk bug
- bitwarden story 0 doc chunks: 20, code chunks: 0 (across all 4 models)
- root cause: driver looks for `repos/bitwarden`; actual dir is `repos/bitwarden-server`. Code search silently produced 0 chunks for all bitwarden cells.
