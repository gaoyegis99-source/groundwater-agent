function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

type ProfileValue = string | number | boolean | null;
export type NumericStringProfile = {
  checkedCount: number;
  parsedCount: number;
  parseSuccessRate: number;
  identifierLike: boolean;
  suggestedType?: "int64" | "float64";
};
export type TemporalProfile = {
  kind: "date" | "timestamp" | "time";
  checkedCount: number;
  parsedCount: number;
  parseSuccessRate: number | null;
  physicalTimezone?: string | null;
};
export type FieldProfileFacts = {
  nonNullCount: number;
  sampleValues: ProfileValue[];
  valueRange?: { minimum: ProfileValue; maximum: ProfileValue };
  nonFiniteCount?: number;
  numericString?: NumericStringProfile;
  temporal?: TemporalProfile;
  exactIntegers?: { sampleValues?: (string | null)[]; minimum?: string; maximum?: string };
};
export type DatasetProfile = {
  schemaVersion: "field-profile.v1";
  statisticsScope: "all_rows";
  sampleScope: "first_non_null_values";
  sampleLimitPerField: number;
};

function profileValue(value: unknown): value is ProfileValue {
  return value === null
    || typeof value === "string"
    || typeof value === "boolean"
    || (typeof value === "number" && Number.isFinite(value));
}

function unsafeInteger(value: unknown): boolean {
  return typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value);
}

function validExactInteger(text: unknown, value: unknown): boolean {
  if (typeof text !== "string" || !/^-?[1-9]\d{15,19}$/.test(text)
    || !unsafeInteger(value) || Number(text) !== value) return false;
  const exact = BigInt(text);
  return exact >= -(2n ** 63n) && exact <= 2n ** 64n - 1n;
}

function validExactIntegers(value: unknown, samples: unknown[], range: unknown): boolean {
  if (!record(value)) return false;
  const exactSamples = value.sampleValues;
  if (exactSamples !== undefined && (!Array.isArray(exactSamples) || exactSamples.length !== samples.length
    || !exactSamples.every((text, index) => unsafeInteger(samples[index])
      ? validExactInteger(text, samples[index]) : text === null))) return false;
  return ["minimum", "maximum"].every(key => value[key] === undefined
    || (record(range) && validExactInteger(value[key], range[key])));
}

export function formatFieldProfileValue(value: ProfileValue, fieldType: string, exact?: string | null): string {
  if (exact != null) return exact;
  if ((fieldType === "int64" || fieldType === "uint64") && unsafeInteger(value)) return "精度不可确认";
  return value === null ? "—" : String(value);
}

export function validDatasetProfile(value: unknown): value is DatasetProfile {
  return record(value)
    && value.schemaVersion === "field-profile.v1"
    && value.statisticsScope === "all_rows"
    && value.sampleScope === "first_non_null_values"
    && count(value.sampleLimitPerField)
    && value.sampleLimitPerField > 0
    && value.sampleLimitPerField <= 20;
}

function validRate(value: unknown, parsed: number, checked: number): boolean {
  if (checked === 0) return value === null;
  return typeof value === "number"
    && Number.isFinite(value)
    && value >= 0
    && value <= 1
    && Math.abs(value - parsed / checked) <= 0.000001;
}

function validNumericProfile(value: unknown): boolean {
  if (!record(value)
    || !count(value.checkedCount)
    || !count(value.parsedCount)
    || value.parsedCount > value.checkedCount
    || typeof value.identifierLike !== "boolean"
    || !validRate(value.parseSuccessRate, value.parsedCount, value.checkedCount)) return false;
  return value.suggestedType === undefined
    || value.suggestedType === "int64"
    || value.suggestedType === "float64";
}

function validTemporalProfile(value: unknown): boolean {
  if (!record(value)
    || !["date", "timestamp", "time"].includes(String(value.kind))
    || !count(value.checkedCount)
    || !count(value.parsedCount)
    || value.parsedCount > value.checkedCount
    || !validRate(value.parseSuccessRate, value.parsedCount, value.checkedCount)) return false;
  return value.physicalTimezone === undefined
    || value.physicalTimezone === null
    || typeof value.physicalTimezone === "string";
}

export function validFieldProfile(
  value: unknown,
  sampleLimit: number,
  rowCount: number | null,
  nullCount: number,
): boolean {
  if (!record(value)
    || !count(value.nonNullCount)
    || !Array.isArray(value.sampleValues)
    || value.sampleValues.length > sampleLimit
    || !value.sampleValues.every(profileValue)
    || (rowCount !== null && value.nonNullCount + nullCount !== rowCount)) return false;
  if (value.valueRange !== undefined && (!record(value.valueRange)
    || !profileValue(value.valueRange.minimum)
    || !profileValue(value.valueRange.maximum))) return false;
  if (value.nonFiniteCount !== undefined
    && (!count(value.nonFiniteCount) || value.nonFiniteCount > value.nonNullCount)) return false;
  if (value.numericString !== undefined && !validNumericProfile(value.numericString)) return false;
  if (value.exactIntegers !== undefined && !validExactIntegers(value.exactIntegers, value.sampleValues, value.valueRange)) return false;
  return value.temporal === undefined || validTemporalProfile(value.temporal);
}
