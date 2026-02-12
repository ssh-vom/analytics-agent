<script lang="ts">
  export let artifactName = "";
  export let columns: string[] = [];
  export let rows: string[][] = [];
  export let rowCount = 0;
  export let previewCount = 0;
  export let truncated = false;
  export let stickyHeader = false;
  export let variant: "inline" | "floating" = "inline";

  type RiskLevel = "critical" | "high" | "medium" | "low";

  interface RiskSummary {
    critical: number;
    high: number;
    medium: number;
    low: number;
    flagged: number;
  }

  interface TopMetric {
    entity: string;
    label: string;
    value: number;
  }

  $: normalizedColumns = columns.map((column, index) => {
    const label = String(column ?? "").trim();
    return label.length > 0 ? label : `column_${index + 1}`;
  });

  $: normalizedRows = rows.map((row) => {
    const cells = normalizedColumns.map((_, index) => normalizeCell(row[index]));
    return cells;
  });

  $: numericColumnIndexes = detectNumericColumnIndexes(normalizedColumns, normalizedRows);
  $: riskColumnIndex = findColumnIndex(normalizedColumns, /(risk|severity|priority|status)/i);
  $: riskSummary =
    riskColumnIndex >= 0 ? summarizeRiskColumn(normalizedRows, riskColumnIndex) : null;
  $: topMetric = findTopMetric(normalizedColumns, normalizedRows);

  $: analysisPrimary = buildPrimaryAnalysis();
  $: analysisSecondary = buildSecondaryAnalysis();

  function normalizeCell(value: unknown): string {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value);
  }

  function findColumnIndex(source: string[], pattern: RegExp): number {
    return source.findIndex((column) => pattern.test(column));
  }

  function parseNumeric(value: string): number | null {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }

    const negativeParens = /^\((.*)\)$/.exec(trimmed);
    const normalized = negativeParens ? `-${negativeParens[1]}` : trimmed;
    const cleaned = normalized.replace(/[$,\s]/g, "").replace(/%$/, "");
    if (!cleaned || !/^[-+]?\d*\.?\d+$/.test(cleaned)) {
      return null;
    }

    const parsed = Number(cleaned);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function detectNumericColumnIndexes(sourceColumns: string[], sourceRows: string[][]): Set<number> {
    const indexes = new Set<number>();
    if (sourceRows.length === 0) {
      return indexes;
    }

    for (let columnIndex = 0; columnIndex < sourceColumns.length; columnIndex += 1) {
      let numericCount = 0;
      let populatedCount = 0;

      for (const row of sourceRows) {
        const cell = row[columnIndex] ?? "";
        if (!cell.trim()) {
          continue;
        }
        populatedCount += 1;
        if (parseNumeric(cell) !== null) {
          numericCount += 1;
        }
      }

      if (populatedCount > 0 && numericCount / populatedCount >= 0.75) {
        indexes.add(columnIndex);
      }
    }

    return indexes;
  }

  function classifyRisk(value: string): RiskLevel | null {
    const normalized = value.trim().toLowerCase();
    if (!normalized) {
      return null;
    }
    if (normalized.includes("critical")) {
      return "critical";
    }
    if (normalized.includes("high")) {
      return "high";
    }
    if (normalized.includes("medium")) {
      return "medium";
    }
    if (normalized.includes("low")) {
      return "low";
    }
    return null;
  }

  function summarizeRiskColumn(sourceRows: string[][], columnIndex: number): RiskSummary {
    const summary: RiskSummary = { critical: 0, high: 0, medium: 0, low: 0, flagged: 0 };

    for (const row of sourceRows) {
      const level = classifyRisk(row[columnIndex] ?? "");
      if (!level) {
        continue;
      }
      summary[level] += 1;
      if (level !== "low") {
        summary.flagged += 1;
      }
    }

    return summary;
  }

  function findTopMetric(sourceColumns: string[], sourceRows: string[][]): TopMetric | null {
    const metricIndex = findColumnIndex(
      sourceColumns,
      /(revenue|amount|total|value|cost|sales|profit|loss)/i,
    );
    if (metricIndex < 0) {
      return null;
    }

    const entityIndex =
      findColumnIndex(sourceColumns, /(provider|name|vendor|customer|account|entity|user)/i) >= 0
        ? findColumnIndex(sourceColumns, /(provider|name|vendor|customer|account|entity|user)/i)
        : 0;

    let best: TopMetric | null = null;

    for (const row of sourceRows) {
      const numericValue = parseNumeric(row[metricIndex] ?? "");
      if (numericValue === null) {
        continue;
      }
      if (!best || numericValue > best.value) {
        best = {
          entity: (row[entityIndex] ?? "").trim() || "Top row",
          label: sourceColumns[metricIndex],
          value: numericValue,
        };
      }
    }

    return best;
  }

  function formatMetricValue(label: string, value: number): string {
    const lower = label.toLowerCase();
    if (/(revenue|amount|cost|sales|profit|loss|price)/.test(lower)) {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
    }
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  }

  function buildPrimaryAnalysis(): string {
    if (riskSummary && (riskSummary.critical > 0 || riskSummary.high > 0 || riskSummary.medium > 0)) {
      return `Identified ${riskSummary.flagged} higher-risk row${riskSummary.flagged === 1 ? "" : "s"} requiring attention.`;
    }
    return `Previewing ${previewCount.toLocaleString()} row${previewCount === 1 ? "" : "s"} across ${normalizedColumns.length} columns.`;
  }

  function buildSecondaryAnalysis(): string {
    if (topMetric) {
      const value = formatMetricValue(topMetric.label, topMetric.value);
      return `${topMetric.entity} has the highest ${topMetric.label} at ${value}.`;
    }
    if (riskSummary && riskSummary.critical > 0) {
      return `${riskSummary.critical} row${riskSummary.critical === 1 ? " is" : "s are"} marked critical.`;
    }
    if (artifactName) {
      return `Artifact: ${artifactName}`;
    }
    return "";
  }
</script>

<div class="table-preview" class:floating={variant === "floating"}>
  <section class="analysis-card" class:floating={variant === "floating"}>
    <div class="analysis-kicker">Analysis Snapshot</div>
    <p class="analysis-primary">{analysisPrimary}</p>
    {#if analysisSecondary}
      <p class="analysis-secondary">{analysisSecondary}</p>
    {/if}
    {#if truncated}
      <p class="analysis-note">
        Showing {previewCount.toLocaleString()} of {rowCount.toLocaleString()} rows in this preview.
      </p>
    {/if}
  </section>

  <div class="table-meta">
    <span>{rowCount.toLocaleString()} rows</span>
    <span class="meta-dot">-</span>
    <span>{normalizedColumns.length.toLocaleString()} columns</span>
  </div>

  <div class="table-shell" class:floating={variant === "floating"}>
    <table>
      <thead>
        <tr>
          {#each normalizedColumns as column, columnIndex}
            <th class:numeric={numericColumnIndexes.has(columnIndex)} class:sticky={stickyHeader}>{column}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#if normalizedRows.length === 0}
          <tr>
            <td colspan={normalizedColumns.length || 1} class="empty-cell">No rows to preview</td>
          </tr>
        {:else}
          {#each normalizedRows as row}
            <tr>
              {#each row as cell, cellIndex}
                {@const riskLevel = classifyRisk(cell)}
                <td class:numeric={numericColumnIndexes.has(cellIndex)}>
                  {#if riskLevel}
                    <span class={`risk-pill ${riskLevel}`}>{cell || "-"}</span>
                  {:else}
                    {cell || "-"}
                  {/if}
                </td>
              {/each}
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>
</div>

<style>
  .table-preview {
    display: flex;
    flex-direction: column;
  }

  .analysis-card {
    padding: var(--space-3);
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    background: linear-gradient(180deg, rgba(62, 207, 142, 0.08) 0%, rgba(62, 207, 142, 0.02) 100%);
  }

  .analysis-card.floating {
    border-color: var(--border-accent);
    box-shadow: var(--shadow-sm);
  }

  .analysis-kicker {
    font-size: 10px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--accent-green);
    margin-bottom: var(--space-1);
  }

  .analysis-primary,
  .analysis-secondary,
  .analysis-note {
    margin: 0;
    color: var(--text-secondary);
    line-height: 1.45;
  }

  .analysis-primary {
    font-size: 14px;
    color: var(--text-primary);
  }

  .analysis-secondary {
    margin-top: var(--space-2);
    font-size: 13px;
  }

  .analysis-note {
    margin-top: var(--space-2);
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
  }

  .table-meta {
    margin: var(--space-2) 0;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .meta-dot {
    color: var(--border-medium);
  }

  .table-shell {
    border: 1px solid var(--border-soft);
    border-radius: var(--radius-md);
    overflow: auto;
    max-height: 360px;
    background: var(--surface-0);
  }

  .table-shell.floating {
    max-height: min(72vh, 760px);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    min-width: 540px;
    font-size: 13px;
  }

  th,
  td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-soft);
    text-align: left;
    white-space: nowrap;
  }

  th {
    position: static;
    background: var(--surface-1);
    color: var(--text-dim);
    font-size: 11px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
  }

  th.sticky {
    position: sticky;
    top: 0;
    z-index: 2;
  }

  td {
    color: var(--text-secondary);
    font-family: var(--font-mono);
  }

  th.numeric,
  td.numeric {
    text-align: right;
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  tbody tr:hover td {
    background: var(--surface-hover);
  }

  .risk-pill {
    display: inline-flex;
    align-items: center;
    border-radius: var(--radius-sm);
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .risk-pill.critical {
    background: rgba(239, 83, 80, 0.14);
    color: #ff8b89;
  }

  .risk-pill.high {
    background: rgba(239, 83, 80, 0.1);
    color: #ef9a97;
  }

  .risk-pill.medium {
    background: rgba(229, 160, 75, 0.16);
    color: #f1bc72;
  }

  .risk-pill.low {
    background: var(--surface-2);
    color: var(--text-dim);
  }

  .empty-cell {
    text-align: center;
    color: var(--text-dim);
    font-family: var(--font-body);
    font-size: 13px;
    padding: var(--space-5);
  }

  @media (max-width: 1100px) {
    .table-shell {
      max-height: 300px;
    }

    .table-shell.floating {
      max-height: min(65vh, 620px);
    }
  }
</style>
