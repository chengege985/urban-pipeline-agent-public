export type GasFailureKind = "contract_error" | "unavailable";

export interface GasContractIssue {
  path: string;
  expected: string;
  actual: string;
}

export interface GasApiErrorOptions {
  kind: GasFailureKind;
  code: string;
  message: string;
  status: number | null;
  requestId: string;
  path: string;
  detail?: string;
  issues?: GasContractIssue[];
}

/** Gas HTTP failures are data states, never permission to synthesize fallback data. */
export class GasApiError extends Error {
  readonly kind: GasFailureKind;
  readonly code: string;
  readonly status: number | null;
  readonly requestId: string;
  readonly path: string;
  readonly detail: string;
  readonly issues: GasContractIssue[];

  constructor(options: GasApiErrorOptions) {
    super(options.message);
    this.name = "GasApiError";
    this.kind = options.kind;
    this.code = options.code;
    this.status = options.status;
    this.requestId = options.requestId;
    this.path = options.path;
    this.detail = options.detail ?? "";
    this.issues = options.issues ?? [];
  }
}

export function isGasApiError(value: unknown): value is GasApiError {
  return value instanceof GasApiError;
}
