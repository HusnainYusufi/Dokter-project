"use client";

import { motion } from "framer-motion";

import BrandLogo from "@/components/BrandLogo";
import ComplianceStrip from "@/components/ComplianceStrip";
import LoginForm from "@/components/LoginForm";

export default function LoginScreen() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(45,212,191,0.24),_transparent_26%),radial-gradient(circle_at_right,_rgba(59,130,246,0.22),_transparent_32%),linear-gradient(135deg,_#08263d_0%,_#0e4a6f_45%,_#163f77_100%)] px-4 py-8">
      <motion.div
        className="absolute inset-0 bg-[linear-gradient(120deg,_rgba(255,255,255,0.03)_0%,_rgba(255,255,255,0)_40%,_rgba(255,255,255,0.04)_100%)]"
        animate={{ x: ["-4%", "4%", "-4%"] }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
      />

      <div className="pointer-events-none absolute inset-0 opacity-40">
        <motion.div
          className="absolute left-[8%] top-[15%] h-48 w-48 rounded-full border border-white/10"
          animate={{ y: [0, -16, 0], x: [0, 10, 0] }}
          transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute right-[10%] top-[18%] h-72 w-72 rounded-full border border-white/10"
          animate={{ y: [0, 20, 0], x: [0, -12, 0] }}
          transition={{ duration: 14, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute bottom-[12%] left-[16%] h-64 w-64 rounded-full border border-white/10"
          animate={{ y: [0, -18, 0], x: [0, 14, 0] }}
          transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute left-[18%] top-[28%] h-56 w-56 rounded-full bg-cyan-400/10 blur-3xl"
          animate={{ scale: [1, 1.1, 1], opacity: [0.18, 0.28, 0.18] }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute bottom-[16%] right-[12%] h-64 w-64 rounded-full bg-blue-500/10 blur-3xl"
          animate={{ scale: [1.04, 0.96, 1.04], opacity: [0.16, 0.3, 0.16] }}
          transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      <div className="relative mx-auto flex min-h-[calc(100vh-4rem)] max-w-6xl items-center justify-center">
        <div className="grid w-full max-w-5xl gap-5 rounded-[36px] border border-white/15 bg-white/8 p-4 shadow-2xl shadow-slate-950/35 backdrop-blur-xl lg:grid-cols-[0.94fr_1.06fr] lg:p-5">
          <motion.section
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.4 }}
            className="flex rounded-[30px] border border-white/10 bg-white/10 px-6 py-7 text-white"
          >
            <div className="my-auto w-full">
              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.35, delay: 0.12 }}
                className="mb-8"
              >
                <BrandLogo theme="dark" />
              </motion.div>

              <div className="mb-5 flex items-center gap-3">
                <div className="h-px flex-1 bg-white/15" />
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-100/80">Security and compliance</p>
                <div className="h-px flex-1 bg-white/15" />
              </div>
              <ComplianceStrip variant="dark" />
            </div>
          </motion.section>

          <motion.section
            initial={{ opacity: 0, x: 24, y: 10 }}
            animate={{ opacity: 1, x: 0, y: 0 }}
            transition={{ duration: 0.4, delay: 0.06 }}
            className="flex items-center rounded-[30px] bg-white px-6 py-7 shadow-xl shadow-slate-950/15 md:px-8"
          >
            <div className="mx-auto w-full max-w-md">
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.1 }}
                className="mb-8 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3"
              >
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Secure login</p>
                  <p className="mt-1 text-sm font-medium text-slate-700">Protected portal access</p>
                </div>
              </motion.div>

              <LoginForm />

              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.2 }}
                className="mt-8"
              >
                <p className="text-center text-xs font-medium text-slate-400">Authorized personnel only</p>
              </motion.div>
            </div>
          </motion.section>
        </div>
      </div>
    </div>
  );
}
