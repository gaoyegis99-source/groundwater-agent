import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { inspectDriverPackaging } from "../skills/migrate-oracle-app/scripts/inspect-driver-packaging.mjs";

test("commented or CDATA XML cannot substantiate a runtime driver in an actual archive", async () => {
  const active = '<server><library id="postgresql-lib"><fileset dir="${server.config.dir}/resources/lib" includes="postgresql-*.jar"/></library></server>';
  const commented = `<server><!-- ${active.replace("<server>", "").replace("</server>", "")} --></server>`;
  const cdata = `<server><![CDATA[${active}]]></server>`;
  for (const [sourceDescriptor, packagedDescriptor] of [
    [commented, commented], [active, commented], [commented, active], [active, cdata],
  ]) {
    const fixture = await archiveFixture({
      externalDriver: true, warDriver: false, sourceDescriptor, packagedDescriptor,
    });
    try {
      const result = await inspectDriverPackaging(fixture.root, fixture.config);
      assert.equal(result.status, "failed");
      assert.ok(result.errors.some((error) => /fileset|reference/.test(error)));
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("deployment archive passes when descriptor runtime path contains driver", async () => {
  const fixture = await archiveFixture({ externalDriver: true, warDriver: false });
  try {
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "passed");
    assert.deepEqual(result.errors, []);
    assert.equal(result.archives[0].warOnlyDriver, false);
    assert.deepEqual(result.archives[0].archiveDriverEntries, [
      "server/resources/lib/postgresql-42.7.10.jar",
    ]);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("WAR-only PostgreSQL driver fails deployment packaging", async () => {
  const fixture = await archiveFixture({ externalDriver: false, warDriver: true });
  try {
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "failed");
    assert.equal(result.archives[0].warOnlyDriver, true);
    assert.match(result.errors.join("\n"), /only inside nested WAR/);
    assert.match(result.errors.join("\n"), /no archive entry matches/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("driver at wrong deployment path fails descriptor reconciliation", async () => {
  const fixture = await archiveFixture({
    externalDriver: true,
    warDriver: false,
    externalPath: "lib/postgresql-42.7.10.jar",
  });
  try {
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "failed");
    assert.match(result.errors.join("\n"), /no archive entry matches/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("broad JAR pattern cannot substitute an Oracle JAR for PostgreSQL", async () => {
  const fixture = await archiveFixture({
    externalDriver: false,
    externalOracle: true,
    warDriver: false,
  });
  try {
    fixture.config.archives[0].descriptors[0].runtimeDriverPatterns = [
      "server/resources/lib/*.jar",
    ];
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "failed");
    assert.match(result.errors.join("\n"), /not substantiated by descriptor fileset/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("WAR-internal runtime driver plan is rejected", async () => {
  const fixture = await archiveFixture({
    externalDriver: false,
    warDriver: true,
  });
  try {
    fixture.config.archives[0].descriptors[0].runtimeDriverPatterns = [
      "server/apps/application.war/WEB-INF/lib-provided/postgresql-42.7.10.jar",
    ];
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "failed");
    assert.match(result.errors.join("\n"), /points inside an application archive/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("stale archive fails when source descriptor no longer matches packaged descriptor", async () => {
  const fixture = await archiveFixture({
    externalDriver: true,
    warDriver: false,
  });
  try {
    await writeFile(
      path.join(fixture.root, "server.xml"),
      `<server><library id="oracle-lib"><fileset dir="\${server.config.dir}/resources/lib" includes="ojdbc8.jar"/></library></server>`,
    );
    const result = await inspectDriverPackaging(fixture.root, fixture.config);

    assert.equal(result.status, "failed");
    assert.match(result.errors.join("\n"), /source descriptor .* has no external PostgreSQL fileset/);
    assert.match(result.errors.join("\n"), /filesets differ from source/);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

async function archiveFixture({
  externalDriver,
  externalOracle = false,
  warDriver,
  externalPath = "server/resources/lib/postgresql-42.7.10.jar",
  sourceDescriptor,
  packagedDescriptor,
}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "driver-packaging-"));
  const content = path.join(root, "content");
  const warContent = path.join(root, "war-content");
  await mkdir(content, { recursive: true });
  const descriptor = `<server>
      <library id="postgresql-lib">
        <fileset dir="\${server.config.dir}/resources/lib" includes="postgresql-*.jar"/>
      </library>
      <dataSource><jdbcDriver libraryRef="postgresql-lib"/><properties.postgresql/></dataSource>
    </server>`;
  await writeFixtureFile(
    path.join(root, "server.xml"),
    sourceDescriptor ?? descriptor,
  );
  await writeFixtureFile(
    path.join(content, "server.xml"),
    packagedDescriptor ?? descriptor,
  );
  if (externalDriver) {
    await writeFixtureFile(path.join(content, ...externalPath.split("/")), "driver");
  }
  if (externalOracle) {
    await writeFixtureFile(
      path.join(content, "server", "resources", "lib", "ojdbc8.jar"),
      "oracle",
    );
  }
  await writeFixtureFile(path.join(warContent, "WEB-INF", "web.xml"), "<web-app/>");
  if (warDriver) {
    await writeFixtureFile(
      path.join(warContent, "WEB-INF", "lib-provided", "postgresql-42.7.10.jar"),
      "driver",
    );
  }
  const war = path.join(content, "server", "apps", "application.war");
  await mkdir(path.dirname(war), { recursive: true });
  execFileSync("jar", ["cf", war, "-C", warContent, "."], { stdio: "ignore" });
  const archive = path.join(root, "application.zip");
  execFileSync("jar", ["cf", archive, "-C", content, "."], {
    stdio: "ignore",
  });
  return {
    root,
    config: {
      archives: [
        {
          id: "deployment-zip",
          path: "application.zip",
          descriptors: [
            {
              sourcePath: "server.xml",
              entry: "server.xml",
              runtimeDriverPatterns: [
                "server/resources/lib/postgresql-*.jar",
              ],
            },
          ],
        },
      ],
    },
  };
}

async function writeFixtureFile(file, content) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, content);
}
