type FastApiValidationError = { loc: (string | number)[]; msg: string; type: string };

export function parseApiErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as Partial<FastApiValidationError>;
      if (typeof first?.msg === "string") {
        return first.msg;
      }
    }
  }
  return fallback;
}
