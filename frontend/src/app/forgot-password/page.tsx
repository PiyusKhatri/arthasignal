"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { FormField } from "@/components/auth/form-field";
import { FormError } from "@/components/auth/form-error";
import { FormSuccess } from "@/components/auth/form-success";
import { SubmitButton } from "@/components/auth/submit-button";
import { parseApiErrorMessage } from "@/lib/api-error";
import { validateEmail } from "@/lib/validation";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setSuccessMessage(null);

    const nextEmailError = validateEmail(email);
    setEmailError(nextEmailError);
    if (nextEmailError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Something went wrong. Please try again."));
        return;
      }
      setSuccessMessage(data.detail);
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard title="Forgot password">
      <form onSubmit={handleSubmit} noValidate>
        <FormError message={formError} />
        <FormSuccess message={successMessage} />
        <FormField
          id="email"
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          error={emailError}
          autoComplete="email"
        />
        <SubmitButton label="Send reset link" pending={pending} />
      </form>
      <p className="mt-4 text-center text-sm text-text-secondary">
        <Link href="/login" className="text-accent-primary hover:text-accent-primary-light">
          Back to login
        </Link>
      </p>
    </AuthCard>
  );
}
