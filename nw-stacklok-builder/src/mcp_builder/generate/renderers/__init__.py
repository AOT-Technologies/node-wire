"""Pure renderers: ServerPlan -> string.

Each module in this package exposes a single ``render_*`` function that
takes a ``ServerPlan`` and returns file content (source code or YAML) as
a string, with no filesystem side effects.

Modules:
    client    — render_client_module() (textwrap.dedent)
    manifests — render_manifests()     (yaml.dump, ToolHive deployment CRDs)
    tools     — render_tools_module()  (Jinja2)
    escape    — shared string-escaping helpers

Related pieces that are NOT pure renderers and live as siblings of this
package:
    ../scaffold.py — copies and renames the template on disk
    ../patches.py  — rewrites scaffolded template files in place
"""
