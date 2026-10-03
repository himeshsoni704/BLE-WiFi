import type { Attribution, AttributionFeature, ExplainResponse, Hypothesis } from "../types/api";

const LIKELIHOOD_COLOR: Record<Hypothesis["likelihood"], string> = {
  "more likely": "var(--accent)",
  possible: "var(--text-dim)",
  unlikely: "var(--text-faint)",
};

function fmt(f: AttributionFeature, which: "value" | "typical"): string {
  const note = which === "value" ? f.value_note : f.typical_note;
  if (note) return note;
  const n = which === "value" ? f.value : f.typical;
  const shown = Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2).replace(/\.?0+$/, "");
  return f.unit ? `${shown} ${f.unit}` : shown;
}

function joinNames(names: string[]): string {
  return names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function Chip({ children, title, tone = "var(--text-faint)" }: { children: string; title?: string; tone?: string }) {
  return (
    <span
      title={title}
      className="rounded px-1.5 py-0.5 font-mono"
      style={{ background: "var(--bg-card)", color: tone, fontSize: 10 }}
    >
      {children}
    </span>
  );
}

function Heading({ children }: { children: string }) {
  return (
    <div className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-faint)" }}>
      {children}
    </div>
  );
}

export function AttributionBars({ attribution }: { attribution: Attribution }) {
  if (!attribution.available) {
    return (
      <div className="text-xs" style={{ color: "var(--text-faint)" }}>
        Score breakdown unavailable: {attribution.reason}
      </div>
    );
  }
  const drivers = attribution.features.filter((f) => f.direction === "more_unusual").slice(0, 5);
  const calming = attribution.features.filter((f) => f.direction === "more_typical").slice(0, 2);
  const clear = attribution.would_stop_being_flagged_if_typical;
  const labels = new Map(attribution.features.map((f) => [f.feature, f.label]));
  return (
    <div>
      {drivers.length === 0 ? (
        <div className="text-xs" style={{ color: "var(--text-faint)" }}>
          No single feature stands out: this record is close to the typical one.
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {drivers.map((f) => (
            <div key={f.feature} title={f.meaning}>
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span style={{ color: "var(--text)" }}>{f.label}</span>
                <span style={{ color: "var(--text-faint)" }}>
                  {fmt(f, "value")} <span style={{ opacity: 0.7 }}>vs typical {fmt(f, "typical")}</span>
                </span>
              </div>
              <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full" style={{ background: "var(--bg-card)" }}>
                <div className="h-full rounded-full" style={{ width: `${Math.max(2, f.share_pct)}%`, background: "var(--amber)" }} />
              </div>
              <div className="text-right" style={{ color: "var(--text-faint)", fontSize: 10 }}>
                {f.share_pct.toFixed(0)}% of the unusualness
              </div>
            </div>
          ))}
        </div>
      )}
      {calming.length > 0 && (
        <div className="mt-1 text-xs" style={{ color: "var(--text-faint)" }}>
          Pulling the other way: {calming.map((f) => f.label).join(", ")}.
        </div>
      )}
      {attribution.flagged && clear && (
        <div className="mt-2 text-xs" style={{ color: "var(--text-dim)" }}>
          If {joinNames(clear.features.map((f) => labels.get(f) ?? f))} {clear.features.length > 1 ? "were" : "was"} typical, the
          forest would not have flagged this record.
        </div>
      )}
      <div className="mt-1" style={{ color: "var(--text-faint)", fontSize: 10 }} title={attribution.method}>
        Exact Shapley values against a typical-normal baseline. Explains the model's score, not intent.
      </div>
    </div>
  );
}

export function ExplanationPanel({ res }: { res: ExplainResponse }) {
  const e = res.explanation;
  const sourceById = new Map(e.sources.map((s) => [s.id, s]));
  return (
    <div
      className="mt-3 rounded-lg border-l-2 px-3 py-2 text-xs leading-relaxed"
      style={{ borderColor: "var(--accent)", background: "var(--bg-elevated)", color: "var(--text-dim)" }}
    >
      <div>{e.text}</div>
      <div className="mt-2 flex flex-wrap items-center gap-2" style={{ color: "var(--text-faint)" }}>
        <span>
          via {res.provider.used}
          {res.provider.used !== res.provider.configured ? ` (configured: ${res.provider.configured})` : ""}
          {res.provider.model ? ` · ${res.provider.model}` : ""}
        </span>
        {e.grounding.checked && !e.grounding.passed && (
          <span style={{ color: "var(--amber)" }}>numbers not found in the evidence: {e.grounding.unverified_terms.join(", ")}</span>
        )}
      </div>
      {res.provider.fallback_reason && (
        <div className="mt-1" style={{ color: "var(--amber)" }}>
          {res.provider.configured === "gemini" ? "Gemini's answer was not used: " : ""}
          {res.provider.fallback_reason}. The offline explainer's answer is shown instead.
        </div>
      )}

      {e.hypotheses.length > 0 && (
        <>
          <Heading>Possible causes, most plausible first</Heading>
          <div className="flex flex-col gap-2">
            {e.hypotheses.map((h, i) => (
              <div key={i} className="rounded-md px-2 py-1.5" style={{ background: "var(--bg-card)" }}>
                <div className="flex items-baseline gap-2">
                  <span className="font-medium uppercase" style={{ color: LIKELIHOOD_COLOR[h.likelihood], fontSize: 10 }}>
                    {h.likelihood}
                  </span>
                  <span style={{ color: "var(--text)" }}>{h.cause}</span>
                </div>
                {h.because && <div style={{ color: "var(--text-faint)" }}>{h.because}</div>}
                <div className="mt-1 flex flex-wrap gap-1">
                  {h.evidence.map((x) => (
                    <Chip key={`e-${x}`} title="evidence field">{x}</Chip>
                  ))}
                  {h.sources.map((x) => (
                    <Chip key={`s-${x}`} tone="var(--accent)" title={sourceById.get(x)?.title ?? x}>{x}</Chip>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <Heading>Why the forest scored it as unusual</Heading>
      <AttributionBars attribution={res.attribution} />

      {e.checks.length > 0 && (
        <>
          <Heading>Faculty can check</Heading>
          <ul className="list-disc pl-4">
            {e.checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </>
      )}

      {e.sources.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer" style={{ color: "var(--text-faint)" }}>
            Sources cited ({e.sources.length})
          </summary>
          <div className="mt-1 flex flex-col gap-1.5">
            {e.sources.map((s) => (
              <div key={s.id}>
                <span className="font-mono" style={{ color: "var(--accent)", fontSize: 10 }}>{s.id}</span>{" "}
                <span style={{ color: "var(--text)" }}>{s.title}</span>
                {s.resolution && <span style={{ color: "var(--text-faint)" }}> · {s.resolution.replace(/_/g, " ")}</span>}
                <div style={{ color: "var(--text-faint)" }}>{s.text}</div>
              </div>
            ))}
          </div>
        </details>
      )}

      {res.similar_cases.length > 0 && (
        <div className="mt-2" style={{ color: "var(--text-faint)" }}>
          Similar past cases:{" "}
          {res.similar_cases
            .map((c) => `${c.title} — ${c.resolution.replace(/_/g, " ")} (${(c.similarity * 100).toFixed(0)}%)`)
            .join("; ")}
        </div>
      )}
    </div>
  );
}
