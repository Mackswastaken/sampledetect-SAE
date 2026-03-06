"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";

type Top5 = { score: number; library_id: string; filename: string };

type AudfBest = {
  match_path: string;
  match_filename: string;
  offset_sec: number;
  common_hashes: number;
  total_common_candidates: number;
  rank: number;
};

type MonitorIncident = {
  id: string;
  created_at: string | null;
  inbox_filename: string;
  inbox_path: string;
  mode: string;
  match_filename: string | null;
  match_path: string | null;
  common_hashes: number | null;
  rank: number | null;
  offset_sec: string | null;
  email_sent: string | null; // "true"/"false"
  email_reason: string | null; // message_id or reason
};

type Asset = {
  id: string;
  original_filename: string;
  stored_path: string;
  file_size_bytes: number;
  created_at: string | null;

  fingerprint_status?: string;
  fingerprint_path?: string | null;
  fingerprint_error?: string | null;

  match_filename?: string | null;
  match_library_id?: string | null;
  top_5?: Top5[];

  spectrogram_url?: string | null;

  proof_hash?: string | null;
  tx_hash?: string | null;
  explorer_url?: string | null;

  email_sent?: boolean | null;
  email_status_code?: number | null;
  email_reason?: string | null;

  audf_mode?: "normal" | "vr" | null;
  audf_best?: AudfBest | null;
  audf_query_path?: string | null;
  audf_error?: string | null;
};

function formatMB(bytes: number) {
  const mb = bytes / 1024 / 1024;
  return `${mb.toFixed(2)} MB`;
}

function GlowButton({
  children,
  onClick,
  disabled,
  title,
  variant = "white",
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  variant?: "white" | "ghost" | "danger";
  type?: "button" | "submit";
}) {
  const base: React.CSSProperties = {
    borderRadius: 14,
    padding: "10px 14px",
    fontWeight: 800,
    fontSize: 13,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
    transition: "transform 120ms ease, box-shadow 160ms ease, background 160ms ease",
    userSelect: "none",
    border: "1px solid rgba(255,255,255,0.35)",
  };

  const variants: Record<string, React.CSSProperties> = {
    // Moodboard: white button, black text, rounded, 3D feel, glow on hover, dark grey on click :contentReference[oaicite:2]{index=2}
    white: {
      background: "linear-gradient(180deg, rgba(255,255,255,0.98), rgba(235,235,235,0.92))",
      color: "#0B0F16",
      boxShadow: "0 10px 18px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.75)",
    },
    ghost: {
      background: "rgba(255,255,255,0.08)",
      color: "rgba(255,255,255,0.92)",
      border: "1px solid rgba(255,255,255,0.18)",
      boxShadow: "0 10px 18px rgba(0,0,0,0.25)",
    },
    danger: {
      background: "rgba(255,255,255,0.12)",
      color: "rgba(255,255,255,0.92)",
      border: "1px solid rgba(255,80,80,0.35)",
      boxShadow: "0 10px 18px rgba(0,0,0,0.25)",
    },
  };

  return (
    <button
      type={type}
      title={title}
      onClick={disabled ? undefined : onClick}
      style={{ ...base, ...variants[variant] }}
      onMouseEnter={(e) => {
        if (disabled) return;
        if (variant === "white") {
          e.currentTarget.style.boxShadow =
            "0 0 0 1px rgba(255,255,255,0.25), 0 14px 26px rgba(0,0,0,0.40), 0 0 26px rgba(255,255,255,0.22)";
        }
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        if (variant === "white") {
          e.currentTarget.style.boxShadow =
            "0 10px 18px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.75)";
        }
        e.currentTarget.style.transform = "scale(1)";
        if (variant === "white") {
          e.currentTarget.style.background =
            "linear-gradient(180deg, rgba(255,255,255,0.98), rgba(235,235,235,0.92))";
        }
      }}
      onMouseDown={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = "scale(0.98)";
        if (variant === "white") e.currentTarget.style.background = "rgba(55,55,55,0.95)";
        if (variant === "white") e.currentTarget.style.color = "rgba(255,255,255,0.92)";
      }}
      onMouseUp={(e) => {
        if (disabled) return;
        e.currentTarget.style.transform = "scale(1)";
        if (variant === "white") {
          e.currentTarget.style.background =
            "linear-gradient(180deg, rgba(255,255,255,0.98), rgba(235,235,235,0.92))";
          e.currentTarget.style.color = "#0B0F16";
        }
      }}
    >
      {children}
    </button>
  );
}

function Pill({
  text,
  kind = "neutral",
}: {
  text: string;
  kind?: "neutral" | "good" | "warn" | "bad";
}) {
  const styles: Record<string, React.CSSProperties> = {
    neutral: { background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.14)" },
    good: { background: "rgba(34,197,94,0.18)", border: "1px solid rgba(34,197,94,0.35)" },
    warn: { background: "rgba(245,158,11,0.18)", border: "1px solid rgba(245,158,11,0.35)" },
    bad: { background: "rgba(239,68,68,0.18)", border: "1px solid rgba(239,68,68,0.35)" },
  };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 10px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 800,
        color: "rgba(255,255,255,0.90)",
        ...styles[kind],
      }}
    >
      {text}
    </span>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        borderRadius: 18,
        padding: 16,
        border: "1px solid rgba(255,255,255,0.10)",
        background: "rgba(0,0,0,0.35)",
        boxShadow: "0 18px 40px rgba(0,0,0,0.45)",
        backdropFilter: "blur(10px)",
      }}
    >
      {children}
    </div>
  );
}

export default function Home() {
  const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";

  const [file, setFile] = useState<File | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [incidents, setIncidents] = useState<MonitorIncident[]>([]);
  const [msg, setMsg] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const stats = useMemo(() => {
    const detected = incidents.filter((i) => (i.rank ?? 999) === 0 && (i.common_hashes ?? 0) >= 200).length;
    return { uploads: assets.length, incidents: incidents.length, detected };
  }, [assets.length, incidents]);

  function pickClientOnly(a: Asset) {
    return {
      match_filename: a.match_filename,
      match_library_id: a.match_library_id,
      top_5: a.top_5,
      spectrogram_url: a.spectrogram_url,
      proof_hash: a.proof_hash,
      tx_hash: a.tx_hash,
      explorer_url: a.explorer_url,
      email_sent: a.email_sent,
      email_status_code: a.email_status_code,
      email_reason: a.email_reason,
      audf_mode: a.audf_mode,
      audf_best: a.audf_best,
      audf_query_path: a.audf_query_path,
      audf_error: a.audf_error,
    };
  }

  async function loadAssets() {
    const res = await fetch(`${API}/assets`);
    if (!res.ok) throw new Error("Failed to load assets");
    const data = (await res.json()) as Asset[];

    setAssets((prev) => {
      const map = new Map(prev.map((a) => [a.id, a]));
      return data.map((a) => {
        const old = map.get(a.id);
        return old ? { ...a, ...pickClientOnly(old) } : a;
      });
    });
  }

  async function loadIncidents() {
    const res = await fetch(`${API}/monitor/incidents`);
    if (!res.ok) throw new Error("Failed to load incidents");
    const data = (await res.json()) as MonitorIncident[];
    setIncidents(data);
  }

  useEffect(() => {
    loadAssets().catch((e) => setMsg(e.message));
    loadIncidents().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setMsg("");
    setBusyId("upload");

    try {
      const form = new FormData();
      form.append("file", file);

      const res = await fetch(`${API}/upload`, { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Upload failed (${res.status})`);

      setMsg("✅ Upload successful");
      setFile(null);
      await loadAssets();
    } catch (err: any) {
      setMsg(err?.message || "Upload failed");
    } finally {
      setBusyId(null);
    }
  }

  async function rebuildAudfIndex() {
    setMsg("");
    setBusyId("audf-index");
    try {
      const res = await fetch(`${API}/audfprint/index`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Index failed (${res.status})`);
      setMsg(`✅ audfprint index rebuilt (files: ${data.files_listed})`);
    } catch (err: any) {
      setMsg(err?.message || "Index failed");
    } finally {
      setBusyId(null);
    }
  }

  async function clearUploads() {
    setMsg("");
    setBusyId("clear-uploads");
    try {
      const res = await fetch(`${API}/assets/clear`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Clear failed (${res.status})`);
      setAssets([]);
      setMsg(`✅ Cleared uploads (deleted: ${data.deleted_assets})`);
    } catch (err: any) {
      setMsg(err?.message || "Clear failed");
    } finally {
      setBusyId(null);
    }
  }

  async function fingerprint(assetId: string) {
    setMsg("");
    setBusyId("fp-" + assetId);
    try {
      const res = await fetch(`${API}/assets/${assetId}/fingerprint`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Fingerprint failed (${res.status})`);
      setMsg("✅ Fingerprint created");
      await loadAssets();
    } catch (err: any) {
      setMsg(err?.message || "Fingerprint failed");
      await loadAssets();
    } finally {
      setBusyId(null);
    }
  }

  async function matchMVP(assetId: string) {
    setMsg("");
    setBusyId("match-" + assetId);
    try {
      const res = await fetch(`${API}/assets/${assetId}/match`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Match failed (${res.status})`);

      const best = data.top_5?.[0];
      setAssets((prev) =>
        prev.map((a) =>
          a.id === assetId
            ? {
                ...a,
                match_filename: best?.filename || null,
                match_library_id: best?.library_id || null,
                top_5: data.top_5 || [],
              }
            : a
        )
      );

      setMsg(`ℹ️ MVP Match: ${best?.filename || "—"}`);
    } catch (err: any) {
      setMsg(err?.message || "Match failed");
    } finally {
      setBusyId(null);
    }
  }

  async function audfMatch(assetId: string, mode: "normal" | "vr" = "vr") {
    setMsg("");
    setBusyId(`audf-${mode}-` + assetId);

    setAssets((prev) =>
      prev.map((a) =>
        a.id === assetId ? { ...a, audf_mode: mode, audf_best: null, audf_query_path: null, audf_error: null } : a
      )
    );

    try {
      const url = `${API}/audfprint/match?asset_id=${encodeURIComponent(assetId)}&mode=${encodeURIComponent(mode)}`;
      const res = await fetch(url, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `audfprint match failed (${res.status})`);

      setAssets((prev) =>
        prev.map((a) =>
          a.id === assetId
            ? { ...a, audf_mode: mode, audf_best: data.best || null, audf_query_path: data.query_path || null }
            : a
        )
      );

      if (data.best?.match_filename) setMsg(`✅ Detected: ${data.best.match_filename}`);
      else setMsg("audfprint ran (no best match found)");
    } catch (err: any) {
      setAssets((prev) =>
        prev.map((a) => (a.id === assetId ? { ...a, audf_error: err?.message || "audfprint failed" } : a))
      );
      setMsg(err?.message || "audfprint match failed");
    } finally {
      setBusyId(null);
    }
  }

  async function spectrogram(assetId: string) {
    setMsg("");
    setBusyId("spec-" + assetId);
    try {
      const res = await fetch(`${API}/assets/${assetId}/spectrogram`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Spectrogram failed (${res.status})`);
      const url = data.spectrogram_url as string | undefined;
      setAssets((prev) => prev.map((a) => (a.id === assetId ? { ...a, spectrogram_url: url || null } : a)));
      setMsg("✅ Spectrogram created");
      if (url) window.open(url, "_blank");
    } catch (err: any) {
      setMsg(err?.message || "Spectrogram failed");
    } finally {
      setBusyId(null);
    }
  }

  async function resolveLibraryIdByPath(matchPath: string): Promise<{ id: string; filename: string } | null> {
    const res = await fetch(`${API}/library`);
    if (!res.ok) return null;
    const tracks = (await res.json()) as { id: string; filename: string; stored_path: string }[];
    const found = tracks.find((t) => t.stored_path === matchPath);
    return found ? { id: found.id, filename: found.filename } : null;
  }

  async function recordProof(assetId: string) {
    setMsg("");
    setBusyId(`proof-` + assetId);

    const asset = assets.find((x) => x.id === assetId);
    const matchPath = asset?.audf_best?.match_path;
    if (!matchPath) {
      setMsg("Run Audfprint Match (VR) first so we know what to prove.");
      setBusyId(null);
      return;
    }

    try {
      const lib = await resolveLibraryIdByPath(matchPath);
      if (!lib) throw new Error("Could not resolve library_id. Run /library/index and try again.");

      const url = `${API}/proofs/record?asset_id=${encodeURIComponent(assetId)}&library_id=${encodeURIComponent(lib.id)}`;
      const res = await fetch(url, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Record proof failed (${res.status})`);

      setAssets((prev) =>
        prev.map((a) =>
          a.id === assetId
            ? {
                ...a,
                proof_hash: data.proof_hash || null,
                tx_hash: data.tx_hash || null,
                explorer_url: data.explorer_url || null,
                email_sent: data.email?.sent ?? null,
                email_status_code: data.email?.status_code ?? null,
                email_reason: data.email?.message_id ?? data.email?.reason ?? null,
              }
            : a
        )
      );

      setMsg(`✅ Proof recorded (tx: ${String(data.tx_hash || "").slice(0, 12)}...)`);
    } catch (err: any) {
      setMsg(err?.message || "Record proof failed");
    } finally {
      setBusyId(null);
    }
  }

  async function scanInbox() {
    setMsg("");
    setBusyId("monitor-scan");
    try {
      const res = await fetch(`${API}/monitor/scan?mode=vr`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Monitor scan failed (${res.status})`);
      setMsg(`✅ Monitor scan (scanned=${data.scanned}, incidents=${data.incidents}, errors=${data.errors})`);
      await loadIncidents();
    } catch (err: any) {
      setMsg(err?.message || "Monitor scan failed");
    } finally {
      setBusyId(null);
    }
  }

  async function refreshIncidents() {
    setMsg("");
    setBusyId("monitor-refresh");
    try {
      await loadIncidents();
      setMsg("✅ Incidents refreshed");
    } catch (err: any) {
      setMsg(err?.message || "Refresh failed");
    } finally {
      setBusyId(null);
    }
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setMsg("✅ Copied");
    } catch {
      setMsg("Copy failed (browser blocked clipboard)");
    }
  }

  function audfStatus(a: Asset): { label: string; kind: "good" | "warn" | "bad" } | null {
    if (a.audf_error) return { label: "Error", kind: "bad" };
    if (!a.audf_best) return null;
    const ok = a.audf_best.common_hashes >= 200 && a.audf_best.rank === 0;
    return ok ? { label: "Detected", kind: "good" } : { label: "Possible", kind: "warn" };
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        padding: 24,
        // Moodboard: sea green + electric blue + black gradient :contentReference[oaicite:3]{index=3}
        background:
          "radial-gradient(900px 520px at 20% 10%, rgba(34,197,94,0.22), transparent 65%), radial-gradient(900px 520px at 80% 20%, rgba(59,130,246,0.26), transparent 60%), linear-gradient(180deg, #02050A 0%, #000000 70%, #02050A 100%)",
        color: "rgba(255,255,255,0.92)",
        fontFamily:
          'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", "Liberation Sans", sans-serif',
      }}
    >
      {/* top-left logo */}
      <div style={{ position: "fixed", top: 18, left: 18, zIndex: 10, opacity: 0.96 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Image
            src="/mirxflow-logo.png"
            alt="Mirxflow"
            width={44}
            height={44}
            style={{ borderRadius: 10 }}
          />
          <div style={{ fontWeight: 900, letterSpacing: 0.5 }}>MIRXFLOW</div>
        </div>
      </div>

      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        {/* Centered hero layout (title + upload under it) :contentReference[oaicite:4]{index=4} */}
        <div style={{ textAlign: "center", paddingTop: 44, paddingBottom: 18 }}>
          <div style={{ fontSize: 44, fontWeight: 900, letterSpacing: 0.2 }}>SampleDetect SAE</div>
          <div style={{ marginTop: 10, color: "rgba(255,255,255,0.70)" }}>
            Upload → audfprint Match (VR) → Proof + Email → Monitor Scan.
          </div>

          <div style={{ marginTop: 18, display: "grid", gap: 12, justifyItems: "center" }}>
            <form onSubmit={onUpload} style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", justifyContent: "center" }}>
              <input
                type="file"
                accept="audio/*"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                style={{
                  width: 360,
                  maxWidth: "90vw",
                  padding: 12,
                  borderRadius: 16,
                  border: "1px solid rgba(255,255,255,0.16)",
                  background: "rgba(255,255,255,0.08)",
                  color: "rgba(255,255,255,0.88)",
                }}
              />
              <GlowButton type="submit" disabled={!file || busyId === "upload"} variant="white">
                {busyId === "upload" ? "Uploading..." : "Upload"}
              </GlowButton>
            </form>

            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
              <GlowButton onClick={rebuildAudfIndex} disabled={busyId === "audf-index"} variant="white">
                {busyId === "audf-index" ? "Rebuilding..." : "Rebuild audfprint index"}
              </GlowButton>

              <GlowButton onClick={scanInbox} disabled={busyId === "monitor-scan"} variant="white" title="Scans D:\\SampleDetectStorage\\monitor_inbox">
                {busyId === "monitor-scan" ? "Scanning..." : "Scan Monitor Inbox"}
              </GlowButton>

              <GlowButton onClick={refreshIncidents} disabled={busyId === "monitor-refresh"} variant="white">
                {busyId === "monitor-refresh" ? "Refreshing..." : "Refresh Incidents"}
              </GlowButton>

              <GlowButton onClick={clearUploads} disabled={busyId === "clear-uploads"} variant="white" title="Clears recent uploads only (keeps library intact)">
                {busyId === "clear-uploads" ? "Clearing..." : "Clear Recent Uploads"}
              </GlowButton>
            </div>

            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center", marginTop: 6 }}>
              <Pill text={`Uploads: ${stats.uploads}`} kind="neutral" />
              <Pill text={`Incidents: ${stats.incidents}`} kind="neutral" />
              <Pill text={`Detected: ${stats.detected}`} kind="good" />
            </div>

            {msg && (
              <div
                style={{
                  width: "min(760px, 92vw)",
                  padding: 12,
                  borderRadius: 16,
                  border: "1px solid rgba(255,255,255,0.14)",
                  background: "rgba(0,0,0,0.30)",
                  boxShadow: "0 18px 40px rgba(0,0,0,0.35)",
                }}
              >
                {msg}
              </div>
            )}
          </div>
        </div>

        {/* Monitor Incidents */}
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <div style={{ fontSize: 20, fontWeight: 900 }}>Monitor Incidents</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.70)" }}>
              Files dropped into <b>D:\SampleDetectStorage\monitor_inbox</b> appear here.
            </div>
          </div>

          {incidents.length === 0 ? (
            <div style={{ marginTop: 10, color: "rgba(255,255,255,0.70)" }}>No incidents yet.</div>
          ) : (
            <div style={{ overflowX: "auto", marginTop: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", fontSize: 12, color: "rgba(255,255,255,0.70)" }}>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Time</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Inbox file</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Matched beat</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Hashes</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Rank</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Email</th>
                    <th style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.10)" }}>Message ID</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.map((r) => {
                    const detected = (r.rank ?? 999) === 0 && (r.common_hashes ?? 0) >= 200;
                    return (
                      <tr key={r.id} style={{ fontSize: 13 }}>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)", whiteSpace: "nowrap" }}>
                          {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                        </td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>{r.inbox_filename}</td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                          {r.match_filename ? (
                            <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                              <span style={{ fontWeight: 900 }}>{r.match_filename}</span>
                              <Pill text={detected ? "Detected ✅" : "Possible ⚠️"} kind={detected ? "good" : "warn"} />
                            </span>
                          ) : (
                            <Pill text="No match" kind="neutral" />
                          )}
                        </td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>{r.common_hashes ?? "—"}</td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>{r.rank ?? "—"}</td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                          <Pill text={r.email_sent === "true" ? "sent ✅" : "no ❌"} kind={r.email_sent === "true" ? "good" : "bad"} />
                        </td>
                        <td style={{ padding: 10, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                          {r.email_reason ? (
                            <GlowButton variant="ghost" onClick={() => copyText(r.email_reason!)} title="Copy SendGrid message id">
                              Copy ID
                            </GlowButton>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div style={{ height: 14 }} />

        {/* Recent Uploads */}
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <div style={{ fontSize: 20, fontWeight: 900 }}>Recent Uploads</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.70)" }}>
              Recommended flow: <b>Audfprint Match (VR)</b> → <b>Record Proof</b>.
            </div>
          </div>

          {assets.length === 0 ? (
            <div style={{ marginTop: 10, color: "rgba(255,255,255,0.70)" }}>No uploads yet.</div>
          ) : (
            <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
              {assets.map((a) => {
                const st = audfStatus(a);
                return (
                  <div
                    key={a.id}
                    style={{
                      borderRadius: 18,
                      padding: 14,
                      border: "1px solid rgba(255,255,255,0.10)",
                      background: "rgba(255,255,255,0.04)",
                      display: "grid",
                      gap: 10,
                    }}
                  >
                    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", justifyContent: "space-between" }}>
                      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                        <div style={{ fontWeight: 900 }}>{a.original_filename}</div>
                        <Pill text={formatMB(a.file_size_bytes)} kind="neutral" />
                        {st && <Pill text={st.label} kind={st.kind} />}
                      </div>

                      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                        <GlowButton variant="white" onClick={() => fingerprint(a.id)} disabled={busyId === "fp-" + a.id}>
                          {busyId === "fp-" + a.id ? "Fingerprinting..." : "Fingerprint"}
                        </GlowButton>

                        <GlowButton variant="white" onClick={() => matchMVP(a.id)} disabled={busyId === "match-" + a.id} title="Old MVP matcher (string similarity)">
                          {busyId === "match-" + a.id ? "Matching..." : "Match (MVP)"}
                        </GlowButton>

                        <GlowButton variant="white" onClick={() => audfMatch(a.id, "vr")} disabled={busyId === `audf-vr-` + a.id}>
                          {busyId === `audf-vr-` + a.id ? "Detecting..." : "Audfprint Match (VR)"}
                        </GlowButton>

                        <GlowButton variant="white" onClick={() => audfMatch(a.id, "normal")} disabled={busyId === `audf-normal-` + a.id}>
                          {busyId === `audf-normal-` + a.id ? "Detecting..." : "Audfprint Match"}
                        </GlowButton>

                        <GlowButton variant="white" onClick={() => spectrogram(a.id)} disabled={busyId === "spec-" + a.id}>
                          {busyId === "spec-" + a.id ? "Generating..." : "Spectrogram"}
                        </GlowButton>

                        <GlowButton variant="white" onClick={() => recordProof(a.id)} disabled={busyId === `proof-` + a.id}>
                          {busyId === `proof-` + a.id ? "Recording..." : "Record Proof"}
                        </GlowButton>
                      </div>
                    </div>

                    {a.audf_error && <div style={{ color: "rgba(255,120,120,0.95)", fontWeight: 900 }}>Error: {a.audf_error}</div>}

                    {a.audf_best && (
                      <div style={{ display: "grid", gap: 6 }}>
                        <div>
                          <span style={{ color: "rgba(255,255,255,0.70)" }}>Match:</span>{" "}
                          <span style={{ fontWeight: 900 }}>{a.audf_best.match_filename}</span>
                        </div>
                        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                          <Pill text={`common_hashes: ${a.audf_best.common_hashes}`} kind="neutral" />
                          <Pill text={`rank: ${a.audf_best.rank}`} kind="neutral" />
                          <Pill text={`offset: ${a.audf_best.offset_sec}s`} kind="neutral" />
                        </div>
                      </div>
                    )}

                    {a.explorer_url && (
                      <div style={{ fontSize: 13 }}>
                        <a
                          href={a.explorer_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "rgba(120,210,255,0.95)", textDecoration: "underline", fontWeight: 900 }}
                        >
                          View Blockchain Proof (PolygonScan)
                        </a>
                        {a.tx_hash && <span style={{ marginLeft: 10, color: "rgba(255,255,255,0.65)" }}>tx: {a.tx_hash.slice(0, 12)}…</span>}
                      </div>
                    )}

                    {a.email_sent !== undefined && a.email_sent !== null && (
                      <div style={{ fontSize: 12, color: "rgba(255,255,255,0.70)" }}>
                        Email: {a.email_sent ? "sent ✅" : `not sent ❌ (${a.email_reason || "unknown"})`}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <div style={{ height: 40 }} />
      </div>
    </main>
  );
}