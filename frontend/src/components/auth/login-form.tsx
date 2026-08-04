"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/auth-card";
import { FormField } from "@/components/auth/form-field";
import { FormError } from "@/components/auth/form-error";
import { SubmitButton } from "@/components/auth/submit-button";
import { parseApiErrorMessage } from "@/lib/api-error";
import { validateEmail, validatePasswordPresence } from "@/lib/validation";

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const nextEmailError = validateEmail(email);
    const nextPasswordError = validatePasswordPresence(password);
    setEmailError(nextEmailError);
    setPasswordError(nextPasswordError);
    if (nextEmailError || nextPasswordError) {
      return;
    }

    setPending(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await response.json();
      if (!response.ok) {
        setFormError(parseApiErrorMessage(data, "Login failed. Please try again."));
        return;
      }
      const next = searchParams.get("next");
      router.push(next && next.startsWith("/") ? next : "/dashboard");
      router.refresh();
    } catch {
      setFormError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard title="Log in">
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
          autoComplete="current-password"
        />
        <div className="mb-4 text-right text-sm">
          <Link href="/forgot-password" className="text-text-secondary hover:text-accent-primary">
            Forgot password?
          </Link>
        </div>
        <SubmitButton label="Log in" pending={pending} />
      </form>
      <p className="mt-4 text-center text-sm text-text-secondary">
        Don&apos;t have an account?{" "}
        <Link href="/signup" className="text-accent-primary hover:text-accent-primary-light">
          Sign up
        </Link>
      </p>
    </AuthCard>
  );
}
