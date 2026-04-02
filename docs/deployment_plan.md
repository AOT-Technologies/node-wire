Plan: Publish Runtime + Connectors as Separate Python Packages

 Context

 src/runtime/ and src/connectors/ are currently bundled into one monolithic
 node-wire package. The goal is to:
 - Publish node-wire-runtime as a standalone installable SDK
 - Publish each connector as an independent package (e.g., node-wire-stripe)
 - Keep source code unreadable on public PyPI using Cython-compiled binary wheels
 - Enable auto-discovery of installed connectors via entry points, with
 per-deployment control via connectors.yml
 - Stay in the monorepo; open-source transition later just means adding sdist back
 to CI

 ---
 Architecture Decisions

 ┌─────────────┬───────────────────────────────┬────────────────────────────────┐
 │   Concern   │           Decision            │             Reason             │
 ├─────────────┼───────────────────────────────┼────────────────────────────────┤
 │ Registry    │ Public PyPI, binary-only      │ No private infra needed;       │
 │             │ wheels                        │ source not shipped in .whl     │
 ├─────────────┼───────────────────────────────┼────────────────────────────────┤
 │ Source      │ Cython (.py → .so/.pyd)       │ Strongest protection; easy to  │
 │ protection  │                               │ drop when open-sourcing        │
 ├─────────────┼───────────────────────────────┼────────────────────────────────┤
 │             │ Entry points                  │ Entry points = auto-discover   │
 │ Discovery   │ (node_wire.connectors group)  │ installed; YAML =              │
 │             │ + connectors.yml              │ per-deployment                 │
 │             │                               │ enable/configure               │
 ├─────────────┼───────────────────────────────┼────────────────────────────────┤
 │ Repo layout │ Monorepo, packages/ directory │ One CI pipeline, one place to  │
 │             │                               │ develop                        │
 └─────────────┴───────────────────────────────┴────────────────────────────────┘

 ---
 Phase 1: Restructure Source Layout

 Rename modules to properly namespaced names (required for separate PyPI packages):

 src/
 ├── node_wire_runtime/          # was: runtime/
 │   ├── __init__.py
 │   ├── base_connector.py
 │   ├── models.py
 │   ├── errors.py
 │   ├── policy.py
 │   ├── secrets.py
 │   ├── resilience.py
 │   ├── sdk_action_spec.py
 │   └── observability.py
 ├── node_wire_stripe/           # was: connectors/stripe/
 ├── node_wire_google_drive/     # was: connectors/google_drive/
 ├── node_wire_http_generic/     # was: connectors/http_generic/
 ├── node_wire_smtp/             # was: connectors/smtp/
 ├── node_wire_fhir_epic/        # was: connectors/fhir_epic/
 └── node_wire_fhir_cerner/      # was: connectors/fhir_cerner/

 Update all imports across the codebase:
 - from runtime import ... → from node_wire_runtime import ...
 - from connectors import auto_register → from node_wire_runtime.connector_registry
 import auto_register
 - from connectors.manifest import build_manifest → from node_wire_runtime.manifest
 import build_manifest

 Move auto_register() and build_manifest() into node_wire_runtime/ (they are runtime
  concerns, not connector concerns). The manifest and registry belong in the runtime
  package.

 ---
 Phase 2: Per-Package pyproject.toml Files

 Create packages/ directory with a subfolder per package, each containing its own
 pyproject.toml.

 packages/runtime/pyproject.toml

 [project]
 name = "node-wire-runtime"
 version = "0.1.0"
 requires-python = ">=3.11"
 dependencies = [
     "pydantic>=2.6.0",
     "tenacity>=8.2.0",
     "pybreaker>=1.0.0",
     "opentelemetry-api",
     "opentelemetry-sdk",
     "opentelemetry-exporter-otlp",
     "traceloop-sdk",
 ]

 [build-system]
 requires = ["setuptools", "cython>=3.0"]
 build-backend = "setuptools.build_meta"

 [tool.setuptools.packages.find]
 where = ["../../src"]
 include = ["node_wire_runtime*"]

 packages/connectors/stripe/pyproject.toml

 [project]
 name = "node-wire-stripe"
 version = "0.1.0"
 requires-python = ">=3.11"
 dependencies = [
     "node-wire-runtime>=0.1.0",
     "stripe>=10.0.0",
 ]

 [project.entry-points."node_wire.connectors"]
 stripe = "node_wire_stripe.logic"

 [build-system]
 requires = ["setuptools", "cython>=3.0"]
 build-backend = "setuptools.build_meta"

 [tool.setuptools.packages.find]
 where = ["../../../src"]
 include = ["node_wire_stripe*"]

 (Repeat pattern for each connector with their own dependencies.)

 ---
 Phase 3: Cython Build Setup

 Add a setup.py in each package directory alongside pyproject.toml:

 # packages/runtime/setup.py
 from Cython.Build import cythonize
 from setuptools import setup
 import os, glob

 src_root = os.path.abspath("../../src/node_wire_runtime")
 pyx_files = glob.glob(f"{src_root}/**/*.py", recursive=True)

 setup(
     ext_modules=cythonize(
         pyx_files,
         compiler_directives={"language_level": "3"},
         build_dir="build",
     ),
 )

 Key build rule: only build wheels (python -m build --wheel), never sdist. The .py
 source files are excluded from the wheel; only the compiled .so/.pyd files are
 included.

 Add MANIFEST.in per package to explicitly exclude .py files from wheels:
 recursive-exclude src *.py
 recursive-include src *.so *.pyd

 ---
 Phase 4: Update auto_register() to Use Entry Points

 Move auto_register() from src/connectors/__init__.py into
 src/node_wire_runtime/connector_registry.py:

 # node_wire_runtime/connector_registry.py
 from importlib.metadata import entry_points

 def auto_register() -> list:
     """
     Load all installed connector packages that declare themselves
     under the 'node_wire.connectors' entry point group.
     Importing the module triggers BaseConnector.__init_subclass__,
     which registers the connector in _CONNECTOR_REGISTRY.
     """
     loaded = []
     eps = entry_points(group="node_wire.connectors")
     for ep in eps:
         module = ep.load()
         loaded.append(module)
     return loaded

 The _CONNECTOR_REGISTRY and BaseConnector.__init_subclass__ in base_connector.py
 need no changes — they already self-register on import.

 ---
 Phase 5: Entry Point Declarations in Each Connector

 Each connector pyproject.toml declares its entry point:

 # node-wire-stripe
 [project.entry-points."node_wire.connectors"]
 stripe = "node_wire_stripe.logic"

 # node-wire-google-drive
 [project.entry-points."node_wire.connectors"]
 google_drive = "node_wire_google_drive.logic"

 # node-wire-http-generic
 [project.entry-points."node_wire.connectors"]
 http_generic = "node_wire_http_generic.logic"

 # node-wire-smtp
 [project.entry-points."node_wire.connectors"]
 smtp = "node_wire_smtp.logic"

 # node-wire-fhir-epic
 [project.entry-points."node_wire.connectors"]
 fhir_epic = "node_wire_fhir_epic.logic"

 # node-wire-fhir-cerner
 [project.entry-points."node_wire.connectors"]
 fhir_cerner = "node_wire_fhir_cerner.logic"

 ---
 Phase 6: connectors.yml Role (Unchanged)

 config/connectors.yaml continues to control per-deployment configuration:
 - enabled: true/false — even if a connector is installed, it won't run unless
 enabled
 - exposed_via — controls which protocols (rest/grpc/mcp) it's active on
 - Connector-specific config (base_url, scopes, etc.)

 The ConnectorFactory in src/bindings/factory.py already reads this file. No changes
  needed there beyond updating the import path for auto_register.

 The lifecycle remains:
 1. auto_register() → imports all installed connector logic modules → populates
 _CONNECTOR_REGISTRY
 2. ConnectorFactory.load() → reads connectors.yml, instantiates only enabled
 connectors from registry

 ---
 Phase 7: CI/CD for Multi-Platform Wheel Builds

 Add .github/workflows/publish.yml using cibuildwheel to build wheels for:
 - Linux (manylinux x86_64, aarch64)
 - macOS (x86_64, arm64)
 - Windows (amd64)

 # Simplified workflow sketch
 - uses: pypa/cibuildwheel@v2
   with:
     package-dir: packages/runtime
 - uses: pypa/gh-action-pypi-publish@release/v1
   with:
     packages-dir: dist/
     skip-existing: true

 Each package gets its own publish job triggered by a git tag: runtime-v0.1.0,
 connector-stripe-v0.1.0, etc.

 ---
 Critical Files to Modify

 ┌────────────────────────────┬─────────────────────────────────────────────────┐
 │            File            │                     Change                      │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/runtime/ (all files)   │ Rename dir → src/node_wire_runtime/             │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/connectors/stripe/     │ Rename dirs → src/node_wire_stripe/,            │
 │ etc.                       │ src/node_wire_google_drive/, etc.               │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/connectors/__init__.py │ Remove; move auto_register to                   │
 │                            │ node_wire_runtime/connector_registry.py         │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/connectors/manifest.py │ Move to node_wire_runtime/manifest.py           │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ All connector logic.py     │ Update imports: from runtime import → from      │
 │ files                      │ node_wire_runtime import                        │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/bindings/factory.py    │ Update import of auto_register and              │
 │                            │ build_manifest                                  │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ src/bindings_entrypoint.py │ Update imports                                  │
 ├────────────────────────────┼─────────────────────────────────────────────────┤
 │ pyproject.toml (root)      │ Update to reflect new src layout; keep as       │
 │                            │ dev/bindings package                            │
 └────────────────────────────┴─────────────────────────────────────────────────┘

 New files to create:
 - packages/runtime/pyproject.toml + setup.py
 - packages/connectors/{name}/pyproject.toml + setup.py (×6)
 - src/node_wire_runtime/connector_registry.py (replaces connectors/__init__.py)
 - .github/workflows/publish.yml

 ---
 Open Source Migration Path

 When ready to open source:
 1. Remove setup.py (Cython build) from each package
 2. Switch pyproject.toml build backend back to pure setuptools (no Cython)
 3. Run python -m build (now produces both sdist + wheel)
 4. Publish to PyPI as usual — source now visible

 No structural changes needed; the package names, entry points, and discovery
 mechanism remain identical.

 ---
 Verification

 1. Local install test:
 pip install -e packages/runtime
 pip install -e packages/connectors/stripe
 python -c "from node_wire_runtime.connector_registry import auto_register;
 auto_register(); from node_wire_runtime import _CONNECTOR_REGISTRY;
 print(_CONNECTOR_REGISTRY)"
 2. Entry point discovery:
 python -c "from importlib.metadata import entry_points;
 print(list(entry_points(group='node_wire.connectors')))"
 3. Binary wheel check (source not included):
 python -m build --wheel packages/runtime
 unzip -l dist/node_wire_runtime-*.whl  # should show .so files, not .py
 4. Run existing test suite: pytest tests/ — all existing tests should pass after
 import updates.