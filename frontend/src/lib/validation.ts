export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_BYTES = 72;

export function validateEmail(email: string): string | null {
  const trimmed = email.trim();
  if (!trimmed) {
    return "Email is required";
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
    return "Enter a valid email address";
  }
  return null;
}

export function validatePasswordPresence(password: string): string | null {
  return password.length === 0 ? "Password is required" : null;
}

export function validatePasswordStrength(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
    return `Password must not exceed ${MAX_PASSWORD_BYTES} bytes`;
  }
  if (!/[A-Za-z]/.test(password)) {
    return "Password must contain at least one letter";
  }
  if (!/[0-9]/.test(password)) {
    return "Password must contain at least one digit";
  }
  return null;
}

export function validatePasswordsMatch(password: string, confirmPassword: string): string | null {
  return password !== confirmPassword ? "Passwords do not match" : null;
}

export function validateSymbol(symbol: string): string | null {
  return symbol.trim().length === 0 ? "Symbol is required" : null;
}

export function validatePositiveNumber(value: string, label: string): string | null {
  if (value.trim().length === 0) {
    return `${label} is required`;
  }
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) {
    return `${label} must be a positive number`;
  }
  return null;
}

export function validateDate(value: string): string | null {
  if (value.trim().length === 0) {
    return "Purchase date is required";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Enter a valid date";
  }
  if (date.getTime() > Date.now()) {
    return "Purchase date cannot be in the future";
  }
  return null;
}
