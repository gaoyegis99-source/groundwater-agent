// Generated validator declarations. Do not edit.
export type GeneratedValidator = ((value: unknown) => boolean) & { errors?: Array<{ instancePath?: string; keyword?: string; message?: string }> | null };
export declare function loadRequestValidator(operationId: string): Promise<GeneratedValidator | undefined>;
export declare function loadResponseValidator(operationId: string): Promise<GeneratedValidator | undefined>;
