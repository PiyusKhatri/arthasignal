export function SubmitButton({ label, pending }: { label: string; pending: boolean }) {
  return (
    <button
      type="submit"
      disabled={pending}
      className="w-full rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light disabled:opacity-60"
    >
      {pending ? "Please wait..." : label}
    </button>
  );
}
