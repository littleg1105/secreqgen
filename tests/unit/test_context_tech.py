# ruff: noqa: E501
"""Tests of the technology check of the system context.

The two big lists come from the security review (44 names with a text that
names the technology and a text with similar words only; 36 usual forms).
"""

from __future__ import annotations

import pytest
from secreqgen.context.checks import (
    _Files,
    check_answer,
    clean_name,
    evidence_words,
    find_part,
    joined_name,
    needed_words,
    prose_names,
    without_prose,
)
from secreqgen.context.scan import CodebaseFacts, Part

# (name as a model writes it, text that names it as a manifest does,
# text with similar words only)
NAMES = [
    (
        "PostgreSQL 15",
        "services:\n  db:\n    image: postgres:15-alpine",
        "The database cluster stores users. Query the data with SQL.",
    ),
    (
        "Ruby on Rails",
        "source 'https://rubygems.org'\ngem 'rails', '~> 7.1'",
        "Ruby scripts help the team. We add guardrails to deploys.",
    ),
    (
        "ASP.NET Core",
        '<PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="8.0.0" />',
        "The core of the service is a web framework written by hand.",
    ),
    (
        "Amazon S3",
        '"dependencies": {"@aws-sdk/client-s3": "^3.500.0"}',
        "Store files in the cloud storage that your company runs.",
    ),
    (
        "Node.js",
        '"engines": {"node": ">=18"}',
        "The node count of the cluster is three. Use a JavaScript runtime.",
    ),
    (
        "Vue.js",
        '"dependencies": {"vue": "^3.4.0", "vue-router": "^4.2.0"}',
        "The preview of the view layer is ready. Review the code.",
    ),
    (
        "gRPC",
        "google.golang.org/grpc v1.60.0",
        "The remote procedure call layer uses plain HTTP.",
    ),
    (
        "C++",
        "project(App LANGUAGES CXX)\nset(CMAKE_CXX_STANDARD 17)\n# Built with C++17",
        "We use C and Python for tooling.",
    ),
    (
        "Go",
        "module example.com/app\n\ngo 1.21",
        "The cloud provider by Google runs it. MongoDB stores data. Algorithm names such as 'Gost' appear.",
    ),
    (
        "R",
        "Imports: dplyr\nDepends: R (>= 4.1)",
        "Run the script with ls -la. Use the share folder.",
    ),
    (
        "Kubernetes",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  labels:\n    app.kubernetes.io/name: shop",
        "Containers run on virtual machines in a cluster.",
    ),
    (
        "Elasticsearch",
        "<artifactId>spring-boot-starter-data-elasticsearch</artifactId>",
        "A search engine on a cluster, with an index of data.",
    ),
    (
        "RabbitMQ",
        "image: rabbitmq:3.12-management",
        "Messages go through a queue and a broker.",
    ),
    (
        "MS SQL Server",
        "image: mcr.microsoft.com/mssql/server:2022-latest\nenvironment:\n  MSSQL_SA_PASSWORD: x",
        "SQL migrations run on the application server.",
    ),
    (
        "Spring Boot",
        "<artifactId>spring-boot-starter-web</artifactId>",
        "A boot sequence runs in the spring release train.",
    ),
    (
        "Next.js",
        '"dependencies": {"next": "14.1.0", "react": "18.2.0"}',
        "The next steps are in the roadmap.",
    ),
    (
        "Tailwind CSS",
        '"devDependencies": {"tailwindcss": "^3.4.0", "postcss": "^8"}',
        "Styles use plain CSS and a wind of change.",
    ),
    (
        "Redis",
        "services:\n  cache:\n    image: redis:7-alpine",
        "We redistribute the load between workers.",
    ),
    (
        "MongoDB",
        '"mongoose": "^8.0.0"\nMONGODB_URI=x',
        "We store data in the store; a mongo shell is not used.",
    ),
    (
        "Docker",
        "FROM node:20-alpine\nRUN npm ci",
        "The app is dockerized by the operations team.",
    ),
    (
        "React",
        '"react": "^18.2.0", "react-dom": "^18.2.0"',
        "A reactive style with an event reaction.",
    ),
    (
        "Django",
        "django==4.2.7\ndjango-cors-headers==4.3.0",
        "The Jango mail tool is not used.",
    ),
    (
        "Express.js",
        '"express": "^4.18.2"',
        "Send an express parcel. Written permission is needed.",
    ),
    (
        "GraphQL",
        '"graphql": "^16.8.1", "apollo-server": "^4"',
        "A graph of the nodes is drawn.",
    ),
    (
        "Apache Kafka",
        "<groupId>org.apache.kafka</groupId><artifactId>kafka-clients</artifactId>",
        "The Apache License 2.0 applies to this project.",
    ),
    (
        "Prometheus",
        "- prom/prometheus:v2.48.0\n",
        "The monitoring uses logs and metrics.",
    ),
    ("Nginx", "FROM nginx:1.25-alpine", "An engine for the web proxy."),
    (
        "Google Cloud Storage",
        '"@google-cloud/storage": "^7.7.0"',
        "Google Analytics runs in the cloud and the storage of files is local.",
    ),
    (
        "Microsoft Azure Blob Storage",
        '<PackageReference Include="Azure.Storage.Blobs" Version="12.19.1" />',
        "Microsoft Teams and Azure DevOps hold the plans.",
    ),
    (
        ".NET 8",
        "<TargetFramework>net8.0</TargetFramework>",
        "Use the internet and a net of services.",
    ),
    ("Python 3.12", 'requires-python = ">=3.12"', "Pythonic style is welcome."),
    ("TypeScript", '"typescript": "^5.3.0"', "The text is typed by hand."),
    (
        "Java 21",
        "<java.version>21</java.version>",
        "Build with JavaScript and a Javascript bundler.",
    ),
    (
        "Rust",
        '[package]\nname = "x"\nedition = "2021"\n[dependencies]\ntokio = "1"\nserde = "1"',
        "We trust the reviewers. Trusted users can log in.",
    ),
    (
        "PHP",
        '"require": {"php": ">=8.1", "laravel/framework": "^10.0"}',
        "Run phpunit for the tests.",
    ),
    (
        "Laravel",
        '"laravel/framework": "^10.10"',
        "The lava flow and the level are shown.",
    ),
    (
        "MySQL 8",
        "image: mysql:8.0",
        "MariaDB is the database. The my-sql notes are old.",
    ),
    (
        "Hibernate",
        "<artifactId>hibernate-core</artifactId>",
        "The bear hibernates in the winter; data is stored on disk.",
    ),
    (
        "OAuth 2.0",
        "<artifactId>spring-security-oauth2-client</artifactId>",
        "Authorization by roles; a plain login form.",
    ),
    (
        "Keycloak",
        "image: quay.io/keycloak/keycloak:23.0",
        "A key store and a cloak of rules.",
    ),
    ("Flask", "flask==3.0.0", "The flask of water; the flash message."),
    (
        "SQLite",
        '"better-sqlite3": "^9.0.0"',
        "We use SQL in tests. A lite mode exists.",
    ),
    (
        "Terraform",
        'terraform {\n  required_version = ">= 1.5"\n}',
        "The terrain model of the site.",
    ),
    (
        "Jenkins",
        "pipeline { agent any }  // Jenkinsfile of Jenkins",
        "A build server runs jobs.",
    ),
]

# Forms that a manifest or a README shows. Each must be kept.
USUAL = [
    ("C++", "CXXFLAGS += -std=c++17"),
    ("C++", "Requires a C++20 compiler."),
    (
        "C++",
        "cmake_minimum_required(VERSION 3.20)\nproject(app LANGUAGES CXX)\nset(CMAKE_CXX_STANDARD 20)",
    ),
    (
        "Microsoft SQL Server",
        '<PackageReference Include="Microsoft.Data.SqlClient" Version="5.1.0" />',
    ),
    (
        "SQL Server",
        '<PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.0.0" />',
    ),
    ("PostgreSQL", '"dependencies": {"pg": "^8.11.0"}'),
    ("PostgreSQL", "psycopg2-binary==2.9.9"),
    ("PostgreSQL", "<artifactId>postgresql</artifactId>"),
    ("Amazon S3", "boto3==1.34.0"),
    ("Amazon S3", "<artifactId>aws-java-sdk-s3</artifactId>"),
    ("AWS S3", "@aws-sdk/client-s3"),
    ("Tailwind CSS", '"tailwindcss": "^3.4.0"'),
    ("Tailwind CSS", "@import 'tailwindcss';"),
    ("Tailwind CSS", "tailwind.config.js"),
    ("Docker", 'FROM ruby:3.3\nRUN bundle install\nCMD ["bin/rails","s"]'),
    ("Docker Compose", "services:\n  web:\n    build: ."),
    ("Rust", "[package]\nname='x'\nedition='2021'"),
    ("Rust", "tokio = '1'\nrust-version = '1.74'"),
    ("Ruby", "source 'https://rubygems.org'\ngem 'sinatra'"),
    ("Python", "[project]\nname='x'\ndependencies=['flask>=3']"),
    ("Vue", '"vue": "^3"'),
    ("jQuery", '"jquery": "^3.7.1"'),
    ("Material UI", '"@mui/material": "^5.15.0"'),
    ("Spring Security", "<artifactId>spring-boot-starter-security</artifactId>"),
    ("Bootstrap 5", '"bootstrap": "^5.3.2"'),
    ("Redux Toolkit", '"@reduxjs/toolkit": "^2.0.1"'),
    ("Sidekiq", "gem 'sidekiq'"),
    ("PostgreSQL", "gem 'pg', '~> 1.5'"),
    ("MySQL", "gem 'mysql2'"),
    (
        "Entity Framework Core",
        '<PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />',
    ),
    ("JWT", '"jsonwebtoken": "^9.0.0"'),
    ("JSON Web Tokens", "<artifactId>jjwt-api</artifactId>"),
    ("Apache Maven", '<project xmlns="http://maven.apache.org/POM/4.0.0">'),
    ("Gradle", "plugins { id 'java' }"),
    ("npm", '"scripts": {"build": "vite build"}'),
    ("Vite", '"vite": "^5.0.0"'),
]

# Cases that stay wrong, with the reason.
# No case of the two lists stays wrong: "the flask of water" has no capital
# letter, so it does not name Flask.
STAYS_WRONG: dict[tuple[str, str], str] = {}


def _facts(kind: str, text: str, path: str = "x.txt", **extra) -> CodebaseFacts:
    parts = [Part("F1", path, kind, text)]
    return CodebaseFacts("p", 1, 0, {}, parts, **extra)


def _kept(facts: CodebaseFacts, name: str) -> bool:
    answer = {"tech_stack": [{"kind": "k", "value": name, "source": "F1"}]}
    context, _ = check_answer(answer, facts, None)
    return bool(context["tech_stack"])


def _wrong(way: str, name: str):
    reason = STAYS_WRONG.get((way, name))
    return [pytest.mark.xfail(reason=reason, strict=True)] if reason else []


@pytest.mark.parametrize("name", [n for n, _, _ in NAMES])
def test_true_name_is_kept_from_a_manifest(name):
    text = next(t for n, t, _ in NAMES if n == name)
    assert _kept(_facts("manifest", text), name)


@pytest.mark.parametrize(
    "name",
    [pytest.param(n, marks=_wrong("manifest", n)) for n, _, _ in NAMES],
)
def test_false_name_is_dropped_when_similar_words_are_a_manifest(name):
    text = next(t for n, _, t in NAMES if n == name)
    assert not _kept(_facts("manifest", text), name)


@pytest.mark.parametrize(
    "name",
    [pytest.param(n, marks=_wrong("prose", n)) for n, _, _ in NAMES],
)
def test_false_name_is_dropped_when_similar_words_are_a_readme(name):
    text = next(t for n, _, t in NAMES if n == name)
    assert not _kept(_facts("readme", text), name)


@pytest.mark.parametrize(("name", "text"), USUAL)
def test_usual_forms_are_kept(name, text):
    assert _kept(_facts("manifest", text), name)


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Azure.Messaging.ServiceBus", {"azure", "messaging", "service", "bus"}),
        ("SqlClient", {"sql", "client", "sqlclient"}),
        ("mysql2", {"mysql", "mysql2"}),
        ("net8.0", {"net8", "net", "<netver>"}),
        ("JavaScript", {"javascript"}),
        ("c++17", {"c++"}),
        ("services:\n  db:", {"compose", "services", "db"}),
    ],
)
def test_evidence_words_hold(text, expected):
    words, _ = evidence_words(text)
    assert expected <= words


def test_javascript_is_not_split_into_java():
    words, order = evidence_words("JavaScript and TypeScript")
    assert "java" not in words
    assert "type" not in words
    assert " javascript and typescript " in order


def test_evidence_words_keep_the_order():
    _, order = evidence_words("Microsoft.EntityFrameworkCore.SqlServer")
    assert " sql server " in order


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Java 21", ["java"]),
        ("Python 3.12", ["python"]),
        (".NET 8", [".net"]),
        ("OAuth 2.0", ["oauth"]),
        ("H2", ["h2"]),
        ("PostgreSQL 15", ["postgresql"]),
        ("Ruby on Rails", ["ruby", "on", "rails"]),
        ("8", []),
    ],
)
def test_clean_name_drops_the_version(value, expected):
    assert clean_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Ruby on Rails", ["ruby", "rails"]),
        ("Tailwind CSS", ["tailwind", "css"]),
        ("Apache Kafka", ["kafka"]),
        ("Google Cloud Storage", ["google", "cloud", "storage"]),
        ("Azure Service Bus", ["bus"]),
        ("Go", ["go"]),
        ("C++", ["c++"]),
        ("C#", ["c#"]),
        ("ASP.NET Core", ["asp"]),
        ("Next.js", ["next"]),
    ],
)
def test_needed_words(value, expected):
    assert needed_words(clean_name(value)) == expected


def test_joined_name():
    assert joined_name(clean_name("Tailwind CSS")) == "tailwindcss"
    assert joined_name(clean_name("Spring Boot")) == "springboot"


@pytest.mark.parametrize(
    ("value", "prose", "expected"),
    [
        ("Rust", "we trust the reviewers", False),
        ("Rust", "the service is written in rust.", False),
        ("Rust", "The service is written in Rust.", True),
        ("Redis", "we redistribute the load", False),
        ("Redis", "cache: redis, queue: kafka", False),
        ("Redis", "The cache is Redis, the queue is Kafka.", True),
        ("Go", "written in go", False),
        ("R", "an r script", False),
        ("Java 21", "javascript only", False),
        ("Java 21", "needs java 21 or newer", False),
        ("Java 21", "It needs Java 21 or newer.", True),
        ("Next.js", "the next steps are in the roadmap", False),
        ("Next.js", "built with next.js", True),
        ("Docker", "the app is dockerized", False),
        ("Python", "python3 is used", False),
        ("C++", "we use c and python", False),
        ("C++", "a c++ compiler", True),
    ],
)
def test_prose_needs_a_whole_phrase(value, prose, expected):
    assert prose_names(clean_name(value), prose) is expected


def test_without_prose_drops_comments_and_sentences():
    text = (
        "# ES_JAVA_OPTS=java\n"
        "// uses java too\n"
        "The next steps are in the roadmap.\n"
        "FROM ruby:3.3\n"
        "RUN bundle install now please\n"
        "A boot sequence runs in the spring release train.\n"
        "#!/usr/bin/env python\n"
        "gem 'rails'\n"
    )
    out = without_prose(text)
    assert "java" not in out.lower()
    assert "roadmap" not in out
    assert "boot sequence" not in out
    assert "FROM ruby:3.3" in out
    assert "RUN bundle install now please" in out
    assert "#!/usr/bin/env python" in out
    assert "gem 'rails'" in out


# ------------------------------------------------------- the source part


def _two_parts() -> CodebaseFacts:
    parts = [
        Part("F1", "(file tree)", "tree", "app/models\napp/views"),
        Part("F2", "README.md", "readme", "A shop. It uses Redis for the cache."),
        Part("F3", "Gemfile", "manifest", "gem 'rails'\ngem 'pg'\nruby '3.3'"),
        Part("F4", "docker-compose.yml", "container", "image: postgres:16"),
    ]
    return CodebaseFacts("p", 4, 0, {}, parts, suffixes={".rb": 90, ".cs": 2})


def _source(value: str, named: str = "F2") -> str | None:
    return find_part(value, named, _Files(_two_parts()))


def test_source_is_the_part_that_holds_the_evidence():
    assert _source("Ruby on Rails") == "F3"


def test_source_prefers_the_part_that_the_model_named():
    assert _source("PostgreSQL", named="F4") == "F4"
    assert _source("PostgreSQL", named="F3") == "F3"


def test_source_of_prose_is_the_readme():
    assert _source("Redis", named="F3") == "F2"


def test_source_of_a_file_type_is_the_tree_part():
    assert _source("C#") == "F1"
    assert _source("Ruby", named="F4") in {"F1", "F3"}


def test_source_of_a_file_name_is_the_part_with_that_path():
    assert _source("Docker", named="F2") == "F4"


def test_file_type_without_a_tree_part_uses_the_named_part():
    facts = CodebaseFacts(
        "p", 1, 0, {}, [Part("F1", "README.md", "readme", "text")], suffixes={".rs": 3}
    )
    assert find_part("Rust", "F1", _Files(facts)) == "F1"


def test_no_evidence_gives_none():
    assert _source("Kafka") is None
    assert _source("8") is None


def test_dotnet_is_not_proved_by_the_word_net():
    facts = _facts("manifest", "a net of nodes, net")
    assert not _kept(facts, ".NET 8")
    assert _kept(
        _facts("manifest", "<TargetFramework>net8.0</TargetFramework>"), ".NET 8"
    )


def test_the_reason_text_stays():
    facts = _facts("manifest", "gem 'rails'")
    answer = {"tech_stack": [{"kind": "k", "value": "Kafka", "source": "F1"}]}
    _, dropped = check_answer(answer, facts, None)
    assert {"what": "tech_stack: Kafka", "why": "not in the files"} in dropped


def test_a_huge_part_is_fast():
    text = "word " * 200_000
    facts = _facts("manifest", text)
    assert not _kept(facts, "Redis")


# ------------------------------------------- second review: faults T1 to T5


# T1: the Web SDK holds ASP.NET Core with no package line.
def test_t1_web_sdk_proves_aspnet_core():
    text = '<Project Sdk="Microsoft.NET.Sdk.Web">\n<TargetFramework>net8.0</TargetFramework>'
    facts = _facts("manifest", text, path="App.csproj")
    assert _kept(facts, "ASP.NET Core")
    assert _kept(facts, ".NET 8")


def test_t1_plain_sdk_does_not_prove_aspnet_core():
    facts = _facts("manifest", '<Project Sdk="Microsoft.NET.Sdk">', path="App.csproj")
    assert not _kept(facts, "ASP.NET Core")
    assert _kept(facts, ".NET")


# T2: a client library proves the database, the cache or the queue.
CLIENT_LIBS = [
    ("MySQL", "requirements.txt", "mysqlclient==2.2.0"),
    ("MySQL", "requirements.txt", "PyMySQL==1.1.0"),
    ("MySQL", "requirements.txt", "aiomysql"),
    ("MySQL", "App.csproj", '<PackageReference Include="MySqlConnector" />'),
    ("PostgreSQL", "requirements.txt", "asyncpg==0.29"),
    ("PostgreSQL", "go.mod", "github.com/lib/pq v1.10.9"),
    ("PostgreSQL", "composer.json", '"ext-pdo_pgsql": "*"'),
    ("Redis", "package.json", '"ioredis": "^5.3.2"'),
    ("Redis", "package.json", '"bullmq": "^5"'),
    ("Redis", "pom.xml", "<artifactId>jedis</artifactId>"),
    ("Redis", "build.gradle", "implementation 'io.lettuce:lettuce-core:6.3.0'"),
    ("Redis", "go.mod", "github.com/gomodule/redigo v1.8.9"),
    ("Kafka", "package.json", '"kafkajs": "^2.2.4"'),
    ("RabbitMQ", "package.json", '"amqplib": "^0.10"'),
    ("RabbitMQ", "pom.xml", "<artifactId>spring-boot-starter-amqp</artifactId>"),
    ("RabbitMQ", "requirements.txt", "pika==1.3.2"),
    ("Oracle", "pom.xml", "<artifactId>ojdbc8</artifactId>"),
    ("MongoDB", "Gemfile", "gem 'mongoid'"),
    ("Elasticsearch", "Gemfile", "gem 'searchkick'"),
]


@pytest.mark.parametrize(("name", "path", "text"), CLIENT_LIBS)
def test_t2_client_library_proves_the_product(name, path, text):
    assert _kept(_facts("manifest", text, path=path), name)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("MySQL", "mysqlish notes"),
        ("Redis", "redistribute"),
        ("Kafka", "kafkaesque"),
        ("RabbitMQ", "bunnies and pikas"),
        ("Oracle", "ojdbcx"),
    ],
)
def test_t2_similar_words_still_do_not_prove_the_product(name, text):
    assert not _kept(_facts("manifest", text), name)


# T3: a list of packages is evidence; a sentence is not.
@pytest.mark.parametrize(
    ("kind", "text", "name"),
    [
        ("manifest", "django flask celery redis", "Flask"),
        ("manifest", "postgresql redis nginx curl", "Redis"),
        ("container", "    apt install postgresql redis nginx curl", "Nginx"),
    ],
)
def test_t3_package_list_is_evidence(kind, text, name):
    assert _kept(_facts(kind, text), name)


@pytest.mark.parametrize(
    "line",
    [
        "The next steps are in the roadmap.",
        "we use redis for the cache of the web",
        "Install redis nginx curl wget",
    ],
)
def test_t3_sentences_are_still_dropped(line):
    assert without_prose(line) == ""


# T4: a file name or a file content proves the technology.
FILE_PROOFS = [
    ("Dart", "pubspec.yaml", "environment:\n  sdk: '>=3.0.0 <4.0.0'"),
    ("Dart", "pubspec.yaml", "name: app"),
    ("Helm", "charts/app/Chart.yaml", "name: app"),
    ("Helm", "x.yaml", "apiVersion: v2\nname: app\nappVersion: 1.0"),
    ("TypeScript", "tsconfig.json", "{}"),
    ("TypeScript", "x.json", '{"compilerOptions": {"strict": true}}'),
    ("Jenkins", "Jenkinsfile", "pipeline { agent any }"),
    ("Jenkins", "x.txt", "pipeline { agent any }"),
    ("Kubernetes", "k8s/deployment.yaml", "kind: Deployment"),
    ("Kubernetes", "x.yaml", "apiVersion: apps/v1\nkind: Deployment"),
    ("GitHub Actions", ".github/workflows/ci.yml", "name: ci"),
    ("GitHub Actions", "x.yml", "jobs:\n  b:\n    runs-on: ubuntu-latest"),
    ("Java", "build.gradle", "sourceCompatibility = '17'"),
    ("Node.js", ".nvmrc", "20.11.0"),
]


@pytest.mark.parametrize(("name", "path", "text"), FILE_PROOFS)
def test_t4_file_name_or_content_proves_the_technology(name, path, text):
    assert _kept(_facts("manifest", text, path=path), name)


@pytest.mark.parametrize(
    ("name", "path", "text"),
    [
        ("Helm", "package.json", '{"dependencies": {"chart.js": "^4"}}'),
        ("GitHub Actions", "README.md", "Hosted on github; see the actions page"),
        ("Dart", "x.yaml", "sdk: 21"),
        ("Kubernetes", "x.yaml", "kind: Service"),
        ("TypeScript", "x.json", '{"compiler": 1}'),
    ],
)
def test_t4_look_alikes_do_not_prove_the_technology(name, path, text):
    assert not _kept(_facts("manifest", text, path=path), name)


def test_t4_path_marks_hold_only_for_the_right_path():
    for ref_path, name in [
        ("Chart.yaml", "Helm"),
        (".github/workflows/a.yml", "GitHub Actions"),
    ]:
        assert _kept(_facts("manifest", "x: 1", path=ref_path), name)
    assert not _kept(_facts("manifest", "x: 1", path="MyChart.yaml"), "Helm")


# T5: design limit. Rule B accepts a whole word from prose and the plural of a
# name word in a manifest. A model must write the name first, so this is a
# wrong keep only when the model guessed it. Each case is marked and explained.
LIMITS = [
    ("Express", "Express delivery"),
    ("Kafka", "Franz Kafka wrote novels."),
]
# A plain word with no capital letter in the prose is no name.
NO_NAMES = [
    ("Swift", "A swift response is expected."),
    ("Helm", "The helm of the ship"),
    ("Vault", "A bank vault"),
    ("Flask", "The flask of water; the flash message."),
]


@pytest.mark.parametrize(("name", "text"), NO_NAMES)
def test_a_plain_word_in_prose_is_no_name(name, text):
    assert not _kept(_facts("readme", text), name)


@pytest.mark.parametrize(("name", "text"), LIMITS)
@pytest.mark.xfail(
    strict=True, reason="a capital word in prose counts as a name (design limit)"
)
def test_t5_common_word_in_prose_stays_a_known_limit(name, text):
    assert not _kept(_facts("readme", text), name)


@pytest.mark.xfail(
    strict=True, reason="plural alternative of a name word in a manifest (design limit)"
)
def test_t5_plural_in_a_manifest_stays_a_known_limit():
    assert not _kept(_facts("manifest", "Mochas and mochaccino."), "Mocha")
