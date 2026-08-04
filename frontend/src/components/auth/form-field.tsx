type FormFieldProps = {
  id: string;
  label: string;
  type: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  autoComplete?: string;
  min?: string | number;
  max?: string | number;
  step?: string | number;
};

export function FormField({ id, label, type, value, onChange, error, autoComplete, min, max, step }: FormFieldProps) {
  return (
    <div className="mb-4">
      <label htmlFor={id} className="mb-1 block text-sm text-text-secondary">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        min={min}
        max={max}
        step={step}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-primary ${
          error ? "border-danger" : "border-border"
        }`}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-xs text-danger-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
