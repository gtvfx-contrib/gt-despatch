# Bundle Manifests

An Envoy bundle opts applications into Despatch by adding
`.envoy/despatch.json`. Envoy commands not listed in this manifest remain
available from the command line but do not appear in the launcher.

## Example

```json
{
  "schemaVersion": 1,
  "groups": [
    {
      "id": "creative",
      "name": "Creative",
      "order": 10
    }
  ],
  "suppress": {
    "bundles": ["vendor:legacy-launchers"],
    "applications": ["vendor:render-tools:legacy-monitor"]
  },
  "applications": [
    {
      "id": "krita",
      "name": "Krita",
      "command": "krita",
      "args": ["--new-instance"],
      "description": "Digital painting and illustration",
      "icon": "creative/krita.svg",
      "group": "creative",
      "keywords": ["paint", "image", "concept"],
      "platforms": ["windows", "linux"],
      "inTerminal": false,
      "homepage": "https://krita.org"
    }
  ]
}
```

## Top-level fields

| Field | Required | Description |
|---|---|---|
| `schemaVersion` | Yes | Must currently be `1` |
| `groups` | No | Stack-wide display sections declared by this bundle |
| `suppress` | No | Bundle and application identities to hide from Despatch |
| `applications` | Yes | Applications exposed to Despatch |

## Groups

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Non-empty identifier unique across the active Stack |
| `name` | Yes | Display label |
| `order` | No | Numeric sort order; defaults to array position |

Groups are shared by every manifest in the active Stack. A group is declared
once, then applications from any bundle can reference its ID. The declaring
manifest may appear before or after those applications in Stack order because
Despatch registers all groups before parsing applications.

For example, a central bundle can define the studio's sections:

```json
{
  "schemaVersion": 1,
  "groups": [
    {"id": "creative", "name": "Creative", "order": 10},
    {"id": "development", "name": "Development", "order": 20}
  ],
  "applications": []
}
```

Another bundle can contribute applications without repeating those group
definitions:

```json
{
  "schemaVersion": 1,
  "applications": [
    {
      "id": "houdini",
      "name": "Houdini",
      "command": "houdini",
      "group": "creative"
    }
  ]
}
```

If multiple manifests declare the same group ID, the first valid declaration
in Stack order supplies its name and order. Later declarations are ignored with
a warning, but their valid applications continue loading and reference the
first group.

## Suppression

Suppression lets a team keep a dependency bundle in the Envoy Stack while
hiding some or all of its Despatch applications:

```json
{
  "schemaVersion": 1,
  "suppress": {
    "bundles": [
      "vendor:dcc-tools"
    ],
    "applications": [
      "vendor:render-tools:legacy-monitor"
    ]
  },
  "applications": []
}
```

| Field | Target identity | Effect |
|---|---|---|
| `suppress.bundles` | Exact Envoy bundle ID, such as `vendor:dcc-tools` | Hides every application contributed by the bundle |
| `suppress.applications` | `<bundle-id>:<application-id>` | Hides only the exact application |

Suppressions are collected from every valid manifest and apply to the entire
catalog regardless of Stack order. Duplicate targets are harmless, and targets
that are not present in a particular Stack are silent no-ops. A manifest can
suppress its own entries, although the usual use is a team override suppressing
entries from dependency bundles.

Suppression affects only Despatch applications. It does not remove the Envoy
bundle, its commands, or groups declared by its manifest. Other bundles can
therefore continue contributing applications to a shared group declared by a
suppressed bundle.

The `applications` field remains required. A manifest used only for shared
groups or suppression declares an empty array.

## Applications

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Non-empty identifier unique within the manifest |
| `name` | Yes | User-facing display name |
| `command` | Yes | Command available somewhere in the active Envoy Stack |
| `args` | No | Array of command arguments |
| `description` | No | Secondary text shown by the launcher |
| `icon` | No | Relative file below the bundle's `resources/icons` directory |
| `group` | No | Stack-wide group `id` declared by any active manifest |
| `keywords` | No | Additional search terms |
| `platforms` | No | Any of `windows`, `macos`, or `linux` |
| `inTerminal` | No | Launch in a new console; defaults to `false` |
| `homepage` | No | `http://` or `https://` URL opened by the "Open homepage" action |

Application identity combines the bundle ID and application ID. This makes
favorites and history stable across refreshes while avoiding collisions
between bundles.

## Icons and path safety

`icon: krita.svg` resolves to `resources/icons/krita.svg` at the bundle root;
`icon: creative/krita.svg` resolves below that directory. Absolute paths,
missing files, and paths escaping `resources/icons` are rejected.

SVG and common raster formats supported by the active Qt binding can be used.
Provide a square icon that remains legible at 34 × 34 pixels.

## Validation and diagnostics

Malformed manifests do not prevent Despatch from starting. Valid entries from
the same or other bundles remain available. Despatch writes precise diagnostics
to its log and summarizes the issue count in the launcher.

An application is omitted when its command is unavailable, its platform does
not match, its group is undeclared, required fields are invalid, or it matches
a valid suppression. Missing suppression targets do not produce diagnostics.

Malformed suppression fields are recoverable: Despatch warns about invalid
containers, arrays, entries, and unknown fields while retaining valid sibling
directives and applications.

Bundle command and environment definitions remain Envoy configuration. See
[Envoy Core Concepts](https://gtvfx-envoy.github.io/envoy/concepts/).
