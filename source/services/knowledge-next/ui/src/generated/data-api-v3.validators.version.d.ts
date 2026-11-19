// Generated validator declarations. Do not edit.
export type GeneratedValidator = ((value: unknown) => boolean) & { errors?: Array<{ instancePath?: string; keyword?: string; message?: string }> | null };
export declare const requestValidators: Readonly<Record<string, GeneratedValidator>>;
export declare const responseValidators: Readonly<Record<string, GeneratedValidator>>;
