export function FormSuccess({ message }: { message: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <div role="status" className="mb-4 rounded-md border border-success/30 bg-success/10 px-3 py-2 text-sm text-success">
      {message}
    </div>
  );
}
