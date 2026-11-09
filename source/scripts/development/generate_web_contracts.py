"""Generate or check Web clients for one selected assembled product."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.development.generate_public_api_contract import (
    ROOT,
    _load_product,
    _output_path,
    encoded_contract,
)


WEB = ROOT / "web"


def _typescript(source: Path, output: Path) -> None:
    subprocess.run(
        [
            "npx",
            "--no-install",
            "openapi-typescript",
            str(source),
            "-o",
            str(output),
        ],
        cwd=WEB,
        check=True,
    )


def _standalone_ui_validator(schema: Path) -> bytes:
    """Compile the UI schema ahead of time so the browser never needs unsafe-eval."""

    script = """
import fs from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import standaloneCode from 'ajv/dist/standalone/index.js';
const schema = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const ajv = new Ajv2020({ allErrors: true, strict: true, code: { source: true, esm: true } });
const validate = ajv.compile(schema);
const standalone = standaloneCode(ajv, validate).replace(
  'const func2 = require("ajv/dist/runtime/ucs2length").default;',
  'const func2 = (value) => [...value].length;',
);
if (standalone.includes('require(')) throw new Error('UI_VALIDATOR_RUNTIME_DEPENDENCY');
process.stdout.write('// Generated from platform/assembler/product-ui.v1.schema.json. Do not edit.\\n');
process.stdout.write(standalone);
process.stdout.write('\\n');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(schema)],
        cwd=WEB,
        check=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def _rewrite_openapi_refs(value):
    if isinstance(value, dict):
        return {
            key: (
                item.replace("#/components/schemas/", "#/$defs/")
                if key == "$ref" and isinstance(item, str)
                else _rewrite_openapi_refs(item)
            )
            for key, item in value.items()
            # ``discriminator`` is OpenAPI routing metadata rather than a
            # JSON Schema assertion.  The generated oneOf branches already
            # carry their literal ``kind`` constraints, so retaining it would
            # make strict Ajv reject an otherwise fully checkable schema.
            if key != "discriminator"
        }
    if isinstance(value, list):
        return [_rewrite_openapi_refs(item) for item in value]
    return value


def _operation_schemas(
    contract: Path,
) -> tuple[dict[str, dict], dict[str, dict], list[str], list[str]]:
    document = json.loads(contract.read_text(encoding="utf-8"))
    definitions = _rewrite_openapi_refs(document.get("components", {}).get("schemas", {}))
    schemas: dict[str, dict] = {}
    request_names: list[str] = []
    response_names: list[str] = []
    for path in document.get("paths", {}).values():
        for operation in path.values():
            if not isinstance(operation, dict) or not operation.get("operationId"):
                continue
            operation_id = operation["operationId"]
            body = operation.get("requestBody", {}).get("content", {}).get(
                "application/json", {}
            ).get("schema")
            if body:
                name = f"request_{operation_id}"
                schemas[name] = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    **_rewrite_openapi_refs(body),
                }
                request_names.append(operation_id)
            responses = []
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2"):
                    continue
                schema = response.get("content", {}).get("application/json", {}).get(
                    "schema"
                )
                if schema:
                    responses.append(_rewrite_openapi_refs(schema))
            if responses:
                name = f"response_{operation_id}"
                root = responses[0] if len(responses) == 1 else {"anyOf": responses}
                schemas[name] = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    **root,
                }
                response_names.append(operation_id)
    # Keeping one shared definition registry avoids embedding the complete
    # OpenAPI schema tree into every standalone operation validator.  The old
    # layout produced a multi-megabyte browser chunk and made every operation
    # pay for definitions it never validates.
    shared_id = "urn:platform-ui-validator:definitions"
    for schema in schemas.values():
        serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        serialized = serialized.replace("#/$defs/", f"{shared_id}#/$defs/")
        schema.clear()
        schema.update(json.loads(serialized))
    return schemas, {"$id": shared_id, "$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": definitions}, request_names, response_names


def _standalone_operation_validators(
    contract: Path,
) -> tuple[bytes, bytes]:
    schemas, definitions, request_names, response_names = _operation_schemas(contract)
    script = r"""
import fs from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import standaloneCode from 'ajv/dist/standalone/index.js';
const schemas = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const ajv = new Ajv2020({
  allErrors: true,
  strict: true,
  validateFormats: false,
  code: { source: true, esm: true },
});
const definitions = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
ajv.addSchema(definitions);
const names = {};
for (const [name, schema] of Object.entries(schemas)) {
  schema.$id = `urn:platform-ui-validator:${name}`;
  ajv.addSchema(schema);
  names[name] = schema.$id;
}
let source = standaloneCode(ajv, names).replace(
  /const (func\d+) = require\("ajv\/dist\/runtime\/ucs2length"\)\.default;/g,
  'const $1 = (value) => [...value].length;',
);
if (source.includes('require(')) throw new Error('VALIDATOR_RUNTIME_DEPENDENCY');
process.stdout.write('// Generated from OpenAPI. Do not edit.\n');
process.stdout.write(source);
"""
    with tempfile.TemporaryDirectory(prefix="platform-operation-schema-") as directory:
        source = Path(directory) / "schemas.json"
        definitions_source = Path(directory) / "definitions.json"
        source.write_text(
            json.dumps(schemas, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        definitions_source.write_text(
            json.dumps(definitions, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(source), str(definitions_source)],
            cwd=WEB,
            check=True,
            stdout=subprocess.PIPE,
        )
    js = result.stdout + (
        "\nexport const requestValidators = {\n"
        + "".join(
            f"  {json.dumps(name)}: request_{name},\n" for name in request_names
        )
        + "};\nexport const responseValidators = {\n"
        + "".join(
            f"  {json.dumps(name)}: response_{name},\n" for name in response_names
        )
        + "};\n"
    ).encode("utf-8")
    declarations = (
        "// Generated validator declarations. Do not edit.\n"
        "export type GeneratedValidator = ((value: unknown) => boolean) & "
        "{ errors?: Array<{ instancePath?: string; keyword?: string; message?: string }> | null };\n"
        "export declare const requestValidators: Readonly<Record<string, GeneratedValidator>>;\n"
        "export declare const responseValidators: Readonly<Record<string, GeneratedValidator>>;\n"
    ).encode("utf-8")
    return js, declarations


def _standalone_split_operation_validators(
    contract: Path,
) -> tuple[dict[str, bytes], bytes, bytes]:
    """Generate lazy operation validators for the public Web shell.

    The aggregate validator is retained for contract checks, but loading it in
    the initial browser graph makes a generated OpenAPI file the largest
    application chunk.  One standalone validator per operation lets the API
    client load only the contract it is about to use.
    """
    schemas, definitions, request_names, response_names = _operation_schemas(contract)
    script = r"""
import fs from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import standaloneCode from 'ajv/dist/standalone/index.js';
const schemas = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const definitions = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const target = process.argv[3];
const ajv = new Ajv2020({
  allErrors: true,
  strict: true,
  validateFormats: false,
  code: { source: true, esm: true },
});
ajv.addSchema(definitions);
const schema = schemas[target];
if (!schema) throw new Error(`UNKNOWN_VALIDATOR:${target}`);
schema.$id = `urn:platform-ui-validator:${target}`;
ajv.addSchema(schema);
let source = standaloneCode(ajv, { [target]: schema.$id }).replace(
  /const (func\d+) = require\("ajv\/dist\/runtime\/ucs2length"\)\.default;/g,
  'const $1 = (value) => [...value].length;',
);
if (source.includes('require(')) throw new Error('VALIDATOR_RUNTIME_DEPENDENCY');
process.stdout.write('// Generated from OpenAPI. Do not edit.\n');
process.stdout.write(source);
"""
    declarations = (
        "// Generated validator declarations. Do not edit.\n"
        "export type GeneratedValidator = ((value: unknown) => boolean) & "
        "{ errors?: Array<{ instancePath?: string; keyword?: string; message?: string }> | null };\n"
        "export declare function loadRequestValidator(operationId: string): Promise<GeneratedValidator | undefined>;\n"
        "export declare function loadResponseValidator(operationId: string): Promise<GeneratedValidator | undefined>;\n"
    ).encode("utf-8")
    files: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="platform-operation-schema-") as directory:
        source_path = Path(directory) / "schemas.json"
        definitions_path = Path(directory) / "definitions.json"
        source_path.write_text(json.dumps(schemas, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        definitions_path.write_text(json.dumps(definitions, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        for validator_name in [f"request_{name}" for name in request_names] + [f"response_{name}" for name in response_names]:
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script, str(source_path), str(definitions_path), validator_name],
                cwd=WEB,
                check=True,
                stdout=subprocess.PIPE,
            )
            files[f"{validator_name}.js"] = result.stdout
    request_entries = "\n".join(
        f"  {json.dumps(name)}: () => import('./public-api-v12.validators/{'request_' + name}.js').then(module => module.request_{name}),"
        for name in request_names
    )
    response_entries = "\n".join(
        f"  {json.dumps(name)}: () => import('./public-api-v12.validators/{'response_' + name}.js').then(module => module.response_{name}),"
        for name in response_names
    )
    registry = (
        "// Generated from OpenAPI. Do not edit.\n"
        "const requestLoaders = {\n" + request_entries + "\n};\n"
        "const responseLoaders = {\n" + response_entries + "\n};\n"
        "export function loadRequestValidator(operationId) { return requestLoaders[operationId]?.(); }\n"
        "export function loadResponseValidator(operationId) { return responseLoaders[operationId]?.(); }\n"
    ).encode("utf-8")
    return files, registry, declarations


def _write_or_check(path: Path, content: bytes, *, check: bool, code: str) -> None:
    if check:
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"{code}:{path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def run(product: str | None, *, check: bool) -> None:
    product_id, manifest = _load_product(ROOT, product)
    public_contract = _output_path(ROOT, product_id, manifest)
    content = encoded_contract(ROOT, product_id)
    if check:
        if not public_contract.is_file() or public_contract.read_bytes() != content:
            raise ValueError("PUBLIC_API_CONTRACT_STALE")
    else:
        public_contract.parent.mkdir(parents=True, exist_ok=True)
        public_contract.write_bytes(content)

    ui_schema = ROOT / "platform/assembler/product-ui.v1.schema.json"
    generated_ui_schema = WEB / "src/generated/product-ui.v1.schema.ts"
    generated_ui_validator = WEB / "src/generated/product-ui.v1.validator.js"
    generated_ui_validator_types = WEB / "src/generated/product-ui.v1.validator.d.ts"
    ui_schema_module = (
        "// Generated from platform/assembler/product-ui.v1.schema.json.\n"
        "export default " + ui_schema.read_text(encoding="utf-8").rstrip() + " as const;\n"
    ).encode("utf-8")
    ui_validator = _standalone_ui_validator(ui_schema)
    ui_validator_types = (
        "// Generated validator declaration. Do not edit.\n"
        "declare function validate(value: unknown): boolean;\n"
        "export { validate };\n"
        "export default validate;\n"
    ).encode("utf-8")
    if check:
        if (
            not generated_ui_schema.is_file()
            or generated_ui_schema.read_bytes() != ui_schema_module
        ):
            raise ValueError("WEB_PRODUCT_UI_SCHEMA_STALE")
        if (
            not generated_ui_validator.is_file()
            or generated_ui_validator.read_bytes() != ui_validator
            or not generated_ui_validator_types.is_file()
            or generated_ui_validator_types.read_bytes() != ui_validator_types
        ):
            raise ValueError("WEB_PRODUCT_UI_VALIDATOR_STALE")
    else:
        generated_ui_schema.write_bytes(ui_schema_module)
        generated_ui_validator.write_bytes(ui_validator)
        generated_ui_validator_types.write_bytes(ui_validator_types)

    clients = (
        (
            public_contract,
            WEB / "src/generated/public-api-v12.ts",
            WEB / "src/generated/public-api-v12.validators.js",
        ),
        (
            ROOT / "services/data/contracts/openapi-v3.json",
            ROOT / "services/data/web/src/generated/data-api-v3.ts",
            ROOT / "services/data/web/src/generated/data-api-v3.validators.js",
        ),
        (
            ROOT / "services/knowledge-next/contracts/openapi-v3.json",
            ROOT / "services/knowledge-next/ui/src/generated/knowledge-api-v3.ts",
            ROOT
            / "services/knowledge-next/ui/src/generated/knowledge-api-v3.validators.js",
        ),
        (
            ROOT / "services/data/contracts/openapi-v3.json",
            ROOT / "services/knowledge-next/ui/src/generated/data-api-v3.ts",
            ROOT / "services/knowledge-next/ui/src/generated/data-api-v3.validators.js",
        ),
    )
    split_validator_dir = WEB / "src/generated/public-api-v12.validators"
    split_validator_registry = WEB / "src/generated/public-api-v12.validators.lazy.js"
    split_validator_declarations = split_validator_registry.with_suffix(".d.ts")
    split_files, split_registry, split_declarations = _standalone_split_operation_validators(public_contract)
    existing_split_files = (
        {path.name for path in split_validator_dir.glob("*.js")}
        if split_validator_dir.is_dir()
        else set()
    )
    stale_split_files = existing_split_files - set(split_files)
    if check and stale_split_files:
        raise ValueError(
            "WEB_OPERATION_VALIDATOR_SPLIT_STALE:"
            + ",".join(sorted(stale_split_files))
        )
    if not check:
        for name in stale_split_files:
            (split_validator_dir / name).unlink()
    # The aggregate file remains a deterministic contract artifact for the
    # existing generator check.  The browser imports this small registry and
    # lazy operation modules instead.
    _write_or_check(
        split_validator_registry,
        split_registry,
        check=check,
        code="WEB_OPERATION_VALIDATOR_REGISTRY_STALE",
    )
    _write_or_check(
        split_validator_declarations,
        split_declarations,
        check=check,
        code="WEB_OPERATION_VALIDATOR_REGISTRY_TYPES_STALE",
    )
    for name, content in split_files.items():
        _write_or_check(
            split_validator_dir / name,
            content,
            check=check,
            code="WEB_OPERATION_VALIDATOR_SPLIT_STALE",
        )
    if not check:
        for source, output, validators in clients:
            _typescript(source, output)
            validator_source, declarations = _standalone_operation_validators(source)
            _write_or_check(
                validators,
                validator_source,
                check=False,
                code="WEB_OPERATION_VALIDATOR_STALE",
            )
            _write_or_check(
                validators.with_suffix(".d.ts"),
                declarations,
                check=False,
                code="WEB_OPERATION_VALIDATOR_TYPES_STALE",
            )
        return
    with tempfile.TemporaryDirectory(prefix="platform-web-contracts-") as directory:
        temporary = Path(directory)
        for index, (source, expected, validators) in enumerate(clients):
            generated = temporary / f"client-{index}.ts"
            _typescript(source, generated)
            if not expected.is_file() or expected.read_bytes() != generated.read_bytes():
                raise ValueError(f"WEB_CONTRACT_CLIENT_STALE:{expected}")
            validator_source, declarations = _standalone_operation_validators(source)
            _write_or_check(
                validators,
                validator_source,
                check=True,
                code="WEB_OPERATION_VALIDATOR_STALE",
            )
            _write_or_check(
                validators.with_suffix(".d.ts"),
                declarations,
                check=True,
                code="WEB_OPERATION_VALIDATOR_TYPES_STALE",
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    run(args.product, check=args.check)


if __name__ == "__main__":
    main()
