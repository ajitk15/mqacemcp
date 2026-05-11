"use client";

interface Props {
  columns: string[];
  rows: string[][];
  title?: string;
}

export default function TableBlock({ columns, rows, title }: Props) {
  return (
    <div className="my-2 overflow-x-auto rounded-md border border-border">
      {title && (
        <div className="border-b border-border bg-panel px-3 py-1 text-xs uppercase tracking-wide text-muted">
          {title}
        </div>
      )}
      <table className="min-w-full text-sm">
        <thead className="bg-panel">
          <tr>
            {columns.map((c) => (
              <th key={c} className="px-3 py-2 text-left font-medium text-muted">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {columns.map((_, j) => (
                <td key={j} className="px-3 py-1.5 align-top">
                  {r[j] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
