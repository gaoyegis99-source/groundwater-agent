// Generated from platform/assembler/product-ui.v1.schema.json.
export default {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:platform:schema:product-ui.v1",
  "title": "Product UI Profile v1",
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion", "productId", "locale", "brand", "examples", "models", "parameterNames", "parameterStages", "outputs", "cases"],
  "properties": {
    "schemaVersion": {"const": "product-ui.v1"},
    "productId": {"$ref": "#/$defs/text"},
    "locale": {"type": "string", "pattern": "^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"},
    "workspaceLayout": {"enum": ["standard", "research"]},
    "theme": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "ink": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "muted": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "canvas": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "surface-soft": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "line": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "accent": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "accent-active": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "accent-soft": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "radius-control": {"type": "string", "pattern": "^[0-9]{1,2}px$"},
        "radius-panel": {"type": "string", "pattern": "^[0-9]{1,2}px$"}
      }
    },
    "brand": {
      "type": "object",
      "additionalProperties": false,
      "required": ["productName", "workspaceName", "assistantName", "welcome", "icon"],
      "properties": {
        "productName": {"$ref": "#/$defs/text"},
        "workspaceName": {"$ref": "#/$defs/text"},
        "assistantName": {"$ref": "#/$defs/text"},
        "welcome": {"$ref": "#/$defs/text"},
        "icon": {
          "type": "object",
          "additionalProperties": false,
          "required": ["path", "checksum", "mediaType"],
          "properties": {
            "path": {"const": "product-icon.svg"},
            "checksum": {"$ref": "#/$defs/checksum"},
            "mediaType": {"const": "image/svg+xml"}
          }
        }
      }
    },
    "examples": {
      "type": "array",
      "minItems": 1,
      "maxItems": 12,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "title", "prompt", "icon", "featured"],
        "properties": {
          "id": {"$ref": "#/$defs/text"},
          "title": {"$ref": "#/$defs/text"},
          "prompt": {"$ref": "#/$defs/text"},
          "icon": {"enum": ["chart", "trend", "flow", "coverage", "compare", "map", "layers", "branch"]},
          "featured": {"type": "boolean"}
        }
      }
    },
    "businessCapabilities": {
      "type": "array",
      "minItems": 1,
      "items": {"$ref": "#/$defs/businessCapability"}
    },
    "models": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {"$ref": "#/$defs/model"}
    },
    "parameterNames": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/parameter"}
    },
    "parameterStages": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/parameterStage"}
    },
    "quantityKindLabels": {
      "type": "object",
      "propertyNames": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{0,127}$"},
      "additionalProperties": {"$ref": "#/$defs/text"}
    },
    "outputs": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/output"}
    },
    "cases": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/case"}
    }
  },
  "$defs": {
    "text": {"type": "string", "minLength": 1, "maxLength": 1000, "pattern": "\\S"},
    "checksum": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    "businessName": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"},
    "businessRef": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "version", "checksum"],
      "properties": {
        "id": {"$ref": "#/$defs/businessName"},
        "version": {"type": "string", "pattern": "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"},
        "checksum": {"$ref": "#/$defs/checksum"}
      }
    },
    "businessRoles": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {"type": "string", "enum": ["admin", "researcher", "viewer"]}
    },
    "businessCapability": {
      "type": "object",
      "additionalProperties": false,
      "required": ["ref", "title", "description", "scenario", "tier", "defaultVisible", "roles", "prompt", "requiredServices", "validationStatus", "profiles"],
      "properties": {
        "ref": {"$ref": "#/$defs/businessRef"},
        "title": {"$ref": "#/$defs/text"},
        "description": {"$ref": "#/$defs/text"},
        "scenario": {"$ref": "#/$defs/text"},
        "tier": {"enum": ["CORE", "SPECIALIZED", "ADVANCED", "EXPERIMENTAL"]},
        "defaultVisible": {"type": "boolean"},
        "roles": {"$ref": "#/$defs/businessRoles"},
        "exampleId": {"$ref": "#/$defs/text"},
        "prompt": {"$ref": "#/$defs/text"},
        "requiredServices": {"type": "array", "items": {"$ref": "#/$defs/businessName"}},
        "validationStatus": {"enum": ["TECHNICALLY_VALIDATED", "UNVERIFIED"]},
        "profiles": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/businessProfile"}}
      }
    },
    "businessProfile": {
      "type": "object",
      "additionalProperties": false,
      "required": ["profileRef", "modelRef", "workflowRef", "resultContractRef", "label", "roles", "inputs", "outputs", "limitations"],
      "properties": {
        "profileRef": {"$ref": "#/$defs/businessRef"},
        "modelRef": {"$ref": "#/$defs/businessRef"},
        "workflowRef": {"$ref": "#/$defs/businessRef"},
        "resultContractRef": {"$ref": "#/$defs/businessRef"},
        "label": {"$ref": "#/$defs/text"},
        "roles": {"$ref": "#/$defs/businessRoles"},
        "inputs": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/businessInput"}},
        "outputs": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/businessOutput"}},
        "limitations": {"type": "array", "items": {"$ref": "#/$defs/text"}}
      }
    },
    "businessInput": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "meaning", "formats", "preconditions"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "meaning": {"$ref": "#/$defs/text"},
        "formats": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/text"}},
        "preconditions": {"type": "array", "items": {"$ref": "#/$defs/text"}}
      }
    },
    "businessOutput": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name", "role", "meaning", "format"],
      "properties": {
        "name": {"$ref": "#/$defs/businessName"},
        "role": {"enum": ["PRIMARY_RESULT", "DIAGNOSTIC", "VISUALIZATION", "SUMMARY"]},
        "meaning": {"$ref": "#/$defs/text"},
        "format": {"$ref": "#/$defs/text"},
        "mediaType": {"$ref": "#/$defs/text"},
        "unit": {"$ref": "#/$defs/text"},
        "qualitySource": {
          "type": "object",
          "additionalProperties": false,
          "required": ["outputName", "fields"],
          "properties": {
            "outputName": {"$ref": "#/$defs/businessName"},
            "fields": {
              "type": "array", "minItems": 1, "uniqueItems": true,
              "items": {"type": "string", "$ref": "#/$defs/text"}
            }
          }
        }
      }
    },
    "stringMap": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/text"}
    },
    "parameter": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "group"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "group": {"$ref": "#/$defs/text"},
        "unit": {"$ref": "#/$defs/text"},
        "values": {"$ref": "#/$defs/stringMap"}
      }
    },
    "parameterStage": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "group"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "group": {"$ref": "#/$defs/text"}
      }
    },
    "input": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "description", "formats"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "description": {"$ref": "#/$defs/text"},
        "formats": {
          "type": "array",
          "minItems": 1,
          "items": {"$ref": "#/$defs/text"}
        }
      }
    },
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "parameters"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "parameters": {
          "type": "array",
          "minItems": 1,
          "items": {"$ref": "#/$defs/text"}
        }
      }
    },
    "model": {
      "type": "object",
      "additionalProperties": false,
      "required": ["displayName", "description", "applicability", "inputs", "parameterOverrides", "confirmationSummary"],
      "properties": {
        "displayName": {"$ref": "#/$defs/text"},
        "description": {"$ref": "#/$defs/text"},
        "applicability": {"$ref": "#/$defs/text"},
        "inputs": {"type": "object", "additionalProperties": {"$ref": "#/$defs/input"}},
        "parameterOverrides": {"type": "object", "additionalProperties": {"$ref": "#/$defs/parameter"}},
        "confirmationSummary": {"type": "array", "items": {"$ref": "#/$defs/summary"}},
        "resultOverview": {"$ref": "#/$defs/resultOverview"},
        "continuation": {"$ref": "#/$defs/continuation"},
        "inputCaseTag": {"$ref": "#/$defs/text"},
        "publishedOutputStep": {
          "type": "object",
          "additionalProperties": false,
          "required": ["output", "targetModel", "targetInput", "arguments"],
          "properties": {
            "output": {"$ref": "#/$defs/text"},
            "targetModel": {"$ref": "#/$defs/text"},
            "targetInput": {"$ref": "#/$defs/text"},
            "arguments": {"type": "object"}
          }
        }
      }
    },
    "continuation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["output", "targetModel", "targetInput", "label"],
      "properties": {
        "output": {"$ref": "#/$defs/text"},
        "targetModel": {"$ref": "#/$defs/text"},
        "targetInput": {"$ref": "#/$defs/text"},
        "label": {"$ref": "#/$defs/text"},
        "reviewReport": {"$ref": "#/$defs/text"}
      }
    },
    "resultOverview": {
      "type": "object",
      "additionalProperties": false,
      "required": ["description", "sections"],
      "properties": {
        "description": {"$ref": "#/$defs/text"},
        "regressionMetrics": {"$ref": "#/$defs/text"},
        "insight": {
          "type": "object",
          "additionalProperties": false,
          "required": ["kind", "output"],
          "properties": {
            "kind": {"enum": ["darcy", "kriging", "drastic", "calibration", "trend", "map", "statistics"]},
            "output": {"$ref": "#/$defs/text"},
            "schemaVersion": {"$ref": "#/$defs/text"},
            "categoricalVisual": {"$ref": "#/$defs/text"}
          }
        },
        "visuals": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/text"}},
        "sections": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["label", "outputs"],
            "properties": {
              "label": {"$ref": "#/$defs/text"},
              "outputs": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/text"}}
            }
          }
        }
      }
    },
    "output": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "viewer"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "viewer": {"enum": ["auto", "table", "chart", "map", "image", "json", "metadata"]}
      }
    },
    "case": {
      "type": "object",
      "additionalProperties": false,
      "required": ["label", "profiles"],
      "properties": {
        "label": {"$ref": "#/$defs/text"},
        "profiles": {"$ref": "#/$defs/stringMap"}
      }
    }
  }
} as const;
