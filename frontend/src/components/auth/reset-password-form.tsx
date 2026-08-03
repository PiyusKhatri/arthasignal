"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { FormField } from "@/components/auth/form-field";
import { FormError } from "@/components/auth/form-error";
import { FormSuccess } from "@/components/auth/form-success";
import { SubmitButton } from "@/components/auth/submit-button";
import { parseApiErrorMessage } from "@/lib/api-error";
import { validatePasswordsMatch, validatePasswordStrength } from "@/lib/validation";

export function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [pending, setPending] = useState(false);

  if (!token) {
    return (
      <AuthCard title="Reset password">
        <FormError message="This reset link is missing a token. Request a new one." />
        <Link href="/forgot-password" className="text-sm text-accent-text hover:text-text-primary">
          Request a new reset link
        </Link>
      </AuthCard>
    );
  }

  if (success) {
    return (
      <AuthCard title="Password reset">
        <FormSuccess message="Your password has been reset. You can now log in." />
        <Link
          href="/login"
          className="block w-full rounded-md bg-accent-primary px-4 py-2 text-center text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
        >
          Go to login
        </Link>
      </AuthCard>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const nextPasswordError = validatePasswordStrength(password);
    const nextConfirmError = nextPasswordError ? null : validatePasswordsMatch(password, confirmPassword);
    setPasswordError(nextPasswordError);
    setConfirmError(nextConfirmError);
    if (nextPasswordError || nextConfirmError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Could not reset password. Please try again."));
        return;
      }
      setSuccess(true);
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard title="Reset password">
      <form onSubmit={handleSubmit} noValidate>
        <FormError message={formError} />
        <FormField
          id="password"
          label="New password"
          type="password"
          value={password}
          onChange={setPassword}
          error={passwordError}
          autoComplete="new-password"
        />
        <FormField
          id="confirm-password"
          label="Confirm new password"
          type="password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          error={confirmError}
          autoComplete="new-password"
        />
        <p className="mb-4 text-xs text-text-secondary">
          At least 8 characters, with a letter and a digit.
        </p>
        <SubmitButton label="Reset password" pending={pending} />
      </form>
    </AuthCard>
  );
}
