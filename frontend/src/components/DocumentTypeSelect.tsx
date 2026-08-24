"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { DocumentType } from "@/lib/types";

interface Props {
  id: string;
  value: string;
  types: DocumentType[];
  disabled?: boolean;
  onChange: (name: string) => void;
  /** Save a brand-new type so it is selectable everywhere afterwards. */
  onCreate: (name: string) => Promise<void>;
  /** Permanently remove a saved type. */
  onDelete: (type: DocumentType) => void;
}

/**
 * Searchable document type picker.
 *
 * Types are saved rows rather than free text, so the same vocabulary is offered
 * everywhere. A type not in the list can still be created inline, and a saved
 * custom type can be permanently removed from here.
 */
export default function DocumentTypeSelect({
  id,
  value,
  types,
  disabled = false,
  onChange,
  onCreate,
  onDelete,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return types;
    return types.filter(
      (type) =>
        type.name.toLowerCase().includes(needle) ||
        type.description.toLowerCase().includes(needle),
    );
  }, [types, query]);

  const trimmedQuery = query.trim();
  const exactExists = types.some(
    (type) => type.name.toLowerCase() === trimmedQuery.toLowerCase(),
  );
  const canCreate = trimmedQuery.length > 0 && !exactExists;

  function choose(name: string) {
    onChange(name);
    setOpen(false);
    setQuery("");
  }

  async function handleCreate() {
    if (!canCreate || creating) return;
    setCreating(true);
    try {
      await onCreate(trimmedQuery);
      choose(trimmedQuery);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        id={id}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((current) => !current);
          setQuery("");
        }}
        className="flex w-full items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-950 outline-none transition focus-visible:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-300 disabled:cursor-not-allowed disabled:bg-slate-50"
      >
        <span className={`min-w-0 flex-1 truncate ${value ? "" : "text-slate-400"}`}>
          {value || "Select a document type"}
        </span>
        <svg
          className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-40 mt-1.5 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg shadow-slate-950/10">
          <div className="border-b border-slate-200 p-2">
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && canCreate) {
                  event.preventDefault();
                  void handleCreate();
                }
                if (event.key === "Escape") setOpen(false);
              }}
              placeholder="Search or type a new one…"
              className="w-full rounded-lg border border-slate-200 px-2.5 py-2 text-sm text-slate-950 outline-none transition placeholder:text-slate-400 focus-visible:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-300"
            />
          </div>

          <ul role="listbox" className="max-h-64 overflow-y-auto p-1.5">
            {filtered.length === 0 && !canCreate && (
              <li className="px-3 py-6 text-center text-sm text-slate-400">No matching type.</li>
            )}

            {filtered.map((type) => {
              const selected = type.name.toLowerCase() === value.trim().toLowerCase();
              return (
                <li key={type.id}>
                  <div
                    className={`group flex items-start gap-2 rounded-lg px-2.5 py-2 transition ${
                      selected ? "bg-blue-50" : "hover:bg-slate-50"
                    }`}
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => choose(type.name)}
                      className="min-w-0 flex-1 text-left focus-visible:outline-none"
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-slate-950">
                          {type.name}
                        </span>
                        {type.is_builtin && (
                          <span className="shrink-0 rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                            Parser
                          </span>
                        )}
                      </span>
                      {type.description && (
                        <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-slate-500">
                          {type.description}
                        </span>
                      )}
                    </button>

                    {!type.is_builtin && (
                      <button
                        type="button"
                        title={`Permanently remove "${type.name}"`}
                        aria-label={`Permanently remove ${type.name}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onDelete(type);
                          setOpen(false);
                        }}
                        className="shrink-0 rounded-md p-1 text-slate-300 transition hover:bg-rose-50 hover:text-rose-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 group-hover:text-slate-400"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          {canCreate && (
            <button
              type="button"
              disabled={creating}
              onClick={() => void handleCreate()}
              className="flex w-full items-center gap-2 border-t border-slate-200 px-3 py-2.5 text-left text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
            >
              <span className="text-slate-400">+</span>
              {creating ? "Saving…" : <>Save &ldquo;{trimmedQuery}&rdquo; as a new document type</>}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
