"use client";

import { motion } from "framer-motion";

import RuleStudioWorkspace from "@/components/RuleStudioWorkspace";

export default function RuleStudioPage() {
  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28 }}
        className="space-y-6"
      >
        <div className="rounded-[32px] border border-slate-200 bg-white px-6 py-6 shadow-sm md:px-8">
          <div className="max-w-3xl">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Rule Studio</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Business rules, without code changes</h2>
            <p className="mt-3 text-sm leading-7 text-slate-500">
              Manage the golden rules and per-document-type behavior the AI follows. Create reusable configurations,
              attach rules to document types (built-in or your own), and pick a configuration when starting an
              extraction. Edits create a new version - completed extractions always keep the rules they ran with.
            </p>
          </div>
        </div>

        <RuleStudioWorkspace />
      </motion.section>
    </div>
  );
}
