"""Tests of the secret rules (remove_secrets). No model, no network.

Each secret form lists the text and the part that must not survive. Each
harmless line must come out equal: the model needs these words.
"""

# ruff: noqa: E501
from __future__ import annotations

import random
import string
import time

import pytest
from secreqgen.context.secrets import (
    REMOVED,
    looks_random,
    remove_secrets,
    remove_secrets_in_names,
)

SECRET_FORMS = [
    ("DATABASE_URL=postgresql://app:Tr0ub4dor@db.internal:5432/app", "Tr0ub4dor"),
    ("mysql://root:p%40ss!w0rd#1@10.0.0.5/db", "w0rd"),
    (
        "mongodb+srv://admin:Hunter2Hunter@cluster0.abc.mongodb.net/test",
        "Hunter2Hunter",
    ),
    ("REDIS=redis://:s3cr3tpw@cache:6379/0", "s3cr3tpw"),
    ("amqp://guest:guestpw99@rabbit:5672//", "guestpw99"),
    ("jdbc:postgresql://db:5432/app?user=app&password=Gl0bal7pw", "Gl0bal7pw"),
    (
        "jdbc:sqlserver://h:1433;databaseName=x;user=sa;password=Sup3rS3cret;",
        "Sup3rS3cret",
    ),
    (
        "Server=tcp:db.example.com,1433;Initial Catalog=App;User ID=sa;Password=Xy7!pLm2Q;Encrypt=True;",
        "Xy7!pLm2Q",
    ),
    ("Data Source=db;User Id=app;Pwd=ab12CD;", "ab12CD"),
    (
        '{"ConnectionStrings":{"Default":"Server=db;Database=app;User Id=sa;Password=Abcd1234x;"}}',
        "Abcd1234x",
    ),
    ('{"db": {"pass": "SomePw123"}}', "SomePw123"),
    ('{"pw": "SomePw124"}', "SomePw124"),
    ('{"clientSecret": "abcDEF123xyz"}', "abcDEF123xyz"),
    ("<password>Xml5ecretVal</password>", "Xml5ecretVal"),
    ('<add key="Password" value="AttrPw99x"/>', "AttrPw99x"),
    ('<property name="hibernate.connection.password">hibPw77</property>', "hibPw77"),
    ("spring.datasource.password=springPw55", "springPw55"),
    ("password: |\n  multi\n  linePw88\n", "linePw88"),
    ("secret: >\n  foldedSecret99\n", "foldedSecret99"),
    ("password:\n  blockPw123\n", "blockPw123"),
    ("stringData:\n  username: admin\n  password: k8sPw1234\n", "k8sPw1234"),
    ("data:\n  password: cGFzc3dvcmQxMjM=\n", "cGFzc3dvcmQxMjM"),
    ('- name: DB_PASSWORD\n  value: "envPw4321"\n', "envPw4321"),
    ("- name: API_TOKEN\n  value: tok-abc-123\n", "tok-abc-123"),
    ("export DB_PASSWORD=ShellPw12", "ShellPw12"),
    ('export API_TOKEN="shellTok34"', "shellTok34"),
    ("MYSQL_PWD=mysqlPw56 mysql -u root", "mysqlPw56"),
    ("mysql -u root -pMyS3cretPw -h db", "MyS3cretPw"),
    ("mysql --password=MyS3cretPw2 -h db", "MyS3cretPw2"),
    ("PGPASSWORD=pgPw78 psql -h db", "pgPw78"),
    ("$db_password = 'phpPw90xx';", "phpPw90xx"),
    ("define('DB_PASSWORD', 'definePw12');", "definePw12"),
    ("'password' => 'arrPw3456',", "arrPw3456"),
    ("config = {'password': 'pyPw78xx'}", "pyPw78xx"),
    ("connect(host='h', user='u', passwd='kwPw1212')", "kwPw1212"),
    ("| DB_PASSWORD | `tablePw99` |", "tablePw99"),
    ("Log in as admin with the password hunter2hunter.", "hunter2hunter"),
    ("The default password is admin123 and the token is abcd-1234.", "admin123"),
    ("curl -u admin:CurlPw1234 https://api.example.com/v1", "CurlPw1234"),
    ("curl --user admin:CurlPw1235 https://api.example.com", "CurlPw1235"),
    ('curl -H "X-Api-Key: shortkey12" https://x', "shortkey12"),
    ('curl -H "Authorization: Token abcdef123456" https://x', "abcdef123456"),
    ('curl -H "Authorization: sk_hdr_value_12345" https://x', "sk_hdr_value_12345"),
    ("Authorization: Basic YWRtaW46c2VjcmV0cGFzcw==", "YWRtaW46c2VjcmV0cGFzcw"),
    ("https://user:p%40ss%2Fw0rd@host.example.com/x", "w0rd"),
    ("https://user:pa/ss@host/x", "pa/ss"),
    ("https://user:pa@ss@host/x", "ss@host"),
    ("https://user:pa#ss1@host/x", "pa#ss1"),
    ("// apiKey: commentKey55", "commentKey55"),
    (
        "SGVsbG8gd29ybGQgdGhpcyBpcyBhIHNlY3JldCB2YWx1ZSE=\nVGhpcyBpcyBhbm90aGVyIGxpbmUgb2YgYmFzZTY0IHRleHQ=",
        "VGhpcyBpcyBhbm90aGVy",
    ),
    ("key = 'a1b2c3d4e5f60718'", "a1b2c3d4e5f60718"),
    ("sk_live_51H8abcdEFGHijklMNOPqrstu", "51H8abcdEFGH"),
    ("whsec_abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnop"),
    ("ACa1b2c3d4e5f60718293a4b5c6d7e8f90", "a1b2c3d4e5f60718"),
    ("TWILIO_AUTH_TOKEN=0123456789abcdef0123456789abcdef", "0123456789abcdef"),
    ("SKa1b2c3d4e5f60718293a4b5c6d7e8f90", "a1b2c3d4e5f60718"),
    (
        "SG.abcdefghijklmnopqrstuv.wxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abc",
        "abcdefghijklmnopqrstuv",
    ),
    (
        "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=abc123xyz789abc123xyz789abc123xyz789abc123xyz789abc123xyz789ab/ab==;EndpointSuffix=core.windows.net",
        "abc123xyz789",
    ),
    ("?sv=2020-08-04&ss=b&sig=AbCdEf1234%2BxYz%2F5678", "AbCdEf1234"),
    ("AZURE_CLIENT_SECRET: abC8Q~xyzxyzxyzxyz.xyzxyzxyz", "abC8Q~xyz"),
    ("key=AIzaSyA-1234567890abcdefghijklmnopqrstuv", "AIzaSyA-1234567890"),
    ("GOCSPX-abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
    ("1//0gAbCdEfGhIjKlMnOpQrStUv-wXyZ", "AbCdEfGhIjKl"),
    ("https://hooks.slack.com/services/T0001/B0001/abcDEF123xyz", "abcDEF123xyz"),
    (
        "MTAwMDAwMDAwMDAwMDAwMDAw.GabcDE.abcdefghijklmnopqrstuvwxyz1",
        "abcdefghijklmnopqrstuvwxyz1",
    ),
    ("HEROKU_API_KEY=01234567-89ab-cdef-0123-456789abcdef", "01234567"),
    (
        "dop_v1_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "aaaaaaaa",
    ),
    (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\nxyz\n-----END OPENSSH PRIVATE KEY-----",
        "b3BlbnNzaC1r",
    ),
    (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF\nabc\n-----END PGP PRIVATE KEY BLOCK-----",
        "lQOYBF",
    ),
    (
        "----BEGIN PRIVATE KEY----\nMIIEvQIBADAN\n----END PRIVATE KEY----",
        "MIIEvQIBADAN",
    ),
    (
        '"key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADANBgkq\\n-----END PRIVATE KEY-----\\n"',
        "MIIEvQIBADAN",
    ),
    (
        "-----BEGIN PRIVATE KEY----- MIIEvQIBADANBgkqhkiG9w0BAQEFAASC -----END PRIVATE KEY-----",
        "MIIEvQIBADAN",
    ),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n", "MIIEowIBAAKC"),
    ("MIIEowIBAAKCAQEAxyz12\nabcdef\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKC"),
    ("ARG DB_PASSWORD=dockerArgPw1", "dockerArgPw1"),
    ("ENV DB_PASSWORD dockerEnvPw2", "dockerEnvPw2"),
    ("ENV API_TOKEN abc123def", "abc123def"),
    ("ENV A=1 \\\n    DB_PASS=multiPw3 \\\n    B=2", "multiPw3"),
    ("RUN echo 'root:rootPw4321' | chpasswd", "rootPw4321"),
    ("RUN useradd -p $(openssl passwd -1 ShadowPw1) bob", "ShadowPw1"),
    ("RUN htpasswd -b /etc/.htpasswd admin htPw1234", "htPw1234"),
    ("RUN curl -u user:dockerCurl9 https://x", "dockerCurl9"),
    ("command: --requirepass redisCmdPw1", "redisCmdPw1"),
    ('command: ["mysqld", "--password=cmdPw11x"]', "cmdPw11x"),
    ("environment:\n  - MYSQL_ROOT_PASSWORD=composePw12\n", "composePw12"),
    ("  - POSTGRES_PASSWORD=composePw13", "composePw13"),
    ("  - 'KEYCLOAK_ADMIN_PASSWORD: kcPw99'", "kcPw99"),
    ("Passwort: Geheim123", "Geheim123"),
    ("passwort = 'Gehe1mPw'", "Gehe1mPw"),
    ("Kennwort: Kenn1234x", "Kenn1234x"),
    ("Schlüssel: AbCdEf12", "AbCdEf12"),
    ("mot de passe: MotDePasse1", "MotDePasse1"),
    ("contraseña: Contra1234", "Contra1234"),
    ("κωδικός: Κωδ1234pw", "Κωδ1234pw"),
    ("Ο κωδικός πρόσβασης είναι MyPassw0rd", "MyPassw0rd"),
    ("secret_access_key: abcd1234efgh", "abcd1234efgh"),
    (
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "wJalrXUtnFEMI",
    ),
    ('AWS_SECRET_ACCESS_KEY: "wJalrXUtnFEMI/K7MDENG/bPxRfiCY"', "wJalrXUtnFEMI"),
    ("hmac: 9f86d081884c7d659a2feaa0c55ad015", "9f86d081884c7d65"),
    ("PASSWORD_SALT=somesaltvalue1", "somesaltvalue1"),
    ("api: 3f786850e387550fdab836ed7e6dc881de23001b", "3f786850e387550f"),
    ("https://x.example.com/cb?token=abc123&x=1", "abc123"),
    ("https://x.example.com/cb?apikey=abc123def&x=1", "abc123def"),
    ("https://x.example.com/cb?pw=abc123def&x=1", "abc123def"),
    ("Driver={SQL Server};Server=s;UID=sa;PWD=odbcPw12;", "odbcPw12"),
    ("ldap_bind_password: ldapPw12", "ldapPw12"),
    ("smtp://user:smtpPw12@mail:587", "smtpPw12"),
    ("Authorization: Bearer abc123", "abc123"),
    ("Cookie: session=abcDEF123456", "abcDEF123456"),
    ("app.use(session({ secret: 'keyboard cat' }))", "keyboard cat"),
    ('"JWT_SECRET":"jwtSec123"', "jwtSec123"),
    ("token=abc=def", "def"),
    ('secret: "a:b:c"', "a:b:c"),
    ("```\nSECRET=abc12345\nDEBUG=true\n```", "abc12345"),
    ("sshpass -p 'sshPw1234' ssh host", "sshPw1234"),
    ("openssl rsa -passin pass:opensslPw1 -in k.pem", "opensslPw1"),
    ("keytool -storepass ktPw1234 -list", "ktPw1234"),
    ("storePassword 'gradlePw1'", "gradlePw1"),
    ('keyPassword "gradlePw2"', "gradlePw2"),
    (
        "<server><id>x</id><username>u</username><password>mvnPw12</password></server>",
        "mvnPw12",
    ),
    ('<add key="ClearTextPassword" value="nugetPw12" />', "nugetPw12"),
    ('"Jwt": {"Key": "JwtSigningKey12345"}', "JwtSigningKey12345"),
    ('"Authentication": {"Google": {"ClientSecret": "csec1234"}}', "csec1234"),
    ("db_pw: shortPw12", "shortPw12"),
    ("auth: admin:adminPw12", "adminPw12"),
    ("admin:$apr1$abc$defghijklmnopq", "defghijklmnopq"),
    (
        "hash: $2y$10$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ01234",
        "abcdefghijklmnopqrstuu",
    ),
    ('"password": "ab\\"LeakTail99"', "LeakTail99"),
    ("password = 'ab\\'LeakTail98'", "LeakTail98"),
    ('os.environ["DB_PASSWORD"] = "IdxPw12345"', "IdxPw12345"),
    ("config['password'] = 'IdxPw12346'", "IdxPw12346"),
    ("$config['db_password'] = 'IdxPw12347';", "IdxPw12347"),
    ("settings['API_KEY'] = 'IdxKey12345'", "IdxKey12345"),
    ("ENV['SECRET_KEY'] = 'IdxPw12348'", "IdxPw12348"),
    (":password => 'rocketPw1'", "rocketPw1"),
    ('Password: "goStructPw1",', "goStructPw1"),
    ('ds.setPassword("javaSetPw1");', "javaSetPw1"),
    ('new UsernamePasswordCredentials("admin", "javaCredPw1")', "javaCredPw1"),
    ("auth: { user: 'a', pass: 'jsPassPw1' }", "jsPassPw1"),
    ("pass: 'nodemailerPw1'", "nodemailerPw1"),
    ("--auth user:cliAuthPw1", "cliAuthPw1"),
    ("redis-cli -a redisCliPw1", "redisCliPw1"),
    ("docker login -u me -p dockerLoginPw1 reg", "dockerLoginPw1"),
    ("echo dockerStdinPw1 | docker login --password-stdin", "dockerStdinPw1"),
    ("git clone https://ghp_notaprefix:x-oauth-basic@github.com/a/b", "ghp_notaprefix"),
    (
        "git clone https://oauth2:glpat_underscorepw1@gitlab.com/a/b",
        "glpat_underscorepw1",
    ),
    ("//registry.npmjs.org/:_authToken=npmAuthTok1", "npmAuthTok1"),
    ("--extra-index-url https://user:pipIdxPw1@pypi.example.com/simple", "pipIdxPw1"),
    ("run --password hunter2x --other", "hunter2x"),
    ("redis-server --requirepass R3disPw", "R3disPw"),
    ("app --token abcDEF123 --x", "abcDEF123"),
    ("app --api-key 'k e y 1'", "k e y 1"),
    ("curl -u admin:CurlPw9 https://x", "CurlPw9"),
    ("curl --user admin:CurlPw8 https://x", "CurlPw8"),
    ("sshpass -p sshPw77 ssh host", "sshPw77"),
    ("mysqldump -u root -pDumpPw1 db", "DumpPw1"),
    ("mariadb -u root -pMariaPw1 db", "MariaPw1"),
    ("docker login reg.io -u me -p DockerPw1", "DockerPw1"),
    ("redis-cli -h cache -a RedisPw1 ping", "RedisPw1"),
    ("openssl enc -passin pass:InPw123 -in a", "InPw123"),
    ("openssl enc -passout pass:OutPw123 -out a", "OutPw123"),
    ("RUN echo 'user:ChPw123' | chpasswd", "ChPw123"),
    ("htpasswd -bc /etc/pw admin HtPw1", "HtPw1"),
    ("htpasswd -cb /etc/pw admin HtPw2", "HtPw2"),
    ("htpasswd -bB /etc/pw admin HtPw3", "HtPw3"),
    ('os.environ["DB_PASSWORD"] = "IdxPw1"', "IdxPw1"),
    ("config['password'] = 'IdxPw2'", "IdxPw2"),
    ("$config['db_password'] = 'IdxPw3';", "IdxPw3"),
    ("ENV['SECRET_KEY'] = 'IdxPw4'", "IdxPw4"),
    ("'password' => 'ArrPw1'", "ArrPw1"),
    ('ds.setPassword("SetPw1");', "SetPw1"),
    ("set_api_key('ApiKeyVal1')", "ApiKeyVal1"),
    ("define('DB_PASSWORD', 'DefPw1')", "DefPw1"),
    ('props.put("password", "PutPw1");', "PutPw1"),
    ("pass: 'ShortPw1'", "ShortPw1"),
    ('"pw": "ShortPw2"', "ShortPw2"),
    ('{"Jwt": {"Key": "JwtKeyVal1"}}', "JwtKeyVal1"),
    ("<password>XmlPw1</password>", "XmlPw1"),
    ("<db.password>XmlPw2</db.password>", "XmlPw2"),
    ('<add key="Password" value="XmlPw3"/>', "XmlPw3"),
    ('<property name="hibernate.connection.password">XmlPw4</property>', "XmlPw4"),
    ("- name: DB_PASSWORD\n  value: K8sPw1\n", "K8sPw1"),
    ("password: |\n  line1Pw\n  line2Pw\n", "line2Pw"),
    ("password: >\n  FoldedPw1\n", "FoldedPw1"),
    ("password:\n  NextLinePw1\n", "NextLinePw1"),
    ("The password is Hunter2x and nothing else.", "Hunter2x"),
    ("The token is `abc-def` for tests.", "abc-def"),
    ("token is 'quotedTok'", "quotedTok"),
    ("Use the password hunter2hunter here.", "hunter2hunter"),
    ("Passwort: GeheimPw1", "GeheimPw1"),
    ("Kennwort: KennPw1", "KennPw1"),
    ("contraseña: ClavePw1", "ClavePw1"),
    ("mot de passe: MotPw1", "MotPw1"),
    ("κωδικός: ΚωδΠw1", "ΚωδΠw1"),
    ("κωδικος: ΚωδΠw2", "ΚωδΠw2"),
    ("συνθηματικό: ΣυνΠw1", "ΣυνΠw1"),
    ("| DB_PASSWORD | tablePw99 |", "tablePw99"),
    ("| api_key | `tableKey1` | secret |", "tableKey1"),
    ("Cookie: session=CookieVal1; theme=dark", "CookieVal1"),
    ("Set-Cookie: sid=SetCookieVal1; Path=/; HttpOnly", "SetCookieVal1"),
    ('Authorization: Digest username="u", response="DigestResp1"', "DigestResp1"),
    ("Authorization: Token TokenVal123", "TokenVal123"),
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ('"password": "ab\\"EscTail1"', "EscTail1"),
    ("https://user:pa/ss1@host/x", "pa/ss1"),
    ("https://user:pa@ss2@host/x", "ss2@host"),
    ("https://ghp_x:x-oauth-basic@github.com/a/b", "x-oauth-basic"),
    ("https://TOKEN123@github.com/a/b", "TOKEN123"),
    ("https://x.example.com/cb?sig=SigVal1&b=2", "SigVal1"),
    ("https://x.example.com/cb?a=1&signature=SigVal2", "SigVal2"),
    ("https://x.example.com/cb?a=1&access_token=AccTok1", "AccTok1"),
    ("https://x.example.com/cb?api_key=QKey123", "QKey123"),
    ("whsec_aaaaaaaaaaaaaaaaaaaaaaaa", "aaaaaaaa"),
    ("GOCSPX-bbbbbbbbbbbbbbbbbbbbbbbb", "bbbbbbbb"),
    ("dop_v1_cccccccccccccccccccccccccccccccc", "cccccccc"),
    ("SG.dddddddddddddddddddd.eeeeeeeeeeeeeeeeeeee", "dddddddd"),
    ("hf_ffffffffffffffffffffffffffffff", "ffffffff"),
    ("shpat_a1a1a1a1a1a1a1a1a1a1a1a1", "a1a1a1a1"),
    ("xapp-1-A0123-4567-gggggggggggg", "gggggggg"),
    ("pypi-AgEIcHlwaS5vcmcCJGhoaGhoaGhoaGhoaGhoaGhoaGho", "aGhoaGho"),
    (
        "-----BEGIN PRIVATE KEY-----\nAAAABBBB1234\n-----END PRIVATE KEY-----",
        "AAAABBBB1234",
    ),
    (
        "----BEGIN PRIVATE KEY----\nAAAABBBB1235\n----END PRIVATE KEY----",
        "AAAABBBB1235",
    ),
    ("storePassword 'GradlePw1'", "GradlePw1"),
    ("auth: admin:AuthPw12", "AuthPw12"),
    ("KEY = 'a1b2c3d4e5f60718'", "a1b2c3d4e5f60718"),
]

# Forms that still pass. Each one has a reason. strict=True makes the test
# fail when a later change fixes the form, so the list stays true.
OPEN_FORMS = [
    pytest.param(
        "data:\n  db: c2VjcmV0cGFzc3dvcmQ=\n",
        "c2VjcmV0cGFzc3dvcmQ",
        marks=pytest.mark.xfail(
            reason="The key name db has no secret word. Only the key words and the form decide.",
            strict=True,
        ),
    ),
    pytest.param(
        "login('admin', 'Pa55w0rdHere')",
        "Pa55w0rdHere",
        marks=pytest.mark.xfail(
            reason="The call name login has no secret word.", strict=True
        ),
    ),
    pytest.param(
        "API_KEY=\\\n  splitKey12345",
        "splitKey12345",
        marks=pytest.mark.xfail(
            reason="The value is on the next line after a backslash. No rule reads it.",
            strict=True,
        ),
    ),
    pytest.param(
        "# the key is ab12cd34ef56ab12 (do not share)",
        "ab12cd34ef56ab12",
        marks=pytest.mark.xfail(
            reason="A key in a free sentence with no separator and no quote.",
            strict=True,
        ),
    ),
    pytest.param(
        "KEY_ID a1b2c3d4e5f607182930abcd",
        "a1b2c3d4e5f607182930abcd",
        marks=pytest.mark.xfail(
            reason="A bare hex word of 24 characters. Too short for the random rule.",
            strict=True,
        ),
    ),
    pytest.param(
        "id: 5f1d7f3e2b8a4c6d9e0a1b2c",
        "5f1d7f3e2b8a4c6d9e0a1b2c",
        marks=pytest.mark.xfail(
            reason="A bare hex word of 24 characters. Too short for the random rule.",
            strict=True,
        ),
    ),
    pytest.param(
        "pass: pass-with-dashes-1",
        "pass-with-dashes-1",
        marks=pytest.mark.xfail(
            reason="The short key pass counts only when the value is in quotes.",
            strict=True,
        ),
    ),
]

HARMLESS_LINES = [
    "Uses basic authentication over TLS.",
    "Provides basic functionality for users.",
    "Basic configuration is in config.yml",
    "Bearer authentication is supported.",
    "Supports Basic Auth",
    "The basic installation needs Docker.",
    "- Passwords: bcrypt with a cost of 12",
    "Secrets: HashiCorp Vault",
    "Tokens: JWT signed with RS256",
    "Credentials: stored in Keycloak",
    "Password hashing: Argon2id",
    "- Authentication: OAuth2 and OpenID Connect",
    '"jsonwebtoken": "^9.0.5",',
    '"@types/jsonwebtoken": "^9.0.5",',
    "tokenizers==0.15.0",
    'tokenizers = "^0.15"',
    'jsonwebtoken = "9"',
    'redis-token-store = "1.2"',
    '"csrf-token": "^1.0.0"',
    'passwordless = "^2.0"',
    "gem 'devise-secret_key', '~> 1.0'",
    "secretstorage==3.3.3",
    "spring.security.oauth2.client.registration.google.client-secret=${GOOGLE_SECRET}",
    "implementation 'io.jsonwebtoken:jjwt-api:0.11.5'",
    "github.com/golang-jwt/jwt/v5 v5.2.0",
    "github.com/hashicorp/vault/api v1.10.0",
    "Keycloak: the identity provider",
    "Vault: stores secrets",
    "Redis: session store",
    "Secret management: AWS Secrets Manager",
    "Session token: stored in Redis",
    "OAuth token: kept in PostgreSQL",
    "Auth key: RSA",
    "class TokenStore : RedisCache {}",
    "passwordEncoder = new BCryptPasswordEncoder()",
    "const apiKey = process.env.API_KEY;",
    "token = jwt.sign(payload, secret)",
    "private final PasswordEncoder passwordEncoder = new Argon2PasswordEncoder();",
    "public class PasswordResetEmail : BaseMail<PasswordResetEmailView>",
    "enableSecretsManager: true",
    "`enableSecretsManager: true` and `overrides.useSecretsManager: false` are sent",
    "version: 1.2.3",
    "license_key_path: /etc/x",
    "master_key_id: abc",
    "This app uses Keycloak for single sign-on",
    "Authorization: Basic <token>",
    "Authorization: Bearer ${TOKEN}",
    "Authorization: RBAC with Keycloak",
    "- Authorization: OAuth2",
    "Cookie: HttpOnly flag is set",
    "Use `--token` to log in.",
    "docker run -u 1000:1000 img",
    "mysql -u root -p mydb",
    "--password-stdin",
    "The token is valid for 15 minutes.",
    "The secret is kept in Vault.",
    "Token length: 32",
    "options.TokenLength = 8;",
    'string token = await provider.GenerateTokenAsync("EmailToken", "email_verification");',
    "| Password | string | The password |",
    "| token | required |",
    "| setup-secrets | Executable | Runs the script |",
    "tokenable.UserId == currentUser.Id",
    "token_url: https://idp.example.com/token",
    "jwt_secret_file: /run/secrets/jwt",
    "password: ${DB_PASSWORD}",
    "POSTGRES_HOST_AUTH_METHOD=trust",
    "https://github.com/a/b/blob/main/docs/x.md",
    "http://host:8080/path?mail=a@b.com",
    "git@github.com:a/b.git",
    "ssh://git@github.com/a/b",
    "password:\n  secretKeyRef:\n    name: db",
    "- name: DB_PASSWORD\n  valueFrom:\n    secretKeyRef:\n      name: x",
    "public_key: abc",
    "<groupId>io.jsonwebtoken</groupId>",
    "<secret>true</secret>",
]

DEPENDENCY_LINES = [
    "tensorflow-io-gcs-filesystem",
    "sphinxcontrib-applehelp==1.0.4",
    "opentelemetry-semantic-conventions==0.43b0",
    "azure-mgmt-recoveryservicesbackup==9.0.0",
    "@aws-sdk/client-secrets-manager",
    "@aws-sdk/client-cognito-identity-provider",
    "org.springframework.cloud:spring-cloud-starter-netflix-eureka-client:4.1.0",
    "org.springframework.boot:spring-boot-starter-data-jpa:3.2.1",
    "com.google.cloud:spring-cloud-gcp-starter-secretmanager:5.0.0",
    "io.jsonwebtoken:jjwt-api:0.12.3",
    "io.jsonwebtoken:jjwt-impl:0.12.3",
    "com.auth0:java-jwt:4.4.0",
    "com.password4j:password4j:1.8.1",
    "org.apache.commons:commons-lang3:3.14.0",
    "github.com/aws/aws-sdk-go-v2/service/secretsmanager v1.25.0",
    "github.com/golang-jwt/jwt/v5 v5.2.0",
    "github.com/hashicorp/vault-client-go v0.4.3",
    "google.golang.org/api v0.150.0",
    "Microsoft.Extensions.Configuration.AzureKeyVault 3.1.24",
    "Azure.Security.KeyVault.Secrets 4.6.0",
    "Microsoft.AspNetCore.Authentication.JwtBearer 8.0.0",
    "Duende.IdentityServer.EntityFramework.Storage 6.3.8",
    "django-rest-framework-simplejwt==5.3.1",
    "python-jose[cryptography]==3.3.0",
    "djangorestframework-api-key==3.0.0",
    "opentelemetry-exporter-otlp-proto-http",
    "org.keycloak:keycloak-spring-boot-starter:22.0.5",
    "software.amazon.awssdk:secretsmanager:2.21.0",
    "com.github.tomakehurst:wiremock-jre8-standalone:3.0.1",
    "jsonwebtoken 9.0.0",
    "passport-jwt@4.0.1",
    "bcrypt@5.1.1",
    "@nestjs/passport@10.0.3",
    "@auth0/auth0-spa-js@2.1.3",
    "next-auth@4.24.5",
    "gem 'doorkeeper-openid_connect', '~> 1.8'",
    "gem 'devise-two-factor', '~> 5.0'",
    "gem 'omniauth-rails_csrf_protection'",
    "gem 'rack-attack'",
    "gem 'bcrypt_pbkdf'",
    "gem 'net-ssh-krb', '1.0'",
    "keycloak-connect@23.0.0",
    "aws-sdk-secretsmanager",
    "vault_tokens==2.0.1",
    "token-bucket==0.3.0",
    'api-key-auth = "0.1"',
    "pyjwt==2.8.0",
    "secrets-store-csi-driver",
]


@pytest.mark.parametrize(("text", "secret"), SECRET_FORMS + OPEN_FORMS)
def test_secret_form_is_removed(text, secret):
    out = remove_secrets(text)
    assert secret not in out
    assert REMOVED in out


@pytest.mark.parametrize("line", HARMLESS_LINES)
def test_harmless_line_stays(line):
    assert remove_secrets(line) == line


@pytest.mark.parametrize("line", DEPENDENCY_LINES)
def test_dependency_line_stays_whole(line):
    assert remove_secrets(line) == line


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DB_PASS=hunter2", "DB_PASS=<removed>"),
        ("password: 'a b c'", "password: <removed>"),
        ("ftp://user:pw99@host/x", "ftp://user:<removed>@host/x"),
        ("https://user:pw99@host/x", "https://<removed>@host/x"),
        ("postgres://shop:s3cr3t@db:5432/app", "postgres://shop:<removed>@db:5432/app"),
        ("https://TOKEN123@github.com/a/b", "https://<removed>@github.com/a/b"),
        ("curl -u admin:Pw123 https://x", "curl -u admin:<removed> https://x"),
        ("Authorization: Bearer abc123xyz", "Authorization: Bearer <removed>"),
        ("Cookie: sid=Abc123", "Cookie: <removed>"),
    ],
)
def test_the_name_and_the_scheme_stay(text, expected):
    assert remove_secrets(text) == expected


def test_a_port_is_no_password():
    url = "http://host:8080/path?mail=a@b.com"
    assert remove_secrets(url) == url


def test_the_scheme_word_stays_in_prose_with_a_digit_value():
    assert remove_secrets("Use Bearer abcdefg1 here") == "Use Bearer <removed> here"
    assert remove_secrets("Use Bearer authentication here") == (
        "Use Bearer authentication here"
    )


@pytest.mark.parametrize(
    "text",
    [form[0] for form in SECRET_FORMS] + HARMLESS_LINES + DEPENDENCY_LINES,
)
def test_second_run_changes_nothing(text):
    once = remove_secrets(text)
    assert remove_secrets(once) == once


def test_names_list_rule_keeps_only_fixed_start_keys():
    text = "password.txt\nghp_" + "a" * 36 + "\nsecret_notes.md"
    out = remove_secrets_in_names(text)
    assert "ghp_" not in out
    assert "password.txt" in out
    assert "secret_notes.md" in out


def test_names_made_of_words_are_not_random():
    names = [
        "2021-10-21_00_SetMaxAutoscaleSeatCount",
        "UniquePasswordsPer100FairOrStronger",
        "opentelemetry-semantic-conventions==0.43b0",
    ]
    assert not any(looks_random(n) for n in names)
    assert looks_random("123e4567-e89b-12d3-a456-426614174000")


def test_random_text_is_found():
    rng = random.Random(11)
    letters = string.ascii_letters + string.digits
    tokens = ["".join(rng.choice(letters) for _ in range(40)) for _ in range(200)]
    missed = [t for t in tokens if not looks_random(t)]
    assert len(missed) <= 2


def _seconds(text):
    start = time.time()
    remove_secrets(text)
    return time.time() - start


def test_one_megabyte_of_normal_lines_is_fast():
    block = (
        "# Shop\nThe service stores orders. Set the token in the header.\n"
        "password: ${DB_PASSWORD}\nname: web\nimage: nginx:1.25\n"
        "See https://example.com/docs/setup.md for the key steps.\n"
    )
    text = block * (1_000_000 // len(block))
    assert _seconds(text) < 3


@pytest.mark.parametrize(
    "unit",
    ["a_b.c-", "password ", "<a>", "-u x:", "key: "],
)
def test_one_long_line_is_fast(unit):
    text = unit * (200_000 // len(unit))
    assert _seconds(text) < 3


@pytest.mark.parametrize(
    "unit",
    [
        "https://u:",
        "name: ",
        "| password |",
        "Authorization: ",
        'password: "',
        "-----END X-----",
        "--token a ",
        "mysql -u ",
        "token is x1 ",
        '<a b="x" ',
    ],
)
def test_more_long_lines_are_fast(unit):
    text = unit * (200_000 // len(unit))
    assert _seconds(text) < 3


# Faults of the second attack (F1 to F11 of the step P9, part S).

FIX_SECRET_FORMS = [
    # F2: SQL "IDENTIFIED BY"
    ("CREATE USER app IDENTIFIED BY 'SqlUserPw1';", "SqlUserPw1"),
    ("GRANT ALL ON *.* TO 'u'@'%' IDENTIFIED BY 'GrantPw12';", "GrantPw12"),
    (
        "CREATE USER a IDENTIFIED WITH mysql_native_password BY 'NativePw1';",
        "NativePw1",
    ),
    # F3: "NAME value" forms
    ("SetEnv DB_PASSWORD ApacheEnvPw1", "ApacheEnvPw1"),
    ("SetEnv SECRET_TOKEN ab12cd34ef56", "ab12cd34ef56"),
    ("fastcgi_param DB_PASSWORD nginxPw12;", "nginxPw12"),
    # F4: a login pair in prose
    ("Log in with admin / ReadmeAdminPw1 after install.", "ReadmeAdminPw1"),
    ("Default credentials: admin / admin123", "admin123"),
    # F5: Windows
    ("cmdkey /add:srv /user:bob /pass:CmdKeyPw1", "CmdKeyPw1"),
    ("net use Z: \\\\srv\\share NetUsePw12 /user:dom\\bob", "NetUsePw12"),
    ("ConvertTo-SecureString 'PsPlainPw1' -AsPlainText -Force", "PsPlainPw1"),
    ("ConvertTo-SecureString -AsPlainText 'PsPlainPw2' -Force", "PsPlainPw2"),
    # F6: signing call, Terraform default, YAML list argument
    ("jwt.sign(payload, 'my-super-secret-jwt-1')", "my-super-secret-jwt-1"),
    ('variable "db_password" {\n  default = "TfDefaultPw1"\n}', "TfDefaultPw1"),
    ("args:\n  - --password\n  - YamlListPw1", "YamlListPw1"),
    # F1: a key name with a very long start must still be found
    ("x" * 300 + "_password=LongStartPw12", "LongStartPw12"),
    ('{"Jwt": {"Key": "JwtSigningKey12345"}}', "JwtSigningKey12345"),
]

FIX_HARMLESS_LINES = [
    # F7: the short key "key" with a plain word
    '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" />',
    '<add key="globalPackagesFolder" value="packages" />',
    "{ key: 'name', label: 'Name' },",
    '<li key="item">',
    "dict(key='value')",
    "tags: { key: 'Name', value: 'web' }",
    # F8: names of technologies
    "password_hasher: argon2",
    "password_encoder: bcrypt",
    "secret_manager: vault",
    "secrets_backend: aws",
    "token_store: redis",
    "token_storage: postgres",
    "CREDENTIALS_STORE=vault",
    "ENCRYPTION_KEY_SOURCE=vault",
    "PASSWORD_HASHERS = ['django.contrib.auth.hashers.Argon2PasswordHasher']",
    # F9: version ranges and sources
    '"jsonwebtoken": ">=8.5.1 <10",',
    '"jsonwebtoken": "git+https://github.com/auth0/node-jsonwebtoken.git",',
    '"jsonwebtoken": "workspace:*",',
    '"jsonwebtoken": "file:../x",',
    '"jsonwebtoken": "github:auth0/x",',
    '"jsonwebtoken": "latest",',
    '"jsonwebtoken": "^1.2 || ^2.0",',
    # F10: references, Secret object names, digests, commit ids
    "password: file:/run/secrets/db_password",
    "existingSecret: my-secret",
    "password: [REDACTED]",
    "image: nginx@sha256:3b4a1f3a1b1c0d6c5e5d9a2c0b9e8d7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d",
    "commit: 9631ac63045cfabddacc69fc06e919b4c13eb913",
    "gem 'webpush', github: 'mastodon/webpush', ref: '9631ac63045cfabddacc69fc06e919b4c13eb913'",
    # F4: no false hit on a slash in prose
    "Login with GitHub / GitLab is possible.",
    "// The login count, total * AppsPer100Logins / 100 rounded down",
]


@pytest.mark.parametrize(("text", "secret"), FIX_SECRET_FORMS)
def test_fixed_secret_form_is_removed(text, secret):
    out = remove_secrets(text)
    assert secret not in out
    assert REMOVED in out
    assert remove_secrets(out) == out


@pytest.mark.parametrize("line", FIX_HARMLESS_LINES)
def test_fixed_harmless_line_stays(line):
    assert remove_secrets(line) == line


def test_the_semicolon_of_a_nginx_line_stays():
    assert (
        remove_secrets("fastcgi_param DB_PASSWORD nginxPw12;")
        == "fastcgi_param DB_PASSWORD <removed>;"
    )


def test_a_digest_after_no_marker_is_still_removed():
    token = "3b4a1f3a1b1c0d6c5e5d9a2c0b9e8d7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d"
    assert token not in remove_secrets("the value is " + token)


# F1 and F11: a long word with key words in it, and a long htpasswd line.
@pytest.mark.parametrize(
    "unit",
    [
        "password",
        "token",
        "secret",
        "api_key",
        "credential",
        "passwd",
        "pwd",
        "chpasswd",
        "x_token",
        "x-password-",
        "a.b.password.",
        "htpasswd -",
    ],
)
def test_one_long_word_with_key_words_is_fast(unit):
    text = unit * (200_000 // len(unit))
    assert _seconds(text) < 3


@pytest.mark.parametrize("unit", ["password", "token", "chpasswd", "htpasswd -"])
def test_one_megabyte_of_long_lines_is_fast_after_the_cut(unit):
    from secreqgen.context.scan import cut_long_lines

    line = (unit * 2000)[:2000]
    text = cut_long_lines("\n".join([line] * 500))
    assert _seconds(text) < 3
