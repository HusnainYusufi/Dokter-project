"use client";

import { motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useState } from "react";

import ConfirmDialog from "@/components/ConfirmDialog";
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

type TabKey = "overview" | "golden" | "rules" | "advanced";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "golden", label: "Golden Rules" },
  { key: "rules", label: "Document Rules" },
  { key: "advanced", label: "Advanced" },
];

const ACTION_OPTIONS: { value: RuleAction; label: string; hint: string; badge: string }[] = [
  {
    value: "extract",
    label: "Extract",
    hint: "Summarize the document following this rule's instructions.",
    badge: "border-emerald-200 bg-emerald-50 text-emerald-700",
  },
  {
    value: "full_data",
    label: "Whole data",
    hint: "Send the document's full page text, not just the extracted evidence. Higher cost.",
    badge: "border-blue-200 bg-blue-50 text-blue-700",
  },
  {
    value: "skip",
    label: "Skip",
    hint: "Keep the document as a numbered card but write no prose for it.",
    badge: "border-slate-200 bg-slate-100 text-slate-600",
  },
];

// The parser's own buckets, mirroring BUILTIN_DOCUMENT_TYPES in
// app/services/rules/store.py. Anything else is a custom type, which the parser
// can only tag once the rule describes how to recognize it.
const BUILTIN_DOCUMENT_TYPES = ["clinical", "imaging", "pathology", "functional", "administrative"];

const TEMPLATE_OPTIONS: { value: OpinionTemplate; label: string }[] = [
  { value: "disability", label: "Disability file review" },
  { value: "critical_illness", label: "Critical illness review" },
  { value: "accommodation", label: "Accommodation opinion" },
  { value: "underwriting", label: "Underwriting review" },
];

const FIELD_CLASS =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-950 outline-none transition placeholder:text-slate-400 focus-visible:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-300";
const TEXTAREA_CLASS =
  "w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2.5 font-mono text-[13px] leading-relaxed text-slate-950 outline-none transition placeholder:text-slate-400 focus-visible:border-slate-400 focus-visible:ring-2 focus-visible:ring-slate-300";
const LABEL_CLASS = "text-xs font-semibold uppercase tracking-[0.14em] text-slate-400";
const PRIMARY_BUTTON =
  "rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400";
const SECONDARY_BUTTON =
  "rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400";

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

function actionMeta(action: RuleAction) {
  return ACTION_OPTIONS.find((option) => option.value === action) ?? ACTION_OPTIONS[0];
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
  const [tab, setTab] = useState<TabKey>("overview");
  const [expandedRule, setExpandedRule] = useState<string | null>(null);
  const [documentTypes, setDocumentTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<null | (() => void)>(null);

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
    const key = nextRuleKey();
    setDraft((current) => ({
      ...current,
      rules: [
        ...current.rules,
        {
          key,
          document_type: "",
          match_prompt: "",
          action: "extract",
          instruction_prompt: "",
          max_words: null,
          use_as_context: false,
        },
      ],
    }));
    setExpandedRule(key);
    setDirty(true);
  }

  function removeRule(key: string) {
    setDraft((current) => ({ ...current, rules: current.rules.filter((rule) => rule.key !== key) }));
    setDirty(true);
  }

  /** Route a navigation through the unsaved-changes guard. */
  function guard(action: () => void) {
    if (dirty) {
      setPendingNavigation(() => action);
      return;
    }
    action();
  }

  function startCreate() {
    guard(() => {
      setCreating(true);
      setSelectedId(null);
      setDraft({ ...emptyDraft(), golden_rule_prompt: selected?.golden_rule_prompt ?? "" });
      setDirty(false);
      setTab("overview");
      setNotice("");
    });
  }

  function selectConfig(id: string) {
    if (id === selectedId && !creating) return;
    guard(() => {
      setCreating(false);
      setSelectedId(id);
      setNotice("");
    });
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
      if (!draft.name.trim()) setTab("overview");
      else setTab("rules");
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
        setNotice(`Saved "${response.config.name}" as version ${response.config.version}.`);
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
    setSaving(true);
    setError("");
    try {
      await deleteRuleConfig(selected.id);
      setNotice(`Deleted "${selected.name}".`);
      setSelectedId(null);
      setDirty(false);
      await refresh(false);
      dispatchConfigsChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete the configuration.");
    } finally {
      setSaving(false);
      setConfirmDelete(false);
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

  const showEditor = Boolean(selected) || creating;

  return (
    <>
      <ConfirmDialog
        open={confirmDelete}
        tone="danger"
        title={`Delete "${selected?.name ?? ""}"?`}
        description="Completed extractions keep the rules they ran with, so past results are unaffected. This cannot be undone."
        confirmLabel="Delete configuration"
        busy={saving}
        busyLabel="Deleting…"
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(false)}
      />

      <ConfirmDialog
        open={pendingNavigation !== null}
        tone="warning"
        title="Discard unsaved changes?"
        description="This configuration has edits that have not been saved. Leaving now loses them."
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        onConfirm={() => {
          const action = pendingNavigation;
          setPendingNavigation(null);
          setDirty(false);
          action?.();
        }}
        onCancel={() => setPendingNavigation(null)}
      />

      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28 }}
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm"
      >
        <div className="grid min-h-0 flex-1 lg:grid-cols-[300px_minmax(0,1fr)]">
          {/* Master: configuration list */}
          <div className="flex min-h-0 flex-col border-b border-slate-200 bg-white lg:border-b-0 lg:border-r">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3.5">
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                  Configurations
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {configs.length} saved
                </p>
              </div>
              <button type="button" onClick={startCreate} className={`${PRIMARY_BUTTON} px-3 py-2`}>
                New
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {loading && <p className="px-2 py-10 text-center text-sm text-slate-400">Loading…</p>}

              {!loading && configs.length === 0 && !creating && (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                  No configurations yet.
                </div>
              )}

              <div className="grid gap-1.5">
                {creating && (
                  <div className="rounded-xl border border-dashed border-slate-400 bg-slate-50 px-3 py-2.5">
                    <span className="block text-sm font-semibold text-slate-900">New configuration</span>
                    <span className="mt-0.5 block text-[11px] text-slate-500">Unsaved draft</span>
                  </div>
                )}

                {configs.map((config) => {
                  const active = !creating && config.id === selectedId;
                  return (
                    <button
                      key={config.id}
                      type="button"
                      onClick={() => selectConfig(config.id)}
                      aria-current={active ? "true" : undefined}
                      className={`rounded-xl border px-3 py-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 ${
                        active
                          ? "border-blue-200 bg-blue-50 shadow-sm"
                          : "border-transparent bg-white hover:border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-950">
                          {config.name}
                        </span>
                        {config.is_default && (
                          <span className="shrink-0 rounded-full border border-blue-200 bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700">
                            Default
                          </span>
                        )}
                      </span>
                      <span className="mt-1 block text-[11px] text-slate-500">
                        v{config.version} · {config.rules.length} rule{config.rules.length === 1 ? "" : "s"}
                        {config.is_seeded ? " · Built-in" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Detail */}
          <div className="flex min-h-0 flex-col bg-slate-50/70">
            {!showEditor ? (
              <div className="flex flex-1 items-center justify-center px-6 py-16 text-center">
                <div>
                  <p className="text-base font-semibold text-slate-900">No configuration selected</p>
                  <p className="mt-2 text-sm text-slate-500">
                    Choose a configuration on the left, or create a new one.
                  </p>
                </div>
              </div>
            ) : (
              <>
                {/* Sticky detail header */}
                <div className="border-b border-slate-200 bg-white/80 px-5 py-3.5 backdrop-blur md:px-6">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-lg font-semibold text-slate-950">
                        {creating ? "New configuration" : selected?.name}
                      </h2>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {creating
                          ? "Not saved yet."
                          : `Version ${selected?.version} · saving creates version ${(selected?.version ?? 0) + 1}. Past extractions keep the version they ran with.`}
                      </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      {dirty && (
                        <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
                          Unsaved changes
                        </span>
                      )}
                      {!creating && selected && (
                        <>
                          <button
                            type="button"
                            onClick={() => void handleSetDefault()}
                            disabled={saving || selected.is_default}
                            className={`${SECONDARY_BUTTON} px-3 py-2`}
                          >
                            {selected.is_default ? "Default" : "Set default"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDuplicate()}
                            disabled={saving}
                            className={`${SECONDARY_BUTTON} px-3 py-2`}
                          >
                            Duplicate
                          </button>
                        </>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleSave()}
                        disabled={saving || (!dirty && !creating)}
                        className={PRIMARY_BUTTON}
                      >
                        {saving ? "Saving…" : creating ? "Create" : "Save changes"}
                      </button>
                    </div>
                  </div>

                  <div role="tablist" aria-label="Configuration sections" className="mt-3 flex w-fit rounded-xl border border-slate-200 bg-white p-1">
                    {TABS.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        role="tab"
                        aria-selected={tab === item.key}
                        onClick={() => setTab(item.key)}
                        className={`rounded-lg px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 ${
                          tab === item.key
                            ? "bg-slate-950 text-white"
                            : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                        }`}
                      >
                        {item.label}
                        {item.key === "rules" && draft.rules.length > 0 && (
                          <span className={tab === "rules" ? "ml-1.5 text-slate-300" : "ml-1.5 text-slate-400"}>
                            {draft.rules.length}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>

                {(error || notice) && (
                  <div
                    className={`border-b px-5 py-3 text-sm md:px-6 ${
                      error
                        ? "border-rose-200 bg-rose-50 text-rose-700"
                        : "border-emerald-200 bg-emerald-50 text-emerald-700"
                    }`}
                  >
                    {error || notice}
                  </div>
                )}

                {/* Tab panes: each owns its scroll */}
                <div className="min-h-0 flex-1 overflow-y-auto p-5 md:p-6">
                  {tab === "overview" && (
                    <div className="grid max-w-3xl gap-5">
                      <div className="grid gap-4 md:grid-cols-2">
                        <div className="grid gap-1.5">
                          <label htmlFor="config-name" className={LABEL_CLASS}>Name</label>
                          <input
                            id="config-name"
                            value={draft.name}
                            onChange={(event) => updateDraft({ name: event.target.value })}
                            placeholder="e.g. LTD reviews — imaging skipped"
                            className={FIELD_CLASS}
                          />
                        </div>
                        <div className="grid gap-1.5">
                          <label htmlFor="config-template" className={LABEL_CLASS}>Opinion template</label>
                          <select
                            id="config-template"
                            value={draft.opinion_template}
                            onChange={(event) =>
                              updateDraft({ opinion_template: event.target.value as OpinionTemplate })
                            }
                            className={FIELD_CLASS}
                          >
                            {TEMPLATE_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>

                      <div className="grid gap-1.5">
                        <label htmlFor="config-description" className={LABEL_CLASS}>Description</label>
                        <span className="text-xs text-slate-500">When should this configuration be used?</span>
                        <input
                          id="config-description"
                          value={draft.description}
                          onChange={(event) => updateDraft({ description: event.target.value })}
                          placeholder="Long-term disability reviews for Alberta Blue Cross"
                          className={FIELD_CLASS}
                        />
                      </div>

                      {!creating && selected && (
                        <div className="rounded-2xl border border-rose-200 bg-rose-50/60 p-4">
                          <p className="text-sm font-semibold text-slate-950">Delete this configuration</p>
                          <p className="mt-1 text-sm text-slate-600">
                            Past extractions keep the rules they ran with. The last remaining configuration
                            cannot be deleted.
                          </p>
                          <button
                            type="button"
                            onClick={() => setConfirmDelete(true)}
                            disabled={saving || configs.length <= 1}
                            className="mt-3 rounded-xl border border-rose-300 bg-white px-4 py-2.5 text-sm font-semibold text-rose-600 transition hover:bg-rose-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            Delete configuration
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {tab === "golden" && (
                    <div className="flex h-full min-h-[26rem] flex-col gap-2">
                      <div>
                        <p className="text-sm font-semibold text-slate-950">Global golden rule prompt</p>
                        <p className="mt-1 text-sm text-slate-500">
                          Applied to every AI stage — page reading, summaries, and opinions — for extractions
                          run with this configuration.
                        </p>
                      </div>
                      <textarea
                        aria-label="Global golden rule prompt"
                        value={draft.golden_rule_prompt}
                        onChange={(event) => updateDraft({ golden_rule_prompt: event.target.value })}
                        placeholder="Standing house rules: tone, naming, date format, what to exclude…"
                        className={`${TEXTAREA_CLASS} min-h-0 flex-1`}
                      />
                    </div>
                  )}

                  {tab === "rules" && (
                    <div className="grid gap-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-slate-950">Document type rules</p>
                          <p className="mt-1 text-sm text-slate-500">
                            Attach behavior to a document type: how the AI recognizes it, and what to do with it.
                          </p>
                        </div>
                        <button type="button" onClick={addRule} className={PRIMARY_BUTTON}>
                          Add rule
                        </button>
                      </div>

                      <datalist id="rule-studio-document-types">
                        {documentTypes.map((value) => (
                          <option key={value} value={value} />
                        ))}
                      </datalist>

                      {draft.rules.length === 0 && (
                        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-4 py-12 text-center">
                          <p className="text-base font-semibold text-slate-900">No rules yet</p>
                          <p className="mt-2 text-sm text-slate-500">
                            Documents are summarized with the standard behavior until you add a rule.
                          </p>
                        </div>
                      )}

                      {draft.rules.map((rule, index) => {
                        const meta = actionMeta(rule.action);
                        const expanded = expandedRule === rule.key;
                        // A skipped document never reaches the summarizer, so the
                        // backend ignores its word ceiling and its instructions
                        // (see build_summary_prompt / _unit_budget). Hiding both
                        // keeps the form honest about what actually applies.
                        const isSkip = rule.action === "skip";
                        const isCustomType = Boolean(
                          rule.document_type.trim() &&
                            !BUILTIN_DOCUMENT_TYPES.includes(rule.document_type.trim().toLowerCase()),
                        );
                        const needsMatchPrompt = isCustomType && !rule.match_prompt.trim();
                        return (
                          <div
                            key={rule.key}
                            className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
                          >
                            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                              <button
                                type="button"
                                onClick={() => setExpandedRule(expanded ? null : rule.key)}
                                aria-expanded={expanded}
                                className="flex min-w-0 flex-1 items-center gap-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                              >
                                <svg
                                  className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${expanded ? "rotate-90" : ""}`}
                                  fill="none"
                                  viewBox="0 0 24 24"
                                  stroke="currentColor"
                                  strokeWidth={2}
                                >
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                </svg>
                                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-950">
                                  {rule.document_type.trim() || (
                                    <span className="text-slate-400">Untitled rule</span>
                                  )}
                                </span>
                                <span
                                  className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${meta.badge}`}
                                >
                                  {meta.label}
                                </span>
                                {rule.max_words && (
                                  <span className="hidden shrink-0 text-[11px] text-slate-400 sm:inline">
                                    {rule.max_words} words
                                  </span>
                                )}
                                {rule.use_as_context && (
                                  <span className="hidden shrink-0 text-[11px] text-slate-400 md:inline">
                                    Opinion context
                                  </span>
                                )}
                              </button>

                              <div className="flex shrink-0 items-center gap-1">
                                <button
                                  type="button"
                                  onClick={() => moveRule(rule.key, -1)}
                                  disabled={index === 0}
                                  aria-label="Move rule up"
                                  className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-500 transition hover:bg-slate-50 disabled:opacity-30"
                                >
                                  ↑
                                </button>
                                <button
                                  type="button"
                                  onClick={() => moveRule(rule.key, 1)}
                                  disabled={index === draft.rules.length - 1}
                                  aria-label="Move rule down"
                                  className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-500 transition hover:bg-slate-50 disabled:opacity-30"
                                >
                                  ↓
                                </button>
                                <button
                                  type="button"
                                  onClick={() => removeRule(rule.key)}
                                  aria-label="Remove rule"
                                  className="rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-semibold text-rose-600 transition hover:bg-rose-50"
                                >
                                  Remove
                                </button>
                              </div>
                            </div>

                            {expanded && (
                              <div className="grid gap-4 border-t border-slate-200 bg-slate-50/60 px-4 py-4">
                                <div className={`grid gap-4 ${isSkip ? "sm:grid-cols-[1fr_200px]" : "sm:grid-cols-[1fr_200px_140px]"}`}>
                                  <div className="grid gap-1.5">
                                    <label htmlFor={`type-${rule.key}`} className={LABEL_CLASS}>
                                      Document type
                                    </label>
                                    <input
                                      id={`type-${rule.key}`}
                                      value={rule.document_type}
                                      onChange={(event) =>
                                        updateRule(rule.key, { document_type: event.target.value })
                                      }
                                      list="rule-studio-document-types"
                                      placeholder="imaging, Referral Form…"
                                      className={FIELD_CLASS}
                                    />
                                  </div>
                                  <div className="grid gap-1.5">
                                    <label htmlFor={`action-${rule.key}`} className={LABEL_CLASS}>
                                      Action
                                    </label>
                                    <select
                                      id={`action-${rule.key}`}
                                      value={rule.action}
                                      onChange={(event) =>
                                        updateRule(rule.key, { action: event.target.value as RuleAction })
                                      }
                                      className={FIELD_CLASS}
                                    >
                                      {ACTION_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>
                                          {option.label}
                                        </option>
                                      ))}
                                    </select>
                                  </div>
                                  {!isSkip && (
                                    <div className="grid gap-1.5">
                                      <label htmlFor={`words-${rule.key}`} className={LABEL_CLASS}>
                                        Max words
                                      </label>
                                      <input
                                        id={`words-${rule.key}`}
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
                                        className={FIELD_CLASS}
                                      />
                                    </div>
                                  )}
                                </div>

                                <p className="text-xs text-slate-500">{meta.hint}</p>

                                <div className={`grid gap-4 ${isSkip ? "" : "xl:grid-cols-2"}`}>
                                  <div className="grid gap-1.5">
                                    <label htmlFor={`match-${rule.key}`} className={LABEL_CLASS}>
                                      How the AI recognizes this document
                                    </label>
                                    <textarea
                                      id={`match-${rule.key}`}
                                      value={rule.match_prompt}
                                      onChange={(event) =>
                                        updateRule(rule.key, { match_prompt: event.target.value })
                                      }
                                      rows={isSkip ? 6 : 10}
                                      placeholder="Referral forms addressed to the reviewing consultant, listing questions to answer."
                                      className={TEXTAREA_CLASS}
                                    />
                                    {needsMatchPrompt && (
                                      <p className="text-xs text-amber-700">
                                        &ldquo;{rule.document_type.trim()}&rdquo; is a custom type. Describe it here
                                        or the AI has nothing to match it on, and the rule will never fire.
                                      </p>
                                    )}
                                  </div>
                                  {!isSkip && (
                                    <div className="grid gap-1.5">
                                      <label htmlFor={`instruction-${rule.key}`} className={LABEL_CLASS}>
                                        What to do with it
                                      </label>
                                      <textarea
                                        id={`instruction-${rule.key}`}
                                        value={rule.instruction_prompt}
                                        onChange={(event) =>
                                          updateRule(rule.key, { instruction_prompt: event.target.value })
                                        }
                                        rows={10}
                                        placeholder="Extract the diagnosis, restrictions, and return-to-work guidance only."
                                        className={TEXTAREA_CLASS}
                                      />
                                    </div>
                                  )}
                                </div>

                                <label className="flex items-center gap-2 text-sm text-slate-600">
                                  <input
                                    type="checkbox"
                                    checked={rule.use_as_context}
                                    onChange={(event) =>
                                      updateRule(rule.key, { use_as_context: event.target.checked })
                                    }
                                    className="h-4 w-4 rounded border-slate-300"
                                  />
                                  Feed matching documents to the Opinion as referral/assignment context
                                </label>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {tab === "advanced" && (
                    <div className="grid gap-5">
                      <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                        These replace the built-in instructions entirely. Prefer the golden rule prompt and
                        per-rule instructions first; use an override only when the built-in behavior is wrong
                        rather than incomplete. Leave empty to keep the built-in prompt.
                      </div>

                      <div className="grid gap-1.5">
                        <label htmlFor="summary-override" className={LABEL_CLASS}>
                          Summary prompt override
                        </label>
                        <textarea
                          id="summary-override"
                          value={draft.summary_prompt}
                          onChange={(event) => updateDraft({ summary_prompt: event.target.value })}
                          rows={12}
                          placeholder="Leave empty to use the built-in summary prompt."
                          className={TEXTAREA_CLASS}
                        />
                      </div>

                      <div className="grid gap-1.5">
                        <label htmlFor="opinion-override" className={LABEL_CLASS}>
                          Opinion prompt override
                        </label>
                        <textarea
                          id="opinion-override"
                          value={draft.opinion_prompt}
                          onChange={(event) => updateDraft({ opinion_prompt: event.target.value })}
                          rows={12}
                          placeholder="Leave empty to use the built-in opinion prompt."
                          className={TEXTAREA_CLASS}
                        />
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </motion.div>
    </>
  );
}
