"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createRuleConfig,
  deleteRuleConfig,
  duplicateRuleConfig,
  listRuleConfigs,
  listRuleDocumentTypes,
  setDefaultRuleConfig,
  updateRuleConfig,
} from "@/lib/api";
import type {
  DocumentRuleInput,
  OpinionTemplate,
  RuleAction,
  RuleConfig,
  RuleConfigInput,
} from "@/lib/types";

const ACTION_OPTIONS: { value: RuleAction; label: string; hint: string }[] = [
  { value: "extract", label: "Extract", hint: "Summarize using the rule's instructions" },
  { value: "full_data", label: "Whole data", hint: "Give the AI the full page text, not just extracted evidence" },
  { value: "skip", label: "Skip", hint: "Keep the document card but write no prose for it" },
];

const TEMPLATE_OPTIONS: { value: OpinionTemplate; label: string }[] = [
  { value: "disability", label: "Disability file review" },
  { value: "critical_illness", label: "Critical illness review" },
  { value: "accommodation", label: "Accommodation opinion" },
  { value: "underwriting", label: "Underwriting review" },
];

interface DraftRule extends DocumentRuleInput {
  key: string;
}

interface Draft {
  name: string;
  description: string;
  golden_rule_prompt: string;
  summary_prompt: string;
  opinion_prompt: string;
  opinion_template: OpinionTemplate;
  rules: DraftRule[];
}

let ruleKeyCounter = 0;
function nextRuleKey() {
  ruleKeyCounter += 1;
  return `draft-rule-${ruleKeyCounter}`;
}

function emptyDraft(): Draft {
  return {
    name: "",
    description: "",
    golden_rule_prompt: "",
    summary_prompt: "",
    opinion_prompt: "",
    opinion_template: "disability",
    rules: [],
  };
}

function draftFromConfig(config: RuleConfig): Draft {
  return {
    name: config.name,
    description: config.description ?? "",
    golden_rule_prompt: config.golden_rule_prompt ?? "",
    summary_prompt: config.summary_prompt ?? "",
    opinion_prompt: config.opinion_prompt ?? "",
    opinion_template: config.opinion_template,
    rules: config.rules.map((rule) => ({
      key: nextRuleKey(),
      document_type: rule.document_type,
      match_prompt: rule.match_prompt ?? "",
      action: rule.action,
      instruction_prompt: rule.instruction_prompt ?? "",
      max_words: rule.max_words,
      use_as_context: rule.use_as_context,
    })),
  };
}

function draftToPayload(draft: Draft): RuleConfigInput {
  return {
    name: draft.name.trim(),
    description: draft.description.trim(),
    golden_rule_prompt: draft.golden_rule_prompt,
    summary_prompt: draft.summary_prompt.trim() ? draft.summary_prompt : null,
    opinion_prompt: draft.opinion_prompt.trim() ? draft.opinion_prompt : null,
    opinion_template: draft.opinion_template,
    rules: draft.rules.map((rule) => ({
      document_type: rule.document_type.trim(),
      match_prompt: rule.match_prompt,
      action: rule.action,
      instruction_prompt: rule.instruction_prompt,
      max_words: rule.max_words,
      use_as_context: rule.use_as_context,
    })),
  };
}

function dispatchConfigsChanged() {
  window.dispatchEvent(new CustomEvent("portal:rule-configs-changed"));
}

export default function RuleStudioWorkspace() {
  const [configs, setConfigs] = useState<RuleConfig[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft());
  const [dirty, setDirty] = useState(false);
  const [documentTypes, setDocumentTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const selected = useMemo(
    () => configs.find((config) => config.id === selectedId) ?? null,
    [configs, selectedId],
  );

  const refresh = useCallback(async (keepSelection = true) => {
    try {
      const [payload, typesPayload] = await Promise.all([listRuleConfigs(), listRuleDocumentTypes()]);
      setConfigs(payload.configs);
      setDocumentTypes(typesPayload.document_types);
      setError("");
      setSelectedId((current) => {
        if (keepSelection && current && payload.configs.some((config) => config.id === current)) {
          return current;
        }
        const fallback = payload.configs.find((config) => config.is_default) ?? payload.configs[0];
        return fallback ? fallback.id : null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load rule configurations.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
  }, [refresh]);

  useEffect(() => {
    if (creating) return;
    if (selected) {
      setDraft(draftFromConfig(selected));
      setDirty(false);
    }
  }, [selected, creating]);

  function updateDraft(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
    setNotice("");
  }

  function updateRule(key: string, patch: Partial<DraftRule>) {
    setDraft((current) => ({
      ...current,
      rules: current.rules.map((rule) => (rule.key === key ? { ...rule, ...patch } : rule)),
    }));
    setDirty(true);
    setNotice("");
  }

  function moveRule(key: string, delta: -1 | 1) {
    setDraft((current) => {
      const index = current.rules.findIndex((rule) => rule.key === key);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= current.rules.length) return current;
      const rules = [...current.rules];
      const [moved] = rules.splice(index, 1);
      rules.splice(target, 0, moved);
      return { ...current, rules };
    });
    setDirty(true);
  }

  function addRule() {
    setDraft((current) => ({
      ...current,
      rules: [
        ...current.rules,
        {
          key: nextRuleKey(),
          document_type: "",
          match_prompt: "",
          action: "extract",
          instruction_prompt: "",
          max_words: null,
          use_as_context: false,
        },
      ],
    }));
    setDirty(true);
  }

  function removeRule(key: string) {
    setDraft((current) => ({ ...current, rules: current.rules.filter((rule) => rule.key !== key) }));
    setDirty(true);
  }

  function startCreate() {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setCreating(true);
    setSelectedId(null);
    setDraft({
      ...emptyDraft(),
      golden_rule_prompt: selected?.golden_rule_prompt ?? "",
    });
    setDirty(false);
    setNotice("");
  }

  function selectConfig(id: string) {
    if (id === selectedId && !creating) return;
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setCreating(false);
    setSelectedId(id);
    setNotice("");
  }

  const validationError = useMemo(() => {
    if (!draft.name.trim()) return "Give the configuration a name.";
    for (const rule of draft.rules) {
      if (!rule.document_type.trim()) return "Every rule needs a document type.";
    }
    return "";
  }, [draft]);

  async function handleSave() {
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = draftToPayload(draft);
      if (creating) {
        const response = await createRuleConfig(payload);
        setCreating(false);
        setSelectedId(response.config.id);
        setNotice(`Created "${response.config.name}".`);
      } else if (selected) {
        const response = await updateRuleConfig(selected.id, payload);
        setNotice(`Saved "${response.config.name}" (now version ${response.config.version}).`);
      }
      setDirty(false);
      await refresh();
      dispatchConfigsChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save the configuration.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selected) return;
    if (!window.confirm(`Delete "${selected.name}"? Past extractions keep the rules they ran with.`)) {
      return;
    }
    setSaving(true);
    setError("");
    try {
      await deleteRuleConfig(selected.id);
      setNotice(`Deleted "${selected.name}".`);
      setSelectedId(null);
      await refresh(false);
      dispatchConfigsChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete the configuration.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDuplicate() {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      const response = await duplicateRuleConfig(selected.id);
      setCreating(false);
      setSelectedId(response.config.id);
      setNotice(`Duplicated into "${response.config.name}".`);
      await refresh();
      dispatchConfigsChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to duplicate the configuration.");
    } finally {
      setSaving(false);
    }
  }

  async function handleSetDefault() {
    if (!selected || selected.is_default) return;
    setSaving(true);
    setError("");
    try {
      await setDefaultRuleConfig(selected.id);
      setNotice(`"${selected.name}" is now the default for new extractions.`);
      await refresh();
      dispatchConfigsChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to set the default configuration.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[300px,1fr]">
      <aside className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between px-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">Configurations</p>
          <button
            type="button"
            onClick={startCreate}
            className="rounded-lg bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-800"
          >
            New
          </button>
        </div>

        <div className="mt-3 grid gap-1.5">
          {loading && <p className="px-2 py-6 text-center text-sm text-slate-400">Loading...</p>}
          {!loading && configs.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-slate-400">No configurations yet.</p>
          )}
          {configs.map((config) => {
            const active = !creating && config.id === selectedId;
            return (
              <button
                key={config.id}
                type="button"
                onClick={() => selectConfig(config.id)}
                className={`rounded-xl border px-3 py-2.5 text-left transition ${
                  active
                    ? "border-slate-900 bg-slate-950 text-white"
                    : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <span className="block truncate text-sm font-semibold">{config.name}</span>
                <span className={`mt-1 flex flex-wrap items-center gap-1.5 text-[11px] ${active ? "text-slate-300" : "text-slate-400"}`}>
                  <span>v{config.version}</span>
                  <span>| {config.rules.length} rule{config.rules.length === 1 ? "" : "s"}</span>
                  {config.is_default && (
                    <span className={`rounded-full px-2 py-0.5 font-semibold ${active ? "bg-white/20 text-white" : "bg-blue-50 text-blue-700"}`}>
                      Default
                    </span>
                  )}
                  {config.is_seeded && (
                    <span className={`rounded-full px-2 py-0.5 font-semibold ${active ? "bg-white/20 text-white" : "bg-slate-100 text-slate-500"}`}>
                      Built-in
                    </span>
                  )}
                </span>
              </button>
            );
          })}
          {creating && (
            <div className="rounded-xl border border-dashed border-blue-300 bg-blue-50 px-3 py-2.5 text-sm font-semibold text-blue-700">
              New configuration
            </div>
          )}
        </div>

        {selected && !creating && (
          <div className="mt-4 grid gap-1.5 border-t border-slate-200 pt-3">
            <button
              type="button"
              onClick={() => void handleSetDefault()}
              disabled={saving || selected.is_default}
              className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {selected.is_default ? "Default configuration" : "Use as default"}
            </button>
            <button
              type="button"
              onClick={() => void handleDuplicate()}
              disabled={saving}
              className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
            >
              Duplicate
            </button>
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={saving || configs.length <= 1}
              className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Delete
            </button>
          </div>
        )}
      </aside>

      <section className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm md:p-6">
        {(error || notice) && (
          <div
            className={`mb-4 rounded-xl border px-4 py-3 text-sm ${
              error ? "border-red-200 bg-red-50 text-red-700" : "border-emerald-200 bg-emerald-50 text-emerald-700"
            }`}
          >
            {error || notice}
          </div>
        )}

        {!selected && !creating ? (
          <p className="py-16 text-center text-sm text-slate-400">Select a configuration or create a new one.</p>
        ) : (
          <div className="grid gap-5">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Name</span>
                <input
                  value={draft.name}
                  onChange={(event) => updateDraft({ name: event.target.value })}
                  placeholder="e.g. LTD reviews - imaging skipped"
                  className="rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-950 outline-none transition focus:border-blue-400"
                />
              </label>
              <label className="grid gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Opinion template</span>
                <select
                  value={draft.opinion_template}
                  onChange={(event) => updateDraft({ opinion_template: event.target.value as OpinionTemplate })}
                  className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-950 outline-none transition focus:border-blue-400"
                >
                  {TEMPLATE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="grid gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Description</span>
              <input
                value={draft.description}
                onChange={(event) => updateDraft({ description: event.target.value })}
                placeholder="When should this configuration be used?"
                className="rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-950 outline-none transition focus:border-blue-400"
              />
            </label>

            <label className="grid gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                Global golden rule prompt
              </span>
              <span className="text-xs text-slate-500">
                Applied to every AI stage (page reading, summaries, and opinions) for extractions run with this
                configuration.
              </span>
              <textarea
                value={draft.golden_rule_prompt}
                onChange={(event) => updateDraft({ golden_rule_prompt: event.target.value })}
                rows={9}
                className="rounded-xl border border-slate-200 px-3 py-2.5 font-mono text-[13px] leading-relaxed text-slate-950 outline-none transition focus:border-blue-400"
              />
            </label>

            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-950">Document type rules</p>
                  <p className="text-xs text-slate-500">
                    Attach behavior to a document type: how the AI recognizes it, and what to do with it.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={addRule}
                  className="rounded-lg bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-800"
                >
                  Add rule
                </button>
              </div>

              <datalist id="rule-studio-document-types">
                {documentTypes.map((value) => (
                  <option key={value} value={value} />
                ))}
              </datalist>

              <div className="mt-3 grid gap-3">
                {draft.rules.length === 0 && (
                  <p className="rounded-xl border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-400">
                    No rules yet. Documents are summarized with the default behavior.
                  </p>
                )}
                {draft.rules.map((rule, index) => (
                  <div key={rule.key} className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="grid gap-3 md:grid-cols-[1fr,180px,120px,auto]">
                      <label className="grid gap-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                          Document type
                        </span>
                        <input
                          value={rule.document_type}
                          onChange={(event) => updateRule(rule.key, { document_type: event.target.value })}
                          list="rule-studio-document-types"
                          placeholder="Type or pick (e.g. imaging, Referral Form)"
                          className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-950 outline-none transition focus:border-blue-400"
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">Action</span>
                        <select
                          value={rule.action}
                          onChange={(event) => updateRule(rule.key, { action: event.target.value as RuleAction })}
                          title={ACTION_OPTIONS.find((option) => option.value === rule.action)?.hint}
                          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-950 outline-none transition focus:border-blue-400"
                        >
                          {ACTION_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value} title={option.hint}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="grid gap-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">Max words</span>
                        <input
                          type="number"
                          min={10}
                          max={2000}
                          value={rule.max_words ?? ""}
                          onChange={(event) =>
                            updateRule(rule.key, {
                              max_words: event.target.value ? Number(event.target.value) : null,
                            })
                          }
                          placeholder="auto"
                          className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-950 outline-none transition focus:border-blue-400"
                        />
                      </label>
                      <div className="flex items-end justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => moveRule(rule.key, -1)}
                          disabled={index === 0}
                          title="Move up"
                          className="rounded-lg border border-slate-200 px-2 py-2 text-xs text-slate-500 transition hover:bg-slate-50 disabled:opacity-30"
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => moveRule(rule.key, 1)}
                          disabled={index === draft.rules.length - 1}
                          title="Move down"
                          className="rounded-lg border border-slate-200 px-2 py-2 text-xs text-slate-500 transition hover:bg-slate-50 disabled:opacity-30"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          onClick={() => removeRule(rule.key)}
                          title="Remove rule"
                          className="rounded-lg border border-red-200 px-2.5 py-2 text-xs font-semibold text-red-600 transition hover:bg-red-50"
                        >
                          Remove
                        </button>
                      </div>
                    </div>

                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <label className="grid gap-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                          How the AI recognizes this document
                        </span>
                        <textarea
                          value={rule.match_prompt}
                          onChange={(event) => updateRule(rule.key, { match_prompt: event.target.value })}
                          rows={3}
                          placeholder="e.g. Referral forms addressed to the reviewing consultant, listing questions to answer."
                          className="rounded-lg border border-slate-200 px-3 py-2 text-[13px] leading-relaxed text-slate-950 outline-none transition focus:border-blue-400"
                        />
                      </label>
                      <label className="grid gap-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                          What to do with it
                        </span>
                        <textarea
                          value={rule.instruction_prompt}
                          onChange={(event) => updateRule(rule.key, { instruction_prompt: event.target.value })}
                          rows={3}
                          placeholder="e.g. Extract the diagnosis, restrictions, and return-to-work guidance only."
                          className="rounded-lg border border-slate-200 px-3 py-2 text-[13px] leading-relaxed text-slate-950 outline-none transition focus:border-blue-400"
                        />
                      </label>
                    </div>

                    <label className="mt-3 flex items-center gap-2 text-xs text-slate-600">
                      <input
                        type="checkbox"
                        checked={rule.use_as_context}
                        onChange={(event) => updateRule(rule.key, { use_as_context: event.target.checked })}
                        className="h-4 w-4 rounded border-slate-300"
                      />
                      Feed matching documents to the Opinion as referral/assignment context
                    </label>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 p-4">
              <button
                type="button"
                onClick={() => setShowAdvanced((current) => !current)}
                className="text-sm font-semibold text-slate-700 transition hover:text-slate-950"
              >
                {showAdvanced ? "Hide" : "Show"} advanced prompt overrides
              </button>
              {showAdvanced && (
                <div className="mt-3 grid gap-4">
                  <label className="grid gap-1.5">
                    <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                      Summary prompt override
                    </span>
                    <span className="text-xs text-slate-500">
                      Replaces the built-in summary instructions entirely. Leave empty to use the built-in prompt.
                    </span>
                    <textarea
                      value={draft.summary_prompt}
                      onChange={(event) => updateDraft({ summary_prompt: event.target.value })}
                      rows={6}
                      className="rounded-xl border border-slate-200 px-3 py-2.5 font-mono text-[13px] leading-relaxed text-slate-950 outline-none transition focus:border-blue-400"
                    />
                  </label>
                  <label className="grid gap-1.5">
                    <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                      Opinion prompt override
                    </span>
                    <span className="text-xs text-slate-500">
                      Replaces the built-in opinion instructions entirely. Leave empty to use the built-in prompt.
                    </span>
                    <textarea
                      value={draft.opinion_prompt}
                      onChange={(event) => updateDraft({ opinion_prompt: event.target.value })}
                      rows={6}
                      className="rounded-xl border border-slate-200 px-3 py-2.5 font-mono text-[13px] leading-relaxed text-slate-950 outline-none transition focus:border-blue-400"
                    />
                  </label>
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
              <p className="text-xs text-slate-400">
                {creating
                  ? "New configuration - not saved yet."
                  : selected
                    ? `Version ${selected.version} - saving creates version ${selected.version + 1}. Past extractions keep the version they ran with.`
                    : ""}
              </p>
              <div className="flex items-center gap-2">
                {dirty && <span className="text-xs font-semibold text-amber-600">Unsaved changes</span>}
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving || (!dirty && !creating)}
                  className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saving ? "Saving..." : creating ? "Create configuration" : "Save changes"}
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
