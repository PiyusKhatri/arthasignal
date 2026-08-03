"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { FormField } from "@/components/auth/form-field";
import { FormError } from "@/components/auth/form-error";
import { FormSuccess } from "@/components/auth/form-success";
import { SubmitButton } from "@/components/auth/submit-button";
import { parseApiErrorMessage } from "@/lib/api-error";
import { validateEmail, validatePasswordsMatch, validatePasswordStrength } from "@/lib/validation";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const nextEmailError = validateEmail(email);
    const nextPasswordError = validatePasswordStrength(password);
    const nextConfirmError = nextPasswordError ? null : validatePasswordsMatch(password, confirmPassword);
    setEmailError(nextEmailError);
    setPasswordError(nextPasswordError);
    setConfirmError(nextConfirmError);
    if (nextEmailError || nextPasswordError || nextConfirmError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Signup failed. Please try again."));
        return;
      }
      setSuccess(true);
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  if (success) {
    return (
      <AuthCard title="Account created">
        <FormSuccess message="Your account has been created. You can now log in." />
        <Link
          href="/login"
          className="block w-full rounded-md bg-accent-primary px-4 py-2 text-center text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
        >
          Go to login
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Sign up">
      <form onSubmit={handleSubmit} noValidate>
        <FormError message={formError} />
        <FormField
          id="email"
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          error={emailError}
          autoComplete="email"
        />
        <FormField
          id="password"
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          error={passwordError}
          autoComplete="new-password"
        />
        <FormField
          id="confirm-password"
          label="Confirm password"
          type="password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          error={confirmError}
          autoComplete="new-password"
        />
        <p className="mb-4 text-xs text-text-secondary">
          At least 8 characters, with a letter and a digit.
        </p>
        <SubmitButton label="Sign up" pending={pending} />
      </form>
      <p className="mt-4 text-center text-sm text-text-secondary">
        Already have an account?{" "}
        <Link href="/login" className="text-accent-primary hover:text-accent-primary-light">
          Log in
        </Link>
      </p>
    </AuthCard>
  );
}
