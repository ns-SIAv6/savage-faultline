"""Build a synthetic multi-repo ecosystem with planted, known ground truth.

The real ecosystem cannot tell us whether the checkers are correct — we do not
know the right answer for it. This fixture does, because we write the answer.

Planted facts (asserted in test_checkers.py):
  alpha-service -> beta-engine   cross-repo, 2 import sites, UNDECLARED
  alpha-service -> beta.ghost    module beta-engine does not define
  beta-engine   -> gamma-lib     declared at the WRONG org
  epsilon-a    <-> epsilon-b     mutual import (CYCLE)
  delta-tools                    isolated: imports nothing, imported by nothing
  gamma-lib                      nothing imports it in code
"""

import json
import os
import shutil

ROOT = os.path.join(os.path.dirname(__file__), "fixture")

# byte-identical content shared by rho-app and upsilon-fork at DIFFERENT
# depths — the example-repo/example-style finding (162 identical files hidden by
# nested layout) miniaturised. Full-path matching sees zero overlap here.
SHARED_MAIN = """\
class Orchestrator:
    \"\"\"Drives the pipeline: load, transform, persist, report.\"\"\"

    def __init__(self, config):
        self.config = config
        self.state = {}

    def run(self):
        self.state["started"] = True
        return self.state
"""

SHARED_CORE = """\
def normalise(records):
    \"\"\"Lowercase keys, drop empties, sort by id.\"\"\"
    out = []
    for r in records:
        cleaned = {k.lower(): v for k, v in r.items() if v is not None}
        if cleaned:
            out.append(cleaned)
    return sorted(out, key=lambda r: r.get("id", ""))
"""

SHARED_UTIL = """\
import hashlib


def fingerprint(payload):
    \"\"\"Stable content hash for dedupe across runs.\"\"\"
    raw = repr(sorted(payload.items())).encode()
    return hashlib.sha1(raw).hexdigest()
"""


FILES = {
    # ---- alpha-service: imports beta, and imports a module beta doesn't have
    "alpha-service/pyproject.toml": '''[project]
name = "alpha-service"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = []
''',
    "alpha-service/src/alpha/__init__.py": "",
    "alpha-service/src/alpha/core.py": '''from beta.engine import Runner
from beta.ghost import MissingThing
from common.util import Thing


def boot():
    return Runner()
''',
    # 'common' is provided by BOTH alpha-service and beta-engine. Each must
    # resolve its own — this is the regression case for the ambiguous-root bug.
    "alpha-service/src/common/__init__.py": "",
    "alpha-service/src/common/util.py": "class Thing:\n    pass\n",

    # ---- beta-engine: declares gamma-lib, but at the wrong org
    "beta-engine/pyproject.toml": '''[project]
name = "beta-engine"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "gamma-lib @ git+https://github.com/WRONG-ORG/gamma-lib.git@main",
]
''',
    "beta-engine/src/beta/__init__.py": "",
    "beta-engine/src/beta/engine.py": '''from common.util import Other


class Runner:
    """Exists. beta.ghost deliberately does not."""
    pass
''',
    "beta-engine/src/common/__init__.py": "",
    "beta-engine/src/common/util.py": "class Other:\n    pass\n",

    # ---- gamma-lib: JS, declared by beta but imported by nobody
    "gamma-lib/package.json": json.dumps({
        "name": "gamma-lib", "version": "1.0.0", "dependencies": {}
    }, indent=2),
    "gamma-lib/src/index.js": """export function helper() { return 1; }
export function unusedHelper() { return 2; }
""",

    # ---- delta-tools: fully isolated in code. Plants shell LOCALS —
    # DESCRIPTION and JOB_NAME are assigned in the script, never exported, so
    # they are not environment and must produce no coupling — while its .env
    # DESCRIPTION *is* env (one repo only, so still no coupling).
    "delta-tools/pyproject.toml": '''[project]
name = "delta-tools"
version = "1.0.0"
dependencies = []
''',
    "delta-tools/src/delta/__init__.py": "",
    "delta-tools/src/delta/main.py": '''def standalone():
    return "nothing connects to me in either direction"
''',
    # a tests/ tree must not publish the package name `tests`; a nested
    # Gradle build file must contribute its dependencies
    "delta-tools/tests/test_main.py": "import pytest\nfrom delta.main import standalone\n",
    "delta-tools/app/build.gradle.kts":
        'dependencies {\n    implementation("com.squareup.okhttp3:okhttp:4.12.0")\n}\n',
    "delta-tools/run.sh": '''DESCRIPTION="local scan"
JOB_NAME=delta
echo "${DESCRIPTION} ${JOB_NAME}"
''',
    "delta-tools/.env": "DESCRIPTION=scan tools\n",

    # ---- epsilon-a <-> epsilon-b : a cycle across a repo boundary
    "epsilon-a/pyproject.toml": '''[project]
name = "epsilon-a"
version = "1.0.0"
dependencies = ["epsilon-b"]
''',
    "epsilon-a/src/epsilon_a/__init__.py": "",
    "epsilon-a/src/epsilon_a/a.py": "from epsilon_b.b import BThing\n\n\nclass AThing:\n    pass\n",

    "epsilon-b/pyproject.toml": '''[project]
name = "epsilon-b"
version = "1.0.0"
dependencies = ["epsilon-a"]
''',
    "epsilon-b/src/epsilon_b/__init__.py": "",
    "epsilon-b/src/epsilon_b/b.py": "from epsilon_a.a import AThing\n\n\nclass BThing:\n    pass\n",

    # ---- iota-lib: a *file stem* that shadows a stdlib name.
    # Having types.py must not make iota-lib the owner of the name `types`
    # for anybody else — that is how `import types` invented a seam.
    "iota-lib/pyproject.toml": '''[project]
name = "iota-lib"
version = "1.0.0"
dependencies = []
''',
    "iota-lib/types.py": "class LocalTypes:\n    pass\n",

    # ---- kappa-app: `import types` is the stdlib module, not iota-lib's file.
    # Also reads SHARED_SECRET — the positive control for env coupling.
    "kappa-app/pyproject.toml": '''[project]
name = "kappa-app"
version = "1.0.0"
dependencies = []
''',
    "kappa-app/src/kappa/__init__.py": "",
    "kappa-app/src/kappa/main.py": '''import os
import types

TOKEN = os.environ.get("SHARED_SECRET")


def go():
    return types.SimpleNamespace()
''',

    # ---- lambda-web: a *config filename* must not publish a package name.
    # vite.config.ts yielding the name `vite` is what made real npm imports
    # look like cross-repo edges.
    # deliberately BOM-prefixed: a leading BOM made json.load throw and the
    # failure was swallowed, so the repo registered no name at all
    "lambda-web/package.json": "\ufeff" + json.dumps(
        {"name": "lambda-web", "version": "1.0.0"}, indent=2),
    "lambda-web/vite.config.ts": "export default { plugins: [] };\n",
    "lambda-web/src/state.ts": "import { create } from 'zustand';\nexport const useStore = create(() => ({}));\n",

    # ---- mu-web: 'vite' here is the npm package, not lambda-web.
    # Also plants, with known line numbers and known truth:
    #   node:assert        — a node: builtin; never a seam, never external
    #   socket.io-client   — one npm package; splitting on "." makes it
    #                        "socket", inventing a seam to sigma-socket
    #   gamma-lib          — a real seam at line 5 (blank line 4 above it is
    #                        how ^\s* used to report the wrong line)
    #   ${DESCRIPTION}     — a JS template literal, NOT an env var
    #   SHARED_SECRET      — a real env var, also read by kappa-app
    #   audit_log          — a real shared table, also used by tau-mono
    "mu-web/package.json": json.dumps(
        {"name": "mu-web", "version": "1.0.0",
         "dependencies": {"gamma-lib": "^1.0.0"}}, indent=2),
    "mu-web/src/main.ts": """import { defineConfig } from 'vite';
import assert from 'node:assert';
import { create } from 'zustand';
import { io } from 'socket.io-client';

import { helper } from 'gamma-lib';

const d = `${DESCRIPTION}`;
const s = process.env.SHARED_SECRET;
const rows = supabase.from('audit_log');
export default defineConfig({});
""",

    # ---- nu-empty: nothing parseable, and a .gitmodules aggregator besides.
    # Must never receive a certificate, and its denial must say where the
    # code actually lives.
    "nu-empty/README.md": "# nu-empty\n\nNo source files at all.\n",
    "nu-empty/.gitmodules": ('[submodule "delta-tools"]\n'
                             '\tpath = delta-tools\n'
                             '\turl = https://github.com/example-org/delta-tools.git\n'),

    # ---- xi-one and omicron-two both publish `shared`. pi-three's import of
    # it is genuinely ambiguous and must be reported, never silently assigned
    # to whichever repo sorts first.
    "xi-one/pyproject.toml": '''[project]
name = "xi-one"
version = "1.0.0"
dependencies = []
''',
    "xi-one/shared/__init__.py": "",
    "xi-one/shared/util.py": "class OneUtil:\n    pass\n",

    "omicron-two/pyproject.toml": '''[project]
name = "omicron-two"
version = "1.0.0"
dependencies = []
''',
    "omicron-two/shared/__init__.py": "",
    "omicron-two/shared/util.py": "class TwoUtil:\n    pass\n",

    "pi-three/pyproject.toml": '''[project]
name = "pi-three"
version = "1.0.0"
dependencies = []
''',
    "pi-three/src/pi/__init__.py": "",
    "pi-three/src/pi/app.py": "from shared.util import OneUtil\n",

    # ---- sigma-socket: publishes the npm package name "socket". mu-web
    # imports "socket.io-client" — a different package. Splitting on "."
    # before "/" is what collapsed the latter into the former.
    "sigma-socket/package.json": json.dumps(
        {"name": "socket", "version": "1.0.0"}, indent=2),
    "sigma-socket/index.js": "export function socket() { return 1; }\n",
    "sigma-socket/deploy.sh": "export DEPLOY_TARGET=prod\naws s3 sync s3://savage-deploy-artifacts .\n",

    # ---- tau-mono: a workspace. The dependency on gamma-lib lives in the
    # NESTED package.json — reading only the root manifest is what made a
    # declared dependency look undeclared.
    "tau-mono/package.json": json.dumps(
        {"name": "tau-mono", "version": "1.0.0",
         "workspaces": ["packages/*"]}, indent=2),
    "tau-mono/packages/web/package.json": json.dumps(
        {"name": "@tau/web", "version": "1.0.0",
         "dependencies": {"gamma-lib": "^1.0.0"}}, indent=2),
    "tau-mono/packages/web/index.js":
        "import { helper } from 'gamma-lib';\nconst rows = supabase.from('audit_log');\n",
    "tau-mono/deploy.sh": "export DEPLOY_TARGET=prod\naws s3 sync s3://savage-deploy-artifacts .\n",

    # ---- zeta-one -> zeta-two -> zeta-three -> zeta-one: a three-repo cycle.
    # Pairwise cycle detection sees nothing; the strongly-connected component
    # is the truth.
    "zeta-one/pyproject.toml": '''[project]
name = "zeta-one"
version = "1.0.0"
dependencies = ["zeta-two"]
''',
    "zeta-one/src/zeta_one/__init__.py": "",
    "zeta-one/src/zeta_one/a.py": "from zeta_two.b import B2\n\n\nclass A1:\n    pass\n",

    "zeta-two/pyproject.toml": '''[project]
name = "zeta-two"
version = "1.0.0"
dependencies = ["zeta-three"]
''',
    "zeta-two/src/zeta_two/__init__.py": "",
    "zeta-two/src/zeta_two/b.py": "from zeta_three.c import C3\n\n\nclass B2:\n    pass\n",

    "zeta-three/pyproject.toml": '''[project]
name = "zeta-three"
version = "1.0.0"
dependencies = ["zeta-one"]
''',
    "zeta-three/src/zeta_three/__init__.py": "",
    "zeta-three/src/zeta_three/c.py": "from zeta_one.a import A1\n\n\nclass C3:\n    pass\n",

    # ---- rho-app and upsilon-fork share three byte-identical files that sit
    # at different depths: full-path matching is blind, suffix matching is not
    "rho-app/pyproject.toml": '''[project]
name = "rho-app"
version = "1.0.0"
dependencies = []
''',
    "rho-app/src/rho/__init__.py": "",
    "rho-app/src/rho/main.py": SHARED_MAIN,
    "rho-app/src/rho/core.py": SHARED_CORE,
    "rho-app/src/rho/util.py": SHARED_UTIL,

    "upsilon-fork/pyproject.toml": '''[project]
name = "upsilon-fork"
version = "1.0.0"
dependencies = []
''',
    "upsilon-fork/deep/nested/rho/__init__.py": "x = 1\n",
    "upsilon-fork/deep/nested/rho/main.py": SHARED_MAIN,
    "upsilon-fork/deep/nested/rho/core.py": SHARED_CORE,
    "upsilon-fork/deep/nested/rho/util.py": SHARED_UTIL,
    "upsilon-fork/extra.py": "def only_here():\n    return 'diverged'\n",
}
# what the hosting platform says each repo actually is
OWNERS = {
    "alpha-service": "example-org",
    "beta-engine": "example-org",
    "gamma-lib": "example-org",
    "delta-tools": "example-org",
    "epsilon-a": "example-org",
    "epsilon-b": "example-org",
    "iota-lib": "example-org",
    "kappa-app": "example-org",
    "lambda-web": "example-org",
    "mu-web": "example-org",
    "nu-empty": "example-org",
    "xi-one": "example-org",
    "omicron-two": "example-org",
    "pi-three": "example-org",
    "sigma-socket": "example-org",
    "tau-mono": "example-org",
    "zeta-one": "example-org",
    "zeta-two": "example-org",
    "zeta-three": "example-org",
    "rho-app": "example-org",
    "upsilon-fork": "example-org",
}


def build():
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    for rel, content in FILES.items():
        p = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    json.dump(OWNERS, open(os.path.join(ROOT, "..", "fixture-owners.json"), "w"), indent=1)
    return ROOT


if __name__ == "__main__":
    r = build()
    print(f"fixture built at {r}")
    print(f"{len(FILES)} files, {len({p.split('/')[0] for p in FILES})} repos")
