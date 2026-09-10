import json
from pathlib import Path

from despatch import _catalog, _models

STACK_STATE = _models.StackState(
    _models.StackMode.EXPLICIT,
    _models.StackSelection("studio", "studio", Path("studio.estack")),
)


class FakeGateway:
    def __init__(self, *bundles):
        self.bundles = bundles

    def loadBundles(self, stack_state):
        assert stack_state == STACK_STATE
        return self.bundles


def makeBundle(tmp_path, commands=("krita",), bundle_id="gt:creative"):
    envoy_directory = tmp_path / ".envoy"
    envoy_directory.mkdir(parents=True)
    return _models.BundleRecord(
        bundle_id=bundle_id,
        root=tmp_path,
        envoy_directory=envoy_directory,
        commands=commands,
    )


def writeManifest(bundle, applications, groups=None, suppress=None):
    manifest_data = {
        "schemaVersion": 1,
        "groups": groups or [],
        "applications": applications,
    }
    if suppress is not None:
        manifest_data["suppress"] = suppress
    (bundle.envoy_directory / "despatch.json").write_text(
        json.dumps(manifest_data),
        encoding="utf-8",
    )


def testLoadsValidManifest(tmp_path):
    bundle = makeBundle(tmp_path)
    icon_path = bundle.root / "resources" / "icons" / "krita.svg"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_text("<svg/>", encoding="utf-8")
    writeManifest(
        bundle,
        [
            {
                "id": "krita",
                "name": "Krita",
                "command": "krita",
                "description": "Paint",
                "icon": "krita.svg",
                "group": "creative",
                "keywords": ["image"],
                "platforms": ["windows"],
            }
        ],
        groups=[{"id": "creative", "name": "Creative", "order": 5}],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert snapshot.stack_state == STACK_STATE
    assert snapshot.applications[0].stable_id == "gt:creative:krita"
    assert snapshot.applications[0].icon_path == icon_path
    assert snapshot.groups[0].stable_id == "creative"
    assert not snapshot.diagnostics


def testResolvesOrganizedIconBelowResourceRoot(tmp_path):
    bundle = makeBundle(tmp_path)
    icon_path = bundle.root / "resources" / "icons" / "creative" / "krita.svg"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_text("<svg/>", encoding="utf-8")
    writeManifest(
        bundle,
        [
            {
                "id": "krita",
                "name": "Krita",
                "command": "krita",
                "icon": "creative/krita.svg",
            }
        ],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert snapshot.applications[0].icon_path == icon_path
    assert not snapshot.diagnostics


def testParsesValidHomepage(tmp_path):
    bundle = makeBundle(tmp_path)
    writeManifest(
        bundle,
        [
            {
                "id": "krita",
                "name": "Krita",
                "command": "krita",
                "homepage": "https://krita.org",
            }
        ],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert snapshot.applications[0].homepage == "https://krita.org"
    assert not snapshot.diagnostics


def testOmittedHomepageDefaultsToEmpty(tmp_path):
    bundle = makeBundle(tmp_path)
    writeManifest(bundle, [{"id": "krita", "name": "Krita", "command": "krita"}])

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert snapshot.applications[0].homepage == ""
    assert not snapshot.diagnostics


def testRejectsNonStringHomepage(tmp_path):
    bundle = makeBundle(tmp_path)
    writeManifest(
        bundle,
        [{"id": "krita", "name": "Krita", "command": "krita", "homepage": 12}],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert "'homepage' must be a string" in snapshot.diagnostics[0].message


def testRejectsNonHttpHomepage(tmp_path):
    bundle = makeBundle(tmp_path)
    writeManifest(
        bundle,
        [
            {
                "id": "krita",
                "name": "Krita",
                "command": "krita",
                "homepage": "ftp://krita.org",
            }
        ],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert "'homepage' must be an http(s) URL" in snapshot.diagnostics[0].message


def testSkipsUnknownCommandsAndWrongPlatform(tmp_path):
    bundle = makeBundle(tmp_path)
    writeManifest(
        bundle,
        [
            {"id": "missing", "name": "Missing", "command": "unknown"},
            {
                "id": "linux",
                "name": "Linux only",
                "command": "krita",
                "platforms": ["linux"],
            },
        ],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert len(snapshot.diagnostics) == 1
    assert "not available" in snapshot.diagnostics[0].message


def testRejectsIconPathEscape(tmp_path):
    bundle = makeBundle(tmp_path)
    outside_icon = tmp_path / "outside.svg"
    outside_icon.write_text("<svg/>", encoding="utf-8")
    writeManifest(
        bundle,
        [{"id": "krita", "name": "Krita", "command": "krita", "icon": "../outside.svg"}],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert snapshot.applications[0].icon_path is None
    assert "escapes the bundle resources/icons directory" in snapshot.diagnostics[0].message


def testInvalidSchemaIsRecoverable(tmp_path):
    bundle = makeBundle(tmp_path)
    manifest_path = bundle.envoy_directory / "despatch.json"
    manifest_path.write_text('{"schemaVersion": 99, "applications": []}', encoding="utf-8")

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert "schemaVersion" in snapshot.diagnostics[0].message


def testApplicationUsesGlobalGroupDeclaredByLaterBundle(tmp_path):
    application_bundle = makeBundle(
        tmp_path / "applications",
        commands=("maya",),
        bundle_id="gt:applications",
    )
    group_bundle = makeBundle(
        tmp_path / "groups",
        commands=(),
        bundle_id="gt:groups",
    )
    writeManifest(
        application_bundle,
        [{"id": "maya", "name": "Maya", "command": "maya", "group": "creative"}],
    )
    writeManifest(
        group_bundle,
        [],
        groups=[{"id": "creative", "name": "Creative", "order": 10}],
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(application_bundle, group_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert snapshot.groups[0].stable_id == "creative"
    assert snapshot.groups[0].bundle_id == "gt:groups"
    assert snapshot.applications[0].group_id == "creative"
    assert not snapshot.diagnostics


def testDuplicateGlobalGroupUsesFirstDeclarationAndLoadsApplications(tmp_path):
    first_bundle = makeBundle(
        tmp_path / "first",
        commands=("maya",),
        bundle_id="gt:first",
    )
    second_bundle = makeBundle(
        tmp_path / "second",
        commands=("houdini",),
        bundle_id="gt:second",
    )
    writeManifest(
        first_bundle,
        [{"id": "maya", "name": "Maya", "command": "maya", "group": "creative"}],
        groups=[{"id": "creative", "name": "Creative", "order": 20}],
    )
    writeManifest(
        second_bundle,
        [
            {
                "id": "houdini",
                "name": "Houdini",
                "command": "houdini",
                "group": "creative",
            }
        ],
        groups=[{"id": "creative", "name": "DCC Applications", "order": 1}],
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(first_bundle, second_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert snapshot.groups[0].name == "Creative"
    assert snapshot.groups[0].order == 20
    assert {application.group_id for application in snapshot.applications} == {"creative"}
    assert len(snapshot.diagnostics) == 1
    assert "already declared by bundle 'gt:first'" in snapshot.diagnostics[0].message


def testBundleSuppressionIsCatalogWideAndPreservesSharedGroup(tmp_path):
    suppressing_bundle = makeBundle(
        tmp_path / "team",
        commands=(),
        bundle_id="studio:team",
    )
    dependency_bundle = makeBundle(
        tmp_path / "dependency",
        commands=("legacy",),
        bundle_id="vendor:dependency",
    )
    writeManifest(
        suppressing_bundle,
        [
            {
                "id": "replacement",
                "name": "Replacement",
                "command": "legacy",
                "group": "creative",
            }
        ],
        suppress={"bundles": ["vendor:dependency"]},
    )
    writeManifest(
        dependency_bundle,
        [{"id": "legacy", "name": "Legacy", "command": "legacy", "group": "creative"}],
        groups=[{"id": "creative", "name": "Creative"}],
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(suppressing_bundle, dependency_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert [application.stable_id for application in snapshot.applications] == [
        "studio:team:replacement"
    ]
    assert snapshot.applications[0].group_id == "creative"
    assert snapshot.groups[0].bundle_id == "vendor:dependency"
    assert not snapshot.diagnostics


def testApplicationWithUndeclaredGlobalGroupIsOmitted(tmp_path):
    bundle = makeBundle(tmp_path, commands=("maya",), bundle_id="gt:applications")
    writeManifest(
        bundle,
        [{"id": "maya", "name": "Maya", "command": "maya", "group": "missing"}],
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert len(snapshot.diagnostics) == 1
    assert "group 'missing' is not declared" in snapshot.diagnostics[0].message


def testApplicationSuppressionKeepsSiblingsAndIgnoresMissingTargets(tmp_path):
    dependency_bundle = makeBundle(
        tmp_path / "dependency",
        commands=("current", "legacy"),
        bundle_id="vendor:tools",
    )
    suppressing_bundle = makeBundle(
        tmp_path / "team",
        commands=(),
        bundle_id="studio:team",
    )
    writeManifest(
        dependency_bundle,
        [
            {"id": "legacy", "name": "Legacy", "command": "legacy"},
            {"id": "current", "name": "Current", "command": "current"},
        ],
    )
    writeManifest(
        suppressing_bundle,
        [],
        suppress={
            "applications": [
                "vendor:tools:legacy",
                "vendor:optional:missing",
                "vendor:tools:legacy",
            ]
        },
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(dependency_bundle, suppressing_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert [application.stable_id for application in snapshot.applications] == [
        "vendor:tools:current"
    ]
    assert not snapshot.diagnostics


def testSelfSuppressionUsesTheSameIdentityRules(tmp_path):
    bundle = makeBundle(tmp_path, commands=("hidden",), bundle_id="gt:self")
    writeManifest(
        bundle,
        [{"id": "hidden", "name": "Hidden", "command": "hidden"}],
        suppress={"bundles": ["gt:self"]},
    )

    snapshot = _catalog.CatalogLoader(FakeGateway(bundle), "windows").loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert not snapshot.diagnostics


def testMalformedSuppressionsAreRecoverableAndValidTargetsStillApply(tmp_path):
    dependency_bundle = makeBundle(
        tmp_path / "dependency",
        commands=("legacy",),
        bundle_id="vendor:tools",
    )
    suppressing_bundle = makeBundle(
        tmp_path / "team",
        commands=(),
        bundle_id="studio:team",
    )
    writeManifest(
        dependency_bundle,
        [{"id": "legacy", "name": "Legacy", "command": "legacy"}],
    )
    writeManifest(
        suppressing_bundle,
        [],
        suppress={
            "bundles": "vendor:tools",
            "applications": ["vendor:tools:legacy", "", 42],
            "application": ["vendor:tools:legacy"],
        },
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(dependency_bundle, suppressing_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert len(snapshot.diagnostics) == 4
    messages = {diagnostic.message for diagnostic in snapshot.diagnostics}
    assert "Unknown suppress field 'application'" in messages
    assert "'suppress.bundles' must be an array" in messages
    assert "Suppress applications entry 2 must be a non-empty string" in messages
    assert "Suppress applications entry 3 must be a non-empty string" in messages


def testValidSuppressionsApplyWhenApplicationsFieldIsInvalid(tmp_path):
    dependency_bundle = makeBundle(
        tmp_path / "dependency",
        commands=("legacy",),
        bundle_id="vendor:tools",
    )
    suppressing_bundle = makeBundle(
        tmp_path / "team",
        commands=(),
        bundle_id="studio:team",
    )
    writeManifest(
        dependency_bundle,
        [{"id": "legacy", "name": "Legacy", "command": "legacy"}],
    )
    writeManifest(
        suppressing_bundle,
        "invalid",
        suppress={"bundles": ["vendor:tools"]},
    )

    snapshot = _catalog.CatalogLoader(
        FakeGateway(dependency_bundle, suppressing_bundle),
        "windows",
    ).loadCatalog(STACK_STATE)

    assert not snapshot.applications
    assert len(snapshot.diagnostics) == 1
    assert snapshot.diagnostics[0].message == "'applications' must be an array"
