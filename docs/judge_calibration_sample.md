# Judge Calibration — Hand-Labeling Worksheet

**Task**: For each (story, chunk) pair below, decide whether the chunk's
content is **relevant** to the story's security requirements work, 
**partial** (useful background but doesn't directly answer), or 
**irrelevant**.

Fill in the `Human verdict` line under each block with one of:
`relevant`, `partial`, `irrelevant`.

**DO NOT** look at `judge_calibration_sample.json` while labeling — that
contains the LLM's predictions which would bias you. After all blocks
are filled, run `scripts/score_judge_calibration.py` to compute Cohen's κ.

Guidelines for the `partial` boundary:
- **relevant** = the chunk directly addresses one or more of the story's
  security requirements (e.g. enumerates auth requirements for an auth
  story, specifies threat-model items for a threat-model story).
- **partial** = the chunk is on a related topic (admin config, an
  adjacent feature's docs) and would inform some requirements but is not
  itself a requirements specification.
- **irrelevant** = the chunk is off-topic (covers a different feature,
  generic boilerplate, project metadata, unrelated security topic).

---

## S001 — bitwarden/BW-US-005

**Story**: Credential Storage and Retrieval

**Story text**: As a user, I want to store and retrieve my passwords, notes, and card details securely in my vault

**Acceptance criteria**:
  - Items are encrypted before storage
  - Decryption happens client-side only
  - Custom fields can be added
  - Items can be organized in folders

**Retrieved chunk source**:
`index.md > Authentication > Client handles the response > Use the decryption information to prepare for decryption > User key (`userKey`)`

**Retrieved chunk content**:
```
### [5] index.md > Authentication > Client handles the response > Use the decryption information to prepare for decryption > User key (`userKey`) (Relevance: 0.41)
Section: Authentication > Client handles the response > Use the decryption information to prepare for decryption > User key (`userKey`)

The User Key is the symmetric encryption key used to decrypt the user's vault.

The specifics of how the User Key is set are defined in each login strategy:

- **Password and Login with Device strategies**
  - The `Key` in the token response is stored in `userKeyMasterKey` in the account state, and the
    `masterKey` already in state from the user entering their master password is used to decrypt and
    store the `userKey`.
- **API strategy**
  - The `Key` in the token response is stored in `userKeyMasterKey` in the account state.
  - If the `ApiUseKeyConnector` property is set on the token response, we retrieve the `masterKey`
    from state and use it to decrypt and store the `userKey`.
- **SSO strategy**
  - Trusted Device Encryption presents us with multiple ways to obtain the User Key, and these are
    handled here, in the SSO login strategy.
  - These include:
    - Using the device key if the user is on a Trusted Device
    - Using an approved Admin Approval request to receive the key
    - Using the master key if the user has a master password
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S002 — bitwarden/BW-US-003

**Story**: Biometric Unlock Setup

**Story text**: As a user, I want to enable biometric unlock (fingerprint/face) so I can access my vault more conveniently on my device

**Acceptance criteria**:
  - User has previously authenticated with master password
  - Biometric key is securely stored on device
  - Biometric unlock works without network access
  - User can disable biometric unlock

**Retrieved chunk source**:
`index.md > Authentication > Appendix: `UserDecryptionOptions``

**Retrieved chunk content**:
```
### [1] index.md > Authentication > Appendix: `UserDecryptionOptions` (Relevance: 0.55)
Section: Authentication > Appendix: `UserDecryptionOptions`

The token response will contain an instance of `UserDecryptionOptions`. This object contains the
following properties, which describe how a user will be able to decrypt their vault.

- `HasMasterPassword` - Will be `true` if the user has a master password in the database.
- `TrustedDeviceOption` - Contains the properties defining whether a user can decrypt their
  symmetric key using Trusted Device Encryption.
  - `HasAdminApproval` - Will be `true` if a user can use Admin Approval to request approval for a
    new Trusted Device.
  - `HasLoginApprovingDevice` - Will be `true` if a user has any eligible devices that can be used
    to request approval for a new Trusted Device.
  - `HasManageResetPasswordPermission` - Will be `true` if the user has the `ManageResetPassword`
    permission. This is required so that the receiving client can know whether to require the user
    to set a master password.
  - `EncryptedPrivateKey` - If the device making the request is trusted, this will contain the a
    device-specific private key, encrypted with the device key that exists only on the Trusted
    Device.
  - `EncryptedUserKey` - If the device making the request is trusted, this will contain the user's
    symmetric encryption key, encrypted with the public key corresponding to the private key in
    `EncryptedPrivateKey` above.
- `KeyConnectorOption` - Contains the properties defining whether a user can decrypt their symmetric
  key using Key Connector.
  - `KeyConnectorUrl` - The URL for the Key Connector instance.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S003 — gitlab/GL-US-008

**Story**: Group Permission Management

**Story text**: As a group owner, I want to manage member permissions so I can control access across projects

**Acceptance criteria**:
  - Members can be added with roles
  - Role inheritance works across subgroups
  - Permissions can be restricted
  - Access can be time-limited

**Retrieved chunk source**:
`application_setting.rb:53 > application_setting.rb:53`

**Retrieved chunk content**:
```
### [2] application_setting.rb:53 > application_setting.rb:53 (Relevance: 0.55)
### code search hit (pattern: 'check_access')
File: app/models/application_setting.rb:53
```
add_authentication_token_field :health_check_access_token, insecure: true # rubocop:todo -- https://gitlab.com/gitlab-org/gitlab/-/issues/376751
```


---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S004 — bitwarden/BW-US-002

**Story**: Vault Unlock with Master Password

**Story text**: As a user, I want to unlock my vault using my master password so I can access my stored credentials

**Acceptance criteria**:
  - User enters master password
  - Vault decryption key is derived from password
  - Vault items are decrypted client-side
  - Failed attempts are tracked

**Retrieved chunk source**:
`0022-server-authorization.md > 0022 - Authorization > Considered options > Third-party solution`

**Retrieved chunk content**:
```
### [9] 0022-server-authorization.md > 0022 - Authorization > Considered options > Third-party solution (Relevance: 0.43)
Section: 0022 - Authorization > Considered options > Third-party solution

Including but not limited to:

- [OpenFGA](https://openfga.dev), an open-source implementation of Google's
  [Zanzibar authorization system](https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/)
  (used in Google Docs).
- [Casbin](https://casbin.org/), an open-source authorization library supporting a variety of
  different models.

Each has its own domain-specific language (DSL) used to define authorization rules as well as a
storage layer to store access information about the users and objects in the application. The
storage is generally a separate database to the Bitwarden database and must be kept up-to-date with
users' roles and relationships in Bitwarden.

#### Advantages

- The DSLs appear to be a flexible and robust way to define an authorization model.
- Support for complex permission structures.
- Reduces load on the Bitwarden database, designed for high performance.
- Single source of truth, strongly separated from our application logic.

#### Disadvantages

- Vendor lock-in for a core part of our architecture.
- New concepts and a DSL unfamiliar to most developers - would probably require a single team to
  become experts in this and maintain it for all teams, which does not work well with our team
  structure.
- Very costly to implement in terms of engineering resources, particularly for developers, BRE and
  SRE.
- Risk of the authorization data store becoming out of step with the main Bitwarden database.
- May be over-engineered for our current requirements and scale.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S005 — mastodon/MT-US-002

**Story**: Media Attachment Upload

**Story text**: As a user, I want to upload images and videos with my posts so I can share media content

**Acceptance criteria**:
  - Images can be attached to posts
  - Videos are transcoded for playback
  - Alt text can be added
  - Media is processed securely

**Retrieved chunk source**:
`user_posting.md > Attachments {#attachments} > Files {#media}`

**Retrieved chunk content**:
```
### [1] user_posting.md > Attachments {#attachments} > Files {#media} (Relevance: 0.47)
Section: Attachments {#attachments} > Files {#media}

{{< figure src="assets/compose-media-attachment.png" width="50%" caption="New post with a thumbnail for attached media, with an Edit button for additional options" >}}

Click the photo stack to attach a file to your post. You can attach the following:

* **Images** (PNG, JPG, HEIF, WEBP, AVIF) **up to 16MB**. Images will be downscaled to 8.3 megapixels (enough for a 3840x2160 image). Up to 4 images can be attached, per post.
* **Animated GIFs** (GIFV) **up to 16MB** are converted to soundless MP4s. GIF dimensions must be less than 1MP (1280x720). You can also upload soundless MP4 and WebM files, which will be looped in clients the same way as an animated GIF. Only one animated GIF can be attached, per post.
  * Static GIF files have the same limits applied to them as animated GIF files. If you are having issues uploading these images, you will need to convert it to another supported image format.
* **Videos** (MP4, M4V, MOV, WebM) **up to 99MB**. Video will be transcoded to H.264 MP4 with a maximum bitrate of 1300kbps and framerate of 120fps. Only one video can be attached, per post.
* **Audio** (MP3, OGG, WAV, FLAC, OPUS, AAC, M4A, 3GP) **up to 99MB**. Audio will be transcoded to MP3 using V2 VBR (roughly 192kbps). Only one audio file can be attached, per post.

#### Editing media {#edit}

{{< figure src="assets/edit-media.png" caption="Edit media to add a media description (ALT text) or choose a focal point for the preview thumbnail" >}}

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S006 — bitwarden/BW-US-003

**Story**: Biometric Unlock Setup

**Story text**: As a user, I want to enable biometric unlock (fingerprint/face) so I can access my vault more conveniently on my device

**Acceptance criteria**:
  - User has previously authenticated with master password
  - Biometric key is securely stored on device
  - Biometric unlock works without network access
  - User can disable biometric unlock

**Retrieved chunk source**:
`credentials.md > Credentials > Discoverability > Client-side discoverable credentials`

**Retrieved chunk content**:
```
### [5] credentials.md > Credentials > Discoverability > Client-side discoverable credentials (Relevance: 0.48)
Section: Credentials > Discoverability > Client-side discoverable credentials

:::info

These credentials have been previously known as resident credentials or resident keys. Due to the
phrases `ResidentKey` and `residentKey` being widely used in both the WebAuthn API and also in the
Authenticator Model (e.g. in dictionary member names, algorithm variable names, and operation
parameters) the usage of resident within their names has not been changed for backwards
compatibility purposes.

:::

This credential is usable in authentication operations where the RP does not provide any
`credentialId`, meaning that the RP does not need to identify the user first.

As a result, an authenticator that can handle discoverable credentials can create assertion
signatures using just an `rpId`. This enables single-click authentication flows where the returned
assertion data contains everything the RP needs to identify and authenticate the user.

This implies that the source of the credential must be stored either in the authenticator or the
client platform (see [Client Side Credentials](#client-side-credentials)).
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S007 — bitwarden/BW-US-007

**Story**: Organization Vault Sharing

**Story text**: As an organization admin, I want to share credentials with team members through collections so we can manage shared access securely

**Acceptance criteria**:
  - Items can be shared to collections
  - Collection access is role-based
  - Encryption keys are shared securely
  - Access can be revoked

**Retrieved chunk source**:
`credentials.md > Credentials > Discoverability > Client-side discoverable credentials`

**Retrieved chunk content**:
```
### [5] credentials.md > Credentials > Discoverability > Client-side discoverable credentials (Relevance: 0.41)
Section: Credentials > Discoverability > Client-side discoverable credentials

:::info

These credentials have been previously known as resident credentials or resident keys. Due to the
phrases `ResidentKey` and `residentKey` being widely used in both the WebAuthn API and also in the
Authenticator Model (e.g. in dictionary member names, algorithm variable names, and operation
parameters) the usage of resident within their names has not been changed for backwards
compatibility purposes.

:::

This credential is usable in authentication operations where the RP does not provide any
`credentialId`, meaning that the RP does not need to identify the user first.

As a result, an authenticator that can handle discoverable credentials can create assertion
signatures using just an `rpId`. This enables single-click authentication flows where the returned
assertion data contains everything the RP needs to identify and authenticate the user.

This implies that the source of the credential must be stored either in the authenticator or the
client platform (see [Client Side Credentials](#client-side-credentials)).
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S008 — keycloak/KC-US-007

**Story**: JWT Token Signing

**Story text**: As a security architect, I want Keycloak to sign JWT tokens with strong cryptographic algorithms so that tokens cannot be forged or tampered with

**Acceptance criteria**:
  - Tokens are signed using RS256 or stronger algorithm
  - Public keys are available via JWKS endpoint
  - Key rotation is supported without service interruption
  - Signature validation is required for token acceptance

**Retrieved chunk source**:
`jwt-authorization-grant.md > Configuration`

**Retrieved chunk content**:
```
### [3] jwt-authorization-grant.md > Configuration (Relevance: 0.58)
Section: Configuration

Only confidential clients can request a JWT authorization grant. In
order to allow a client to send such a grant, the client should be
configured accordingly. Using the admin console, **clients** → select
your client → **Settings** tab → **Capability config** section.

1.  Enable the **JWT Authorization Grant** capability.

2.  In the option **Allowed Identity Providers for JWT Authorization
    Grant**, select all the Identity Providers that this client can use
    for authorizing grants.

<figure>
<img src="jwt-authorization-grant-client-config.png"
alt="Client configuration for JWT Authorization Grant" />
<figcaption aria-hidden="true">Client configuration for JWT
Authorization Grant</figcaption>
</figure>

The Identity Provider (both types commented in the introduction) needs
to also be configured to establish the relationship that will validate
the assertion. In **Identity providers** → select your OIDC or JWT
provider → **Settings** tab → **Authorization Grant Settings** section.

1.  Enable the option **JWT Authorization Grant** switch.

2.  Configure the rest of options as desired.

- **Allow assertion reuse**: By default {project_name} only allows
      one-time assertions (re-using is not permitted) and the the `jti`
      claim should be present in the JWT (unique identifier of the
      token).

- **Max allowed assertion expiration**: The maximum expiration the
      server will allow in the assertion. Default 5 minutes.

- **Assertion signature algorithm**: The signature algorithm that is
      valid for assertions. If not specified any signature is valid.

- **Allowed clock skew**: Clock skew in seconds that is tolerated
      when validating identity provider tokens. Default value is zero.

- **Limit access token expiration**: If enabled the access token
      lifespan will be limited to the expiration of the JWT assertion
      but only if the JWT assertion e

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S009 — keycloak/KC-US-001

**Story**: OIDC Authorization Code Flow

**Story text**: As an application developer, I want to integrate my application with Keycloak using OpenID Connect authorization code flow so that users can authenticate securely without my application handling credentials directly

**Acceptance criteria**:
  - Application can register as an OIDC client with Keycloak
  - Users are redirected to Keycloak for authentication
  - Authorization code is exchanged for tokens
  - ID token contains user identity claims
  - Access token can be used to access protected resources

**Retrieved chunk source**:
`partials-supported-grant-types.md > Supported Grant Types > Authorization code`

**Retrieved chunk content**:
```
### [2] partials-supported-grant-types.md > Supported Grant Types > Authorization code (Relevance: 0.69)
Section: Supported Grant Types > Authorization code

The Authorization Code flow redirects the user agent to {project_name}.
Once the user has successfully authenticated with {project_name}, an
Authorization Code is created and the user agent is redirected back to
the application. The application then uses the authorization code along
with its credentials to obtain an Access Token, Refresh Token and ID
Token from {project_name}.

The flow is targeted towards web applications, but is also recommended
for native applications, including mobile applications, where it is
possible to embed a user agent.

For more details refer to the [Authorization Code
Flow](https://openid.net/specs/openid-connect-core-1_0.html#CodeFlowAuth)
in the OpenID Connect specification.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S010 — bitwarden/BW-US-002

**Story**: Vault Unlock with Master Password

**Story text**: As a user, I want to unlock my vault using my master password so I can access my stored credentials

**Acceptance criteria**:
  - User enters master password
  - Vault decryption key is derived from password
  - Vault items are decrypted client-side
  - Failed attempts are tracked

**Retrieved chunk source**:
`index.md > Authentication > Identity API grants access > Generating a response`

**Retrieved chunk content**:
```
### [3] index.md > Authentication > Identity API grants access > Generating a response (Relevance: 0.45)
Section: Authentication > Identity API grants access > Generating a response

Once the request has been validated through the appropriate logic for the given grant type, the
Identity API will generate a response. At Bitwarden, this response contains an `access_token` with
claims and also a custom JSON object that contains essential information to start the vault
decryption process.

:::info Known Device

It is at this point, when the token request has been validated, that the requesting device is stored
and associated with the user as a “known device”.

:::

#### Authentication information

For a validated password and SSO token requests (grant types of `password` and
`authorization_code`), the response will contain an `access_token`, which is a JWT with the
following claims:

- `device` - the device identifier.
- `premium` - a boolean indicating whether they have access to premium features.
- `email` - the user’s email address.
- `email_verified` - whether the user has verified their email address.
- `sstamp` - the user’s security stamp (a unique GUID for the user stored in the `User` table and
  regenerated when a new password or key is generated). A changed `sstamp` indicates that the user
  should re-authenticate.
- `name` - The user’s name.
- **Organization claims**
  - `orgowner` - The `ID` of any org(s) for which the user is the owner.
  - `orgadmin` - The `ID` of any org(s) for which the user is the administrator.
  - `orgmanager` - The `ID` of the org(s) for which the user is the manager.
  - `orguser` - The `ID` of the org(s) for which the user is a user.
  - `orgcustom` - The `ID` of the org(s) for which the user has custom permissions. In this case,
    the user will also have custom claims added as defined for that org.
- **Provider claims**
  - `providerprovideradmin` - The `ID` of the provider(s) for which the user is an admin.
  - `providerservice

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S011 — gitlab/GL-US-006

**Story**: API Token Authentication

**Story text**: As a developer, I want to generate API tokens so I can interact with GitLab programmatically

**Acceptance criteria**:
  - Personal access tokens can be created
  - Token scopes can be configured
  - Tokens can be revoked
  - Token usage is logged

**Retrieved chunk source**:
`README.md > GitLab > Third-party applications`

**Retrieved chunk content**:
```
### [1] README.md > GitLab > Third-party applications (Relevance: 0.45)
Section: GitLab > Third-party applications

There are a lot of [third-party applications integrating with GitLab](https://about.gitlab.com/applications/). These include GUI Git clients, mobile applications and API wrappers for various languages.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S012 — keycloak/KC-US-002

**Story**: Redirect URI Configuration

**Story text**: As an application administrator, I want to configure allowed redirect URIs for my OIDC client so that after authentication users are redirected back to my application

**Acceptance criteria**:
  - Admin can specify multiple redirect URIs per client
  - Redirect URIs support wildcards for subdomains
  - Invalid redirect URIs are rejected during authorization
  - Users are redirected to the specified URI after login

**Retrieved chunk source**:
`specifications.md > OAuth`

**Retrieved chunk content**:
```
### [5] specifications.md > OAuth (Relevance: 0.45)
Section: OAuth

8693)](https://datatracker.ietf.org/doc/html/rfc8693) | Supported (see comments) | | Token exchange V2 only supports the internal to internal use-case, so the specification is only partially supported now.

See \<@links.securingapps id="token-exchange" /\> for more information.

|
| [The OAuth 2.0 Authorization Framework: JWT-Secured Authorization Request (JAR) (RFC 9101)](https://datatracker.ietf.org/doc/html/rfc9101) | Supporte |  |  |
| [OAuth 2.0 Authorization Server Issuer Identification (RFC 9207)](https://datatracker.ietf.org/doc/html/rfc9207) | Supporte |  |  |
| [OAuth 2.0 Demonstrating Proof of Possession (DPoP) (RFC 9449)](https://datatracker.ietf.org/doc/html/rfc9449) | Preview |  |  |
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S013 — bitwarden/BW-US-002

**Story**: Vault Unlock with Master Password

**Story text**: As a user, I want to unlock my vault using my master password so I can access my stored credentials

**Acceptance criteria**:
  - User enters master password
  - Vault decryption key is derived from password
  - Vault items are decrypted client-side
  - Failed attempts are tracked

**Retrieved chunk source**:
`crypto-guide.md > How to use cryptography in Bitwarden > crypto-guide.md > How to use cryptography in Bitwarden`

**Retrieved chunk content**:
```
### [1] crypto-guide.md > How to use cryptography in Bitwarden > crypto-guide.md > How to use cryptography in Bitwarden (Relevance: 0.60)
Section: How to use cryptography in Bitwarden
Section: How to use cryptography in Bitwarden

This guide is aimed at non-cryptography teams that want to consume cryptographic APIs in order to
build features that need end-to-end encryption. This guide provides an overview of available tools
for composing features that need cryptographic protection.

Currently, there is a set of low-level APIs
([EncString](https://github.com/bitwarden/sdk-internal/blob/c60a5d794732d2c0fc203feb21ce5851d5325fe1/crates/bitwarden-crypto/src/enc_string/symmetric.rs#L59),
[UnsignedSharedKey](https://github.com/bitwarden/sdk-internal/blob/c60a5d794732d2c0fc203feb21ce5851d5325fe1/crates/bitwarden-crypto/src/enc_string/asymmetric.rs#L58),
[MasterKey](https://github.com/bitwarden/sdk-internal/blob/c60a5d794732d2c0fc203feb21ce5851d5325fe1/crates/bitwarden-crypto/src/keys/master_key.rs#L32))
that have been used to build most features, with each team owning the cryptographic constructions
created. Recently, high-level safe primitives were introduced that will move the complexity out of
each team's ownership. These are not yet complete, and if a particular use-case is not covered by
them, teams should reach out to the key-management team! The goal of these is to have most teams
never have to think about cryptography, or having to do safety analysis, or to own any cryptographic
construct or protocol. These high-level primitives abstract away all complex details and give teams
a low-complexity, easy to use and hard to misuse interface to work with.

Primarily, this is aimed for consumption for end-to-end encrypted storage of long-term data, in
products such as the password manager or secrets manager.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S014 — keycloak/KC-US-002

**Story**: Redirect URI Configuration

**Story text**: As an application administrator, I want to configure allowed redirect URIs for my OIDC client so that after authentication users are redirected back to my application

**Acceptance criteria**:
  - Admin can specify multiple redirect URIs per client
  - Redirect URIs support wildcards for subdomains
  - Invalid redirect URIs are rejected during authorization
  - Users are redirected to the specified URI after login

**Retrieved chunk source**:
`partials-recommendations.md > Recommendations > Redirect URIs`

**Retrieved chunk content**:
```
### [2] partials-recommendations.md > Recommendations > Redirect URIs (Relevance: 0.67)
Section: Recommendations > Redirect URIs

When using the redirect based flows, be sure to use valid redirect uris
for your clients. The redirect uris should be as specific as possible.
This especially applies to client-side (public clients) applications.
Failing to do so could result in:

- Open redirects - this can allow attackers to create spoof links that
  looks like they are coming from your domain

- Unauthorized entry - when users are already authenticated with
  {project_name}, an attacker can use a public client where redirect
  uris have not be configured correctly to gain access by redirecting
  the user without the users knowledge

In production for web applications always use `https` for all redirect
URIs. Do not allow redirects to http.

A few special redirect URIs also exist:

<div id="_installed_applications_url" wrapper="1">

`$$`[`http://127.0.0.1$$`](http://127.0.0.1$$)  

</div>

    This redirect URI is useful for native applications and allows the native application to create a web server on a random port that can be used to obtain the
    authorization code. This redirect uri allows any port. Note that per https://datatracker.ietf.org/doc/html/rfc8252#section-8.3[OAuth 2.0 for Native Apps], the use of
    `localhost` is *not* recommended and the IP literal `127.0.0.1` should be used instead.

<div id="_installed_applications_urn" wrapper="1">

`urn:ietf:wg:oauth:2.0:oob`  

</div>

    If you cannot start a web server in the client (or a browser is not available), you can use the special `urn:ietf:wg:oauth:2.0:oob` redirect uri.
    When this redirect uri is used, {project_name} displays a page with the code in the title and in a box on the page.
    The application can either detect that the browser title has changed, or the user can copy and paste the code manually to the application.
    With this redirect uri, a user can use a different device to obtain

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S015 — keycloak/KC-US-001

**Story**: OIDC Authorization Code Flow

**Story text**: As an application developer, I want to integrate my application with Keycloak using OpenID Connect authorization code flow so that users can authenticate securely without my application handling credentials directly

**Acceptance criteria**:
  - Application can register as an OIDC client with Keycloak
  - Users are redirected to Keycloak for authentication
  - Authorization code is exchanged for tokens
  - ID token contains user identity claims
  - Access token can be used to access protected resources

**Retrieved chunk source**:
`partials-available-endpoints.md > Available Endpoints > Endpoints > Authorization endpoint > partials-available-endpoints.md > Available Endpoints > Endpoints >`

**Retrieved chunk content**:
```
### [3] partials-available-endpoints.md > Available Endpoints > Endpoints > Authorization endpoint > partials-available-endpoints.md > Available Endpoints > Endpoints > Authorization endpoint (Relevance: 0.60)
Section: Available Endpoints > Endpoints > Authorization endpoint
Section: Available Endpoints > Endpoints > Authorization endpoint

/realms/{realm-name}/protocol/openid-connect/auth

The authorization endpoint performs authentication of the end-user. This
authentication is done by redirecting the user agent to this endpoint.

For more details see the [Authorization
Endpoint](https://openid.net/specs/openid-connect-core-1_0.html#AuthorizationEndpoint)
section in the OpenID Connect specification.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S016 — bitwarden/BW-US-003

**Story**: Biometric Unlock Setup

**Story text**: As a user, I want to enable biometric unlock (fingerprint/face) so I can access my vault more conveniently on my device

**Acceptance criteria**:
  - User has previously authenticated with master password
  - Biometric key is securely stored on device
  - Biometric unlock works without network access
  - User can disable biometric unlock

**Retrieved chunk source**:
`index.md > Authentication > Identity API grants access > Generating a response`

**Retrieved chunk content**:
```
### [5] index.md > Authentication > Identity API grants access > Generating a response (Relevance: 0.43)
Section: Authentication > Identity API grants access > Generating a response

- `providerserviceuser` - The `ID` of the provider(s) for which the user is a user. For API token requests (grant type of `client_credentials`), the response differs based on the
client type, as follows:

<!-- prettier-ignore -->
| Client Type  | Claims |
| ------------ | ------ |
| Installation | `sub` - the installation `ID` |
| Internal     | `sub` - the internal `ID` |
| Organization | `sub` - the organization `ID` |
| User         | `sub` - the user ID <br /> `amr` - set to `Application` and `external` <br /> Plus all of the user claims from a password authentication request as listed above. |

#### Decryption information

The response will also contain a custom JSON response object containing the following properties:

<!-- prettier-ignore -->
| Property | Purpose |
| -------- | -------- |
| `PrivateKey` | The user’s RSA private key, encrypted with the user's symmetric encryption key. |
| `Key` | The user’s symmetric encryption key, encrypted with the user's master password. |
| `UserDecryptionOptions` | The ways that the user has available to them to decrypt their symmetric key. See [User Decryption Options](#appendix-userdecryptionoptions) for details. |
| `Kdf` | The user’s defined KDF type. |
| `KdfIterations` | The user’s defined KDF iterations. |
| `ForcePasswordReset`  | A boolean indicating whether the user must immediately reset their password. |
| `MasterPasswordPolicy` | The master password policy for any organizations of which the user is a member. |
| `TwoFactorToken` | A token that can be used to bypass 2FA. Generated when a user chooses to "remember" their 2FA response. |
| `ResetMasterPassword` (_obsolete_) | A boolean indicating whether the user needs to set their master password. It is explicitly set to `false` for users with Key Connector configured. This has be

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S017 — keycloak/KC-US-006

**Story**: Client Registration

**Story text**: As a developer, I want to register new OIDC/OAuth2 clients with Keycloak so that my applications can authenticate users and request API access

**Acceptance criteria**:
  - Client ID and secret are generated during registration
  - Client can be configured as public or confidential
  - Allowed grant types can be specified
  - Token lifetimes can be customized per client

**Retrieved chunk source**:
`jwt-authorization-grant.md > Examples`

**Retrieved chunk content**:
```
### [4] jwt-authorization-grant.md > Examples (Relevance: 0.53)
Section: Examples

to validate the signature. ``` json
{"alg":"ES256", "kid":"2AOACLJmd5dQ8HPrDxwpkS-83yBhrzaLWSny9wmnYcY"}
```

{project_name} will validate the request and assertion. If everything is
correct, the response will contain an access token ready to be used.

``` json
{
  "access_token":"eyJhbG[...redacted...].eyJleH[...redacted...].RFnNEv[...redacted...]",
  "expires_in":300,
  "refresh_expires_in":0,
  "token_type":"Bearer",
  "not-before-policy":0,
  "scope":"email profile"
}
```

> [!NOTE]
> Following the spec recommendation, the JWT Authorization Grant never
> issues a refresh token and a [transient
> session]({adminguide_link}#_transient-session) is always created. The
> access token can be used normally in {project_name} through the
> introspection, user-info or any other endpoint. It will be valid until
> expired or explicitly revoked by the revocation endpoint.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S018 — bitwarden/BW-US-004

**Story**: Two-Factor Authentication Setup

**Story text**: As a user, I want to enable 2FA with TOTP or WebAuthn so I have an additional layer of security on my account

**Acceptance criteria**:
  - User can enable TOTP authenticator
  - User can register WebAuthn/FIDO2 devices
  - Backup codes are provided
  - 2FA is required on new device logins

**Retrieved chunk source**:
`operations.md > Operations > Registering a passkey`

**Retrieved chunk content**:
```
### [4] operations.md > Operations > Registering a passkey (Relevance: 0.52)
Section: Operations > Registering a passkey

name, and icon. - **extensions**: An object that contains any additional parameters that the server wants to include in the registration process. An example of this is the `prf` extension, which enables the credential to be used for data encryption.

3. **Client-Side WebAuthn Request:** The application receives the options object from the server and
   passes it to `navigator.credentials.create()`.

4. **Client-Side Credential Creation Request**: The WebAuthn Client receives the options object,
   validates it, searches for connected authenticators, and then sends the options object to all
   connected authenticators to create a new FIDO2 credential.

5. **User Interaction**: All connected authenticators will indicate that they have received the
   request and are ready to create a new credential (e.g. using a blinking LED). The user will then
   need to perform an action to confirm the creation of the new credential (e.g. by pressing a
   button on the authenticator).

6. **Authenticator Response**: Once the user confirms the creation of the new credential, the
   authenticator generates a new public-private key pair and creates a new credential object. This
   object includes the `credentialId`, public key, a signature, the type of authenticator used, the
   attestation statement, and other relevant information.

7. **Client-Side Response**: Once the credential is created, the client sends a request back to the
   server. This request includes the newly-created credential and all the other relevant information
   received from the authenticator.

8. **Server-Side Validation**: The server receives the request from the client and performs several
   checks to validate the authenticity and integrity of the received data. This includes verifying
   the challenge, verifying the signature of the credential, checking the attestation statement,
   etc.

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S019 — gitlab/GL-US-001

**Story**: Repository Creation and Access

**Story text**: As a user, I want to create repositories and control who can access them so I can manage my source code securely

**Acceptance criteria**:
  - User creates project with visibility level
  - Repository is initialized
  - Access permissions are set
  - Clone URL is generated

**Retrieved chunk source**:
`README.md > GitLab > Open source software to collaborate on code`

**Retrieved chunk content**:
```
### [1] README.md > GitLab > Open source software to collaborate on code (Relevance: 0.45)
Section: GitLab > Open source software to collaborate on code

To see how GitLab looks please see the [features page on our website](https://about.gitlab.com/features/).

- Manage Git repositories with fine grained access controls that keep your code secure
- Perform code reviews and enhance collaboration with merge requests
- Complete continuous integration (CI) and continuous deployment/delivery (CD) pipelines to build, test, and deploy your applications
- Each project can also have an issue tracker, issue board, and a wiki
- Used by more than 100,000 organizations, GitLab is the most popular solution to manage Git repositories on-premises
- Open-core, most of the code is MIT with the files in `/ee` being proprietary but source available and open for contributions.
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S020 — gitlab/GL-US-004

**Story**: Workspace File Operations

**Story text**: As a developer, I want to use workspaces to edit files directly in GitLab

**Acceptance criteria**:
  - Workspace can be created
  - Files can be edited
  - Changes can be committed
  - Workspace runs in isolated environment

**Retrieved chunk source**:
`README.md > GitLab > Licensing`

**Retrieved chunk content**:
```
### [5] README.md > GitLab > Licensing (Relevance: 0.42)
Section: GitLab > Licensing

See the [LICENSE](LICENSE) file for licensing information as it pertains to
files in this repository.
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S021 — keycloak/KC-US-001

**Story**: OIDC Authorization Code Flow

**Story text**: As an application developer, I want to integrate my application with Keycloak using OpenID Connect authorization code flow so that users can authenticate securely without my application handling credentials directly

**Acceptance criteria**:
  - Application can register as an OIDC client with Keycloak
  - Users are redirected to Keycloak for authentication
  - Authorization code is exchanged for tokens
  - ID token contains user identity claims
  - Access token can be used to access protected resources

**Retrieved chunk source**:
`oidc-layers.md > Supported Grant Types > Implicit`

**Retrieved chunk content**:
```
### [4] oidc-layers.md > Supported Grant Types > Implicit (Relevance: 0.51)
Section: Supported Grant Types > Implicit

The Implicit flow works similarly to the Authorization Code flow, but
instead of returning an Authorization Code, the Access Token and ID
Token is returned. This approach reduces the need for the extra
invocation to exchange the Authorization Code for an Access Token.
However, it does not include a Refresh Token. This results in the need
to permit Access Tokens with a long expiration; however, that approach
is not practical because it is very hard to invalidate these tokens.
Alternatively, you can require a new redirect to obtain a new Access
Token once the initial Access Token has expired. The Implicit flow is
useful if the application only wants to authenticate the user and deals
with logout itself.

You can instead use a Hybrid flow where both the Access Token and an
Authorization Code are returned.

One thing to note is that both the Implicit flow and Hybrid flow have
potential security risks as the Access Token may be leaked through web
server logs and browser history. You can somewhat mitigate this problem
by using short expiration for Access Tokens.

For more details, see the [Implicit
Flow](https://openid.net/specs/openid-connect-core-1_0.html#ImplicitFlowAuth)
in the OpenID Connect specification.

Per current [Best Current Practice for OAuth 2.0 Security (RFC
9700)](https://datatracker.ietf.org/doc/html/rfc9700#name-implicit-grant),
this flow SHOULD NOT be used. This flow is removed from the future
[OAuth 2.1
specification](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13).

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S022 — keycloak/KC-US-007

**Story**: JWT Token Signing

**Story text**: As a security architect, I want Keycloak to sign JWT tokens with strong cryptographic algorithms so that tokens cannot be forged or tampered with

**Acceptance criteria**:
  - Tokens are signed using RS256 or stronger algorithm
  - Public keys are available via JWKS endpoint
  - Key rotation is supported without service interruption
  - Signature validation is required for token acceptance

**Retrieved chunk source**:
`jwt-authorization-grant.md > General`

**Retrieved chunk content**:
```
### [5] jwt-authorization-grant.md > General (Relevance: 0.56)
- JSON Web Token (JWT) Profile for OAuth 2.0 Client Authentication and Authorization Grants ([RFC 7523](https://datatracker.ietf.org/doc/html/rfc7523)). Specifics for JWT assertions.

In short, the JWT Authorization is an OAuth extension grant as defined
by OAuth 2.0 [RFC
6749](https://datatracker.ietf.org/doc/html/rfc6749#section-4.5) that is
sent to the token endpoint. The **grant_type** request parameter must be
`urn:ietf:params:oauth:grant-type:jwt-bearer`. The **assertion** must be
a single JWT with some claims that will be validated by the server. The
parameter **scope** is optional and maintains the same meaning described
by Oauth 2.0 and managed by {project_name} for other grants. If the
assertion token is valid for authorization, an access token is returned
to the client without any interaction to the authorization endpoint.

The trust relationship in {project_name} is defined by an [Identity
Provider]({adminguide_link}#_identity_broker). Currently two Identity
Provider types can manage JWT authorization grants:

1.  OpenID Connect v1.0 / Keycloak OpenID Connect

2.  JWT Authorization Grant

**OpenID Connect v1.0** (also the **Keycloak OpenID Connect** which is
just an extension of the previous type) can be used to define a trust
relationship with an external OpenID Provider or OP (an OAuth 2.0
Authentication Servers implementing the OpenID Connect specification).
This is the common choice. The received assertion will be processed
using the provider configuration to validate the JWT token in terms of
claims and signature.

The **JWT Authorization Grant** is a new type of Identity Provider in
{project_name} to represent a generic trust relationship. Similar to the
previous type, its configuration allows to validate the assertion and
obtain an access token using the JWT authorization grant.

{project_name} requires the `sub` claim in the assertion to be the user
identifier in the external provider

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S023 — keycloak/KC-US-005

**Story**: User Federation with LDAP

**Story text**: As an enterprise administrator, I want to federate user authentication with our existing LDAP directory so that employees can use their corporate credentials

**Acceptance criteria**:
  - LDAP connection can be configured with bind credentials
  - Users can authenticate using LDAP credentials
  - User attributes are synchronized from LDAP
  - Group memberships are mapped to Keycloak roles

**Retrieved chunk source**:
`oidc-layers.md > Financial-grade API (FAPI) Support > FAPI client profiles`

**Retrieved chunk content**:
```
### [5] oidc-layers.md > Financial-grade API (FAPI) Support > FAPI client profiles (Relevance: 0.44)
Section: Financial-grade API (FAPI) Support > FAPI client profiles

To make sure that your clients are FAPI compliant, you can configure
Client Policies in your realm as described in the
[{adminguide_name}]({adminguide_link}#_client_policies) and link them to
the global client profiles for FAPI support, which are automatically
available in each realm. You can use either `fapi-1-baseline` or
`fapi-1-advanced` profile based on which FAPI profile you need your
clients to conform with. You can use also profiles
`fapi-2-security-profile`, `fapi-2-dpop-security-profile`,
`fapi-2-message-signing` and `fapi-2-dpop-message-signing` for the
compliance with FAPI 2.0 specifications.

In case you want to use [Pushed Authorization Request
(PAR)]({adminguide_link}#_oidc_clients), it is recommended that your
client use both the `fapi-1-baseline` profile and `fapi-1-advanced` for
PAR requests. Specifically, the `fapi-1-baseline` profile contains
`pkce-enforcer` executor, which makes sure that client use PKCE with
secured S256 algorithm. This is not required for FAPI Advanced clients
unless they use PAR requests.

In case you want to use <a href="#_backchannel_authentication_endpoint"
class="cross-reference">CIBA</a> in a FAPI compliant way, make sure that
your clients use both `fapi-1-advanced` and `fapi-ciba` client profiles.
There is a need to use the `fapi-1-advanced` profile, or other client
profile containing the requested executors, as the `fapi-ciba` profile
contains just CIBA-specific executors. When enforcing the requirements
of the FAPI CIBA specification, there is a need for more requirements,
such as enforcement of confidential clients or certificate-bound access
tokens.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S024 — mastodon/MT-US-003

**Story**: Post Visibility Control

**Story text**: As a user, I want to control who can see my posts so I can manage my privacy

**Acceptance criteria**:
  - Public posts appear on timelines
  - Followers-only posts restricted
  - Direct messages reach only recipients
  - Unlisted posts hidden from timelines

**Retrieved chunk source**:
`status.rb:408 > status.rb:408`

**Retrieved chunk content**:
```
### [6] status.rb:408 > status.rb:408 (Relevance: 0.55)
### code search hit (pattern: 'visibility')
File: app/models/status.rb:408
```
return unless direct_visibility?
```


---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S025 — bitwarden/BW-US-007

**Story**: Organization Vault Sharing

**Story text**: As an organization admin, I want to share credentials with team members through collections so we can manage shared access securely

**Acceptance criteria**:
  - Items can be shared to collections
  - Collection access is role-based
  - Encryption keys are shared securely
  - Access can be revoked

**Retrieved chunk source**:
`0022-server-authorization.md > 0022 - Authorization > Context and problem statement > Current patterns`

**Retrieved chunk content**:
```
### [4] 0022-server-authorization.md > 0022 - Authorization > Context and problem statement > Current patterns (Relevance: 0.45)
Section: 0022 - Authorization > Context and problem statement > Current patterns

To date, we have broadly used the following authorization patterns:

- In the individual user context, we match the user ID in the JWT to the resource being accessed.
  This is usually a one-to-one match (e.g. the JWT user ID should match the cipher user ID) without
  additional requirements.
- In organizational contexts, we use role-based authorization, where the user is assigned a role in
  an organization and permission is granted or denied based on that role. This closely tracks
  [how access control is presented to the user](https://bitwarden.com/help/user-types-access-control/).
  However, the expansion of collections has complicated this simple model, and it now looks more
  like
  [attribute-based access control](https://en.wikipedia.org/wiki/Attribute-based_access_control).

This logic is spread throughout controllers in the API layer, JWT claims accessed via
`CurrentContext`, the core service layer, and database queries.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S026 — gitlab/GL-US-008

**Story**: Group Permission Management

**Story text**: As a group owner, I want to manage member permissions so I can control access across projects

**Acceptance criteria**:
  - Members can be added with roles
  - Role inheritance works across subgroups
  - Permissions can be restricted
  - Access can be time-limited

**Retrieved chunk source**:
`project.rb:2552 > project.rb:2552`

**Retrieved chunk content**:
```
### [5] project.rb:2552 > project.rb:2552 (Relevance: 0.55)
### code search hit (pattern: 'access_control')
File: app/models/project.rb:2552
```
pages_access_control_forced_by_ancestor? ||
```

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S027 — mattermost/MM-US-006

**Story**: Team and Channel Creation

**Story text**: As a team admin, I want to create teams and channels so I can organize my workspace

**Acceptance criteria**:
  - Teams can be created
  - Channels can be added to teams
  - Permissions can be configured
  - Members can be invited

**Retrieved chunk source**:
`notifications.ts:60 > notifications.ts:60`

**Retrieved chunk content**:
```
### [3] onboard_advanced_permissions.md > Advanced permissions > Recipes > Public and private channel management (Relevance: 0.64)
Section: Advanced permissions > Recipes > Public and private channel management

#### Restrict who can rename & edit channels

Example: As the default for the entire system, restrict renaming channels and editing headers and purposes to admins only.

1.  Go to **System Console \> User Management \> Permissions**.
2.  Select **Edit Scheme**.
3.  In the **All Members** panel, uncheck the box for **Manage Public Channels \> Manage Channel Settings**.

The **Manage Channel Settings** option is now only available to channel admins, team admins, and system admins.

#### Restrict who can create channels in specific teams

Example: In Team C, restrict public channel creation to admins. As the default for all other teams, allow everyone to create public channels.

1.  Go to **System Console \> User Management \> Permissions**.
2.  Select **Edit Scheme**.
3.  In the **All Members** panel, in the **Manage Public Channels** menu, check the box for **Create Channels**. This sets the system default to allow creation of public channels on all teams.
4.  Select **Save**.
5.  Select the arrow to return to the **Permission Schemes** interface.
6.  Select **New Team Override Scheme**.

> 1.  Name and describe the scheme. For example, `Contractor Scheme` with description `Restrict public channel creation to Admins only`.
> 2.  Select **Add Teams** to add Team B to the **Select teams to override permissions** list, locate Team B, then select **Add**.
> 3.  In the **All Members** panel, in the **Manage Public Channels** section, uncheck the box for **Create Channels**.
> 4.  In the **Team Administrators** panel, in the **Manage Public Channels** section, check the box for **Create Channels**.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S028 — bitwarden/BW-US-006

**Story**: File Attachment Upload

**Story text**: As a user, I want to attach files to my vault items so I can store sensitive documents alongside credentials

**Acceptance criteria**:
  - Files are encrypted before upload
  - Each file has unique encryption key
  - File size limits are enforced
  - Files can be downloaded and decrypted

**Retrieved chunk source**:
`crypto-guide.md > How to use cryptography in Bitwarden > Terminology > Symmetric encryption`

**Retrieved chunk content**:
```
### [4] crypto-guide.md > How to use cryptography in Bitwarden > Terminology > Symmetric encryption (Relevance: 0.37)
Section: How to use cryptography in Bitwarden > Terminology > Symmetric encryption

Symmetric encryption (also secret key cryptography) describes cryptographic algorithms that have a
single key that is used both for encryption and decryption. As a rule of thumb, non-cryptography
developers should build most functionality with symmetric encryption. Symmetric encryption is very
fast and has small keys. To build complex sharing mechanisms, the data should be encrypted with a
symmetric key, and only the symmetric key is shared. This sharing can then be done via asymmetric
cryptography, not described here.

---

```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S029 — bitwarden/BW-US-007

**Story**: Organization Vault Sharing

**Story text**: As an organization admin, I want to share credentials with team members through collections so we can manage shared access securely

**Acceptance criteria**:
  - Items can be shared to collections
  - Collection access is role-based
  - Encryption keys are shared securely
  - Access can be revoked

**Retrieved chunk source**:
`0022-server-authorization.md > 0022 - Authorization > Decision outcome`

**Retrieved chunk content**:
```
### [5] 0022-server-authorization.md > 0022 - Authorization > Decision outcome (Relevance: 0.44)
Section: 0022 - Authorization > Decision outcome

Chosen option: **ASP.NET Core resource-based authorization**.

### Positive consequences

- Lowest cost/effort implementation.
- Fits team structure.
- Existing code can be refactored incrementally.

### Negative consequences

- Some duplication of read logic between discrete reads and bulk reads.

### Plan

- See the [Authorization deep dive](../deep-dives/authorization.md) for implementation details and
  examples.
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---

## S030 — bitwarden/BW-US-003

**Story**: Biometric Unlock Setup

**Story text**: As a user, I want to enable biometric unlock (fingerprint/face) so I can access my vault more conveniently on my device

**Acceptance criteria**:
  - User has previously authenticated with master password
  - Biometric key is securely stored on device
  - Biometric unlock works without network access
  - User can disable biometric unlock

**Retrieved chunk source**:
`index.md > Authentication > Identity API grants access > Generating a response`

**Retrieved chunk content**:
```
### [5] index.md > Authentication > Identity API grants access > Generating a response (Relevance: 0.62)
Section: Authentication > Identity API grants access > Generating a response

Once the request has been validated through the appropriate logic for the given grant type, the
Identity API will generate a response. At Bitwarden, this response contains an `access_token` with
claims and also a custom JSON object that contains essential information to start the vault
decryption process.

:::info Known Device

It is at this point, when the token request has been validated, that the requesting device is stored
and associated with the user as a “known device”.

:::

#### Authentication information

For a validated password and SSO token requests (grant types of `password` and
`authorization_code`), the response will contain an `access_token`, which is a JWT with the
following claims:

- `device` - the device identifier.
- `premium` - a boolean indicating whether they have access to premium features.
- `email` - the user’s email address.
- `email_verified` - whether the user has verified their email address.
- `sstamp` - the user’s security stamp (a unique GUID for the user stored in the `User` table and
  regenerated when a new password or key is generated). A changed `sstamp` indicates that the user
  should re-authenticate.
- `name` - The user’s name.
- **Organization claims**
  - `orgowner` - The `ID` of any org(s) for which the user is the owner.
  - `orgadmin` - The `ID` of any org(s) for which the user is the administrator.
  - `orgmanager` - The `ID` of the org(s) for which the user is the manager.
  - `orguser` - The `ID` of the org(s) for which the user is a user.
  - `orgcustom` - The `ID` of the org(s) for which the user has custom permissions. In this case,
    the user will also have custom claims added as defined for that org.
- **Provider claims**
  - `providerprovideradmin` - The `ID` of the provider(s) for which the user is an admin.
  - `providerservice

... [truncated] ...
```

**Human verdict**: `____________`  *(relevant / partial / irrelevant)*

---
