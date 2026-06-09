import { Fragment, type CSSProperties, type Dispatch, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode, type SetStateAction, type WheelEvent } from "react";
import { useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import ReactECharts from "echarts-for-react";
import "./index.css";
import { supabase } from "./supabaseClient";

type Sender = "user" | "assistant" | "progress";

interface RunCost {
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  ai_cost_usd?: number;
  surcharge_usd?: number;
  final_cost_usd?: number;
}

interface ChatMessage {
  id: string;
  sender: Sender;
  content: string;
  runCost?: RunCost;
}

interface PendingMessage {
  id: string;
  userId: string;
  taskId?: string;
}

interface ConversationSnapshotResponse {
  conversation_id?: unknown;
  messages?: unknown;
  run_status?: unknown;
  latest_progress?: unknown;
  latest_error?: unknown;
  task_id?: unknown;
  final_response?: unknown;
  pending_user_message?: unknown;
  pending_user_mode?: unknown;
  latest_export_url?: unknown;
  latest_export_status?: unknown;
  model_builder_state?: unknown;
}

interface ChatAcceptedResponse {
  conversation_id?: unknown;
  task_id?: unknown;
  run_status?: unknown;
  latest_progress?: unknown;
}

interface ModellingProject {
  id: string;
  name: string;
  question: string;
  status: "draft" | "active" | "archived";
  conversationId: string;
  modelBuilderState: ModelBuilderState;
  activeValidatedVariableIds: string[];
  updatedAt: string;
}

interface ValidatedVariable {
  id: string;
  name: string;
  label: string;
  sourceName: string;
  metric: string;
  unit: string;
  geography: string;
  frequency: string;
  seasonalTreatment: string;
  transformSummary: string;
  contentsSummary?: string;
  contents?: Record<string, unknown>;
  validationStatus: "candidate" | "validated" | "rejected";
}

interface ModelAssumption {
  id: string;
  variableId?: string;
  nodeId?: string;
  label: string;
  valueText: string;
  method?: string;
  inputs?: string[];
  output?: string;
  logicSummary?: string;
  parameters?: Record<string, unknown>;
  calculationLogic?: Record<string, unknown>;
  calculationSpec?: Record<string, unknown>;
}

interface ModelNode {
  id: string;
  label: string;
  nodeType: "variable" | "assumption" | "calculation" | "result";
  variableId?: string;
  assumptionId?: string;
  expression?: string;
  method?: string;
  inputs?: string[];
  output?: string;
  sourceCalculationId?: string;
  logicSummary?: string;
  tooltip?: string;
  parameters?: Record<string, unknown>;
  calculationLogic?: Record<string, unknown>;
  calculationSpec?: Record<string, unknown>;
  positionX?: number;
  positionY?: number;
}

interface ModelEdge {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;
  operator?: string;
  label?: string;
}

interface ModelBuilderSpec {
  variables?: Array<Partial<ValidatedVariable>>;
  assumptions?: Array<Partial<ModelAssumption> & { variable?: string }>;
  nodes?: Array<Partial<ModelNode>>;
  edges?: Array<Partial<ModelEdge> & { from?: string; to?: string }>;
}

interface ModelBuilderState {
  variables: ValidatedVariable[];
  assumptions: ModelAssumption[];
  nodes: ModelNode[];
  edges: ModelEdge[];
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const STORAGE_KEY = "abs-analyst-session";
const ACTIVE_PROJECT_STORAGE_KEY = "abs-active-project";
const MAX_POLL_FAILURES = 20;
const USD_TO_AUD_RATE = 1.398;
const DEFAULT_PROJECTS_PANE_WIDTH = 272;
const COLLAPSED_PROJECTS_PANE_WIDTH = 48;
const MIN_PROJECTS_PANE_WIDTH = 220;
const MAX_PROJECTS_PANE_WIDTH = 520;
const MIN_WORKSPACE_PANE_WIDTH = 320;
const DEFAULT_WORKSPACE_SPLIT_PERCENT = 50;
const PROJECT_SELECT_COLUMNS =
  "id,name,question,status,conversation_id,model_builder_state,model_assumptions,model_graph_state,active_validated_variable_ids,updated_at";
const NISABA_THEME = {
  green: "#234233",
  umber: "#8f6a3a",
  secondaryGreen: "#54745f",
  rust: "#b45f3a",
  text: "#2f352f",
  bodyFont: "IBM Plex Sans, Segoe UI, sans-serif",
  chartText: "rgba(47, 53, 47, 0.78)",
  chartTextSoft: "rgba(47, 53, 47, 0.72)",
  chartTextMuted: "rgba(47, 53, 47, 0.58)",
  chartTextAxis: "rgba(47, 53, 47, 0.62)",
  chartLine: "rgba(47, 53, 47, 0.18)",
  chartGrid: "rgba(47, 53, 47, 0.09)",
  chartPointer: "rgba(47, 53, 47, 0.22)",
  chartPointerFill: "rgba(47, 53, 47, 0.06)",
  chartTooltipBg: "rgba(247, 241, 230, 0.96)",
  chartTooltipBorder: "rgba(71, 56, 37, 0.14)",
} as const;
function createConversationId() {
  if (crypto && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

interface ChartPoint {
  x: string;
  y: number;
}

type ChartType = "line" | "bar" | "area" | "scatter" | "stacked_bar" | "stacked_area";

interface ChartSeries {
  name: string;
  color?: string;
  points: ChartPoint[];
}

interface ChartSpec {
  type?: ChartType;
  title?: string;
  xLabel?: string;
  yLabel?: string;
  series: ChartSeries[];
}

type ContentBlock =
  | { type: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; text: string }
  | { type: "paragraph"; lines: string[] }
  | { type: "list"; items: string[] }
  | { type: "ordered-list"; items: string[] }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "code"; code: string; language: string }
  | { type: "chart"; spec: ChartSpec };

function renderInlineMarkdown(value: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null = null;
  let key = 0;

  while ((match = pattern.exec(value)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(value.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("[")) {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a key={`link-${key++}`} href={linkMatch[2]} target="_blank" rel="noreferrer">
            {linkMatch[1]}
          </a>
        );
      } else {
        nodes.push(token);
      }
    } else if (token.startsWith("`")) {
      nodes.push(<code key={`code-${key++}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={`strong-${key++}`}>{token.slice(2, -2)}</strong>);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < value.length) {
    nodes.push(value.slice(lastIndex));
  }

  return nodes;
}

function isTableLine(value: string) {
  const trimmed = value.trim();
  return trimmed.includes("|") && trimmed.replaceAll("|", "").trim().length > 0;
}

function isTableSeparator(line: string) {
  const cells = line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function parseTableCells(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function parseChartBlock(raw: string): ChartSpec | null {
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.series)) {
      return null;
    }
    const series = parsed.series
      .map((entry: unknown) => {
        if (!entry || typeof entry !== "object" || !Array.isArray((entry as { points?: unknown[] }).points)) {
          return null;
        }
        const typed = entry as { name?: unknown; color?: unknown; points: Array<{ x?: unknown; y?: unknown }> };
        const points = typed.points
          .map((point) => {
            const y = Number(point?.y);
            const x = String(point?.x ?? "");
            if (!x || !Number.isFinite(y)) {
              return null;
            }
            return { x, y };
          })
          .filter((point): point is ChartPoint => point !== null);
        if (!points.length) {
          return null;
        }
        return {
          name: String(typed.name ?? "Series"),
          color: typeof typed.color === "string" ? typed.color : undefined,
          points,
        };
      })
      .filter((entry: ChartSeries | null): entry is ChartSeries => entry !== null);

    if (!series.length) {
      return null;
    }

    const supportedTypes = new Set<ChartType>([
      "line",
      "bar",
      "area",
      "scatter",
      "stacked_bar",
      "stacked_area",
    ]);
    const chartType: ChartType =
      typeof parsed.type === "string" && supportedTypes.has(parsed.type as ChartType)
        ? (parsed.type as ChartType)
        : "line";

    return {
      type: chartType,
      title: typeof parsed.title === "string" ? parsed.title : undefined,
      xLabel: typeof parsed.xLabel === "string" ? parsed.xLabel : undefined,
      yLabel: typeof parsed.yLabel === "string" ? parsed.yLabel : undefined,
      series,
    };
  } catch {
    return null;
  }
}

function maybeChartLanguage(language: string) {
  return language === "chart" || language === "json" || language === "";
}

function parseHeadingLine(value: string) {
  const match = value.trim().match(/^(#{1,6})\s+(.+)$/);
  if (!match) {
    return null;
  }
  return {
    level: match[1].length as 1 | 2 | 3 | 4 | 5 | 6,
    text: match[2].trim(),
  };
}

function parseContentBlocks(value: string): ContentBlock[] {
  const normalized = value.replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const lines = normalized.split("\n");
  const blocks: ContentBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const heading = parseHeadingLine(trimmed);
    if (heading) {
      blocks.push({ type: "heading", level: heading.level, text: heading.text });
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim().toLowerCase();
      index += 1;
      const codeLines: string[] = [];
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      if (language === "model" || language === "model-builder") {
        continue;
      }
      const raw = codeLines.join("\n");
      const spec = maybeChartLanguage(language) ? parseChartBlock(raw) : null;
      if (spec) {
        blocks.push({ type: "chart", spec });
      } else {
        blocks.push({ type: "code", code: raw, language });
      }
      continue;
    }

    if (
      index + 1 < lines.length &&
      isTableLine(line) &&
      isTableSeparator(lines[index + 1])
    ) {
      const headers = parseTableCells(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && isTableLine(lines[index]) && !lines[index].trim().startsWith("```")) {
        rows.push(parseTableCells(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    if (trimmed.startsWith("- ")) {
      const items: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith("- ")) {
        items.push(lines[index].trim().slice(2));
        index += 1;
      }
      blocks.push({ type: "list", items });
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+\.\s+/, ""));
        index += 1;
      }
      blocks.push({ type: "ordered-list", items });
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const candidate = lines[index];
      const candidateTrimmed = candidate.trim();
      if (!candidateTrimmed) {
        break;
      }
      if (
        candidateTrimmed.startsWith("```") ||
        candidateTrimmed.startsWith("- ") ||
        /^\d+\.\s+/.test(candidateTrimmed) ||
        parseHeadingLine(candidateTrimmed)
      ) {
        break;
      }
      if (
        index + 1 < lines.length &&
        isTableLine(candidate) &&
        isTableSeparator(lines[index + 1])
      ) {
        break;
      }
      paragraphLines.push(candidateTrimmed);
      index += 1;
    }
    if (paragraphLines.length === 1) {
      const spec = parseChartBlock(paragraphLines[0]);
      if (spec) {
        blocks.push({ type: "chart", spec });
        continue;
      }
    }
    blocks.push({ type: "paragraph", lines: paragraphLines });
  }

  return blocks;
}

function formatTick(value: number) {
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  if (Math.abs(value) >= 10) {
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ChartBlock({ spec }: { spec: ChartSpec }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const colors = [NISABA_THEME.green, NISABA_THEME.umber, NISABA_THEME.secondaryGreen, NISABA_THEME.rust];
  const chartType = spec.type || "line";
  const isBarLike = chartType === "bar" || chartType === "stacked_bar";
  const isLineLike = chartType === "line" || chartType === "area" || chartType === "stacked_area";
  const isAreaLike = chartType === "area" || chartType === "stacked_area";
  const isStacked = chartType === "stacked_bar" || chartType === "stacked_area";
  const isScatter = chartType === "scatter";
  const allPoints = spec.series.flatMap((series) => series.points);
  const rawXValues = Array.from(new Set(allPoints.map((point) => point.x)));
  const scatterNumericX = isScatter && allPoints.every((point) => Number.isFinite(Number(point.x)));
  const xValues = scatterNumericX
    ? [...rawXValues].sort((a, b) => Number(a) - Number(b))
    : rawXValues;
  const longestXAxisLabelLength = xValues.reduce((max, value) => Math.max(max, value.length), 0);
  const longestSeries = Math.max(...spec.series.map((series) => series.points.length), 0);
  const isNarrow = containerWidth > 0 && containerWidth < 640;
  const useHorizontalBars =
    isBarLike && (isNarrow || xValues.length > 10 || longestXAxisLabelLength > 16);
  const rotateVerticalLabels =
    isBarLike && !useHorizontalBars && (xValues.length > 7 || longestXAxisLabelLength > 12);
  const chartHeight =
    isBarLike && useHorizontalBars
      ? Math.max(360, xValues.length * 28 + 120)
      : 360;

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      setContainerWidth(entry.contentRect.width);
    });
    observer.observe(element);
    setContainerWidth(element.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  const option = {
    animationDuration: 320,
    color: spec.series.map((series, index) => series.color || colors[index % colors.length]),
    textStyle: {
      color: NISABA_THEME.chartText,
      fontFamily: NISABA_THEME.bodyFont,
    },
    grid: useHorizontalBars
      ? {
          top: spec.series.length > 1 ? (spec.title ? 54 : 42) : spec.title ? 20 : 8,
          right: 20,
          bottom: spec.xLabel ? 52 : 22,
          left: Math.min(220, Math.max(110, longestXAxisLabelLength * 7)),
          containLabel: false,
        }
      : {
          top: spec.series.length > 1 ? (spec.title ? 54 : 42) : spec.title ? 20 : 8,
          right: 20,
          bottom: rotateVerticalLabels ? 92 : spec.xLabel ? 54 : 30,
          left: spec.yLabel ? 72 : 58,
          containLabel: false,
        },
    tooltip: {
      trigger: isScatter && scatterNumericX ? "item" : "axis",
      confine: true,
      backgroundColor: NISABA_THEME.chartTooltipBg,
      borderColor: NISABA_THEME.chartTooltipBorder,
      borderWidth: 1,
      textStyle: {
        color: NISABA_THEME.text,
      },
      axisPointer: {
        type: isBarLike ? "shadow" : "line",
        lineStyle: {
          color: NISABA_THEME.chartPointer,
        },
        shadowStyle: {
          color: NISABA_THEME.chartPointerFill,
        },
      },
    },
    legend:
      spec.series.length > 1
        ? {
            top: spec.title ? 18 : 6,
            left: "center",
            icon: "circle",
            itemWidth: 10,
            itemHeight: 10,
            textStyle: {
              color: NISABA_THEME.chartTextSoft,
              fontSize: 12,
            },
          }
        : undefined,
    xAxis: useHorizontalBars
      ? {
          type: "value",
          name: spec.xLabel,
          nameLocation: "middle",
          nameGap: spec.xLabel ? 36 : 0,
          axisLabel: {
            color: NISABA_THEME.chartTextMuted,
            fontSize: 11,
            formatter: (value: number) => formatTick(Number(value)),
          },
          splitLine: {
            lineStyle: {
              color: NISABA_THEME.chartGrid,
              type: [2, 5],
            },
          },
          axisLine: {
            lineStyle: {
              color: NISABA_THEME.chartLine,
            },
          },
          axisTick: {
            show: false,
          },
        }
      : {
          type: scatterNumericX ? "value" : "category",
          data: scatterNumericX ? undefined : xValues,
          name: spec.xLabel,
          nameLocation: "middle",
          nameGap: rotateVerticalLabels ? 78 : spec.xLabel ? 34 : 0,
          axisLabel: {
            color: NISABA_THEME.chartTextMuted,
            fontSize: 11,
            interval: isBarLike ? 0 : "auto",
            hideOverlap: true,
            rotate: rotateVerticalLabels ? -40 : 0,
            width: rotateVerticalLabels ? 96 : 88,
            overflow: "truncate",
            formatter: scatterNumericX ? (value: number) => formatTick(Number(value)) : undefined,
          },
          axisLine: {
            lineStyle: {
              color: NISABA_THEME.chartLine,
            },
          },
          axisTick: {
            show: false,
          },
        },
    yAxis: useHorizontalBars
      ? {
          type: "category",
          data: xValues,
          name: spec.yLabel,
          nameLocation: "middle",
          nameGap: spec.yLabel ? 92 : 0,
          axisLabel: {
            color: NISABA_THEME.chartTextAxis,
            fontSize: 11,
            width: Math.min(200, Math.max(120, longestXAxisLabelLength * 7)),
            overflow: "truncate",
          },
          axisTick: {
            show: false,
          },
          axisLine: {
            show: false,
          },
        }
      : {
          type: "value",
          name: spec.yLabel,
          nameLocation: "middle",
          nameGap: spec.yLabel ? 52 : 0,
          axisLabel: {
            color: NISABA_THEME.chartTextMuted,
            fontSize: 11,
            formatter: (value: number) => formatTick(Number(value)),
          },
          splitLine: {
            lineStyle: {
              color: NISABA_THEME.chartGrid,
              type: [2, 5],
            },
          },
          axisLine: {
            lineStyle: {
              color: NISABA_THEME.chartLine,
            },
          },
          axisTick: {
            show: false,
          },
          min: (value: { min: number; max: number }) =>
            value.min === value.max ? value.min - 1 : value.min - (value.max - value.min) * 0.08,
          max: (value: { min: number; max: number }) =>
            value.min === value.max ? value.max + 1 : value.max + (value.max - value.min) * 0.08,
        },
    series: spec.series.map((series) => {
      const data = isScatter && scatterNumericX
        ? series.points
            .map((point) => [Number(point.x), point.y])
            .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]))
        : xValues.map((xValue) => {
            const match = series.points.find((point) => point.x === xValue);
            return match ? match.y : null;
          });
      return {
        name: series.name,
        type: isBarLike ? "bar" : isScatter ? "scatter" : "line",
        data,
        stack: isStacked ? "total" : undefined,
        barMaxWidth: 28,
        barCategoryGap: spec.series.length > 1 ? "34%" : "42%",
        smooth: isLineLike && !isAreaLike && spec.series.length === 1 ? 0.15 : 0,
        showSymbol: (isLineLike || isScatter) && longestSeries <= 16 && spec.series.length <= 2,
        symbolSize: isScatter ? 9 : 6,
        lineStyle: {
          width: isScatter ? 0 : spec.series.length > 1 ? 2.4 : 2.8,
        },
        areaStyle: isAreaLike ? { opacity: isStacked ? 0.82 : 0.18 } : undefined,
        itemStyle: {
          borderRadius: isBarLike ? [4, 4, 0, 0] : 0,
        },
        emphasis: {
          focus: "series",
        },
      };
    }),
  };

  return (
    <section className="chart-block">
      {spec.title && <h3>{spec.title}</h3>}
      <div ref={containerRef} className="chart-frame">
        <ReactECharts
          option={option}
          notMerge
          lazyUpdate
          style={{ width: "100%", height: `${chartHeight}px` }}
          className="chart-echart"
        />
      </div>
    </section>
  );
}

function AusDataLoader() {
  return (
    <svg
      className="ausdata-loader"
      viewBox="0 0 80 60"
      role="img"
      aria-hidden="true"
    >
      <g className="ausdata-loader-group">
        <rect
          className="ausdata-stroke ausdata-border ausdata-stroke-1"
          x="8"
          y="7"
          width="64"
          height="46"
          rx="10"
          pathLength={1}
        />

        <g className="ausdata-wedge ausdata-stroke-2">
          <line className="ausdata-wedge-line" x1="26" y1="24" x2="39" y2="19" pathLength={1} />
          <path className="ausdata-wedge-press" d="M19 25 L26 19 L26 29 Z" />
        </g>
        <g className="ausdata-wedge ausdata-stroke-3">
          <line className="ausdata-wedge-line" x1="45" y1="24" x2="58" y2="19" pathLength={1} />
          <path className="ausdata-wedge-press" d="M38 25 L45 19 L45 29 Z" />
        </g>
        <g className="ausdata-wedge ausdata-stroke-4">
          <line className="ausdata-wedge-line" x1="33" y1="40" x2="46" y2="35" pathLength={1} />
          <path className="ausdata-wedge-press" d="M26 41 L33 35 L33 45 Z" />
        </g>
        <g className="ausdata-wedge ausdata-stroke-5">
          <line className="ausdata-wedge-line" x1="52" y1="40" x2="65" y2="35" pathLength={1} />
          <path className="ausdata-wedge-press" d="M45 41 L52 35 L52 45 Z" />
        </g>
      </g>
    </svg>
  );
}

function NisabaLogo() {
  return (
    <svg className="nisaba-logo" viewBox="0 0 80 60" role="img" aria-label="Nisaba clay tablet mark">
      <rect className="nisaba-logo-tablet" x="8" y="7" width="64" height="46" rx="10" />
      <g className="nisaba-logo-wedge">
        <line x1="26" y1="24" x2="39" y2="19" />
        <path d="M19 25 L26 19 L26 29 Z" />
      </g>
      <g className="nisaba-logo-wedge">
        <line x1="45" y1="24" x2="58" y2="19" />
        <path d="M38 25 L45 19 L45 29 Z" />
      </g>
      <g className="nisaba-logo-wedge">
        <line x1="33" y1="40" x2="46" y2="35" />
        <path d="M26 41 L33 35 L33 45 Z" />
      </g>
      <g className="nisaba-logo-wedge">
        <line x1="52" y1="40" x2="65" y2="35" />
        <path d="M45 41 L52 35 L52 45 Z" />
      </g>
    </svg>
  );
}

function renderContentBlocks(value: string) {
  const blocks = parseContentBlocks(value);
  return blocks.map((block, index) => {
    if (block.type === "heading") {
      const HeadingTag = `h${block.level}` as const;
      return <HeadingTag key={`heading-${index}`}>{renderInlineMarkdown(block.text)}</HeadingTag>;
    }

    if (block.type === "paragraph") {
      return (
        <p key={`p-${index}`}>
          {block.lines.map((line, lineIndex) => (
            <Fragment key={`line-${lineIndex}`}>
              {lineIndex > 0 ? <br /> : null}
              {renderInlineMarkdown(line)}
            </Fragment>
          ))}
        </p>
      );
    }

    if (block.type === "list") {
      return (
        <ul key={`list-${index}`}>
          {block.items.map((item, itemIndex) => (
            <li key={`item-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>
      );
    }

    if (block.type === "ordered-list") {
      return (
        <ol key={`olist-${index}`}>
          {block.items.map((item, itemIndex) => (
            <li key={`oitem-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>
      );
    }

    if (block.type === "table") {
      return (
        <div key={`table-${index}`} className="table-scroll">
          <table className="message-table">
            <thead>
              <tr>
                {block.headers.map((header, headerIndex) => (
                  <th key={`header-${headerIndex}`}>{renderInlineMarkdown(header)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {block.headers.map((_, cellIndex) => (
                    <td key={`cell-${rowIndex}-${cellIndex}`}>
                      {renderInlineMarkdown(row[cellIndex] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    if (block.type === "code") {
      return (
        <pre key={`code-${index}`} className="message-code-block">
          <code>{block.code}</code>
        </pre>
      );
    }

    return <ChartBlock key={`chart-${index}`} spec={block.spec} />;
  });
}

function simplifyStatusMessage(value: string) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  const toolResultMatch = normalized.match(/^(.*?)(?:\s+result_json(?:_preview)?:|\s+created artifacts:)/i);
  if (toolResultMatch) {
    const summary = toolResultMatch[1]
      .replace(/artifact-\d+/gi, "the data")
      .replace(/\s+/g, " ")
      .trim();
    if (/^Creating a .*chart /i.test(summary) || /^Building a .*chart /i.test(summary)) {
      return "Preparing the chart.";
    }
    if (/^Retrieving .*chart content/i.test(summary) || /^Reading .*chart file/i.test(summary)) {
      return "Preparing the chart for display.";
    }
    if (/^Composing final answer/i.test(summary)) {
      return "Writing the final answer.";
    }
    return summary || "Working through the ABS results.";
  }

  if (/^Loop \d+: reasoning about the next step\.?$/i.test(normalized)) {
    return "";
  }
  if (/^Plan approved\./i.test(normalized)) {
    return "Continuing with the approved approach.";
  }
  if (/^Tool execution failed\./i.test(normalized)) {
    return "Adjusting the approach after a failed step.";
  }
  if (normalized.startsWith("{") || normalized.startsWith("[")) {
    return "Fetched structured output. Summarising it.";
  }
  return normalized;
}

function mapBackendMessages(rawMessages: unknown): ChatMessage[] {
  if (!Array.isArray(rawMessages)) {
    return [];
  }
  return rawMessages.flatMap((message) => {
    if (!message || typeof message !== "object") {
      return [];
    }
    const typed = message as { role?: unknown; content?: unknown; run_cost?: unknown };
    const role = typeof typed.role === "string" ? typed.role.trim().toLowerCase() : "";
    const content = typeof typed.content === "string" ? typed.content : "";
    if (!content.trim()) {
      return [];
    }
    if (role !== "user" && role !== "assistant" && role !== "progress") {
      return [];
    }
    const rawRunCost = typed.run_cost;
    const runCost =
      rawRunCost && typeof rawRunCost === "object"
        ? {
            model: typeof (rawRunCost as RunCost).model === "string" ? (rawRunCost as RunCost).model : undefined,
            input_tokens: Number((rawRunCost as RunCost).input_tokens),
            output_tokens: Number((rawRunCost as RunCost).output_tokens),
            ai_cost_usd: Number((rawRunCost as RunCost).ai_cost_usd),
            surcharge_usd: Number((rawRunCost as RunCost).surcharge_usd),
            final_cost_usd: Number((rawRunCost as RunCost).final_cost_usd),
          }
        : undefined;
    return [
      {
        id: createConversationId(),
        sender: role as Sender,
        content,
        runCost,
      } satisfies ChatMessage,
    ];
  });
}

function mapStoredChatRuns(rawRuns: unknown): ChatMessage[] {
  if (!Array.isArray(rawRuns)) {
    return [];
  }
  return rawRuns.flatMap((run) => {
    if (!run || typeof run !== "object") {
      return [];
    }
    const row = run as Record<string, unknown>;
    const rowId = toText(row.id) || createConversationId();
    const userMessage = toText(row.user_message);
    const finalResponse = toText(row.final_response);
    if (!userMessage || !finalResponse) {
      return [];
    }
    const rawProgressNotes = Array.isArray(row.progress_notes) ? row.progress_notes : [];
    const progressNotes = rawProgressNotes.map((note) => toText(note)).filter(Boolean);
    const rawRunCost = row.run_cost;
    const runCost =
      rawRunCost && typeof rawRunCost === "object"
        ? {
            model: toText((rawRunCost as RunCost).model) || undefined,
            input_tokens: Number((rawRunCost as RunCost).input_tokens),
            output_tokens: Number((rawRunCost as RunCost).output_tokens),
            ai_cost_usd: Number((rawRunCost as RunCost).ai_cost_usd),
            surcharge_usd: Number((rawRunCost as RunCost).surcharge_usd),
            final_cost_usd: Number((rawRunCost as RunCost).final_cost_usd),
          }
        : undefined;

    return [
      {
        id: `${rowId}-user`,
        sender: "user",
        content: userMessage,
      } satisfies ChatMessage,
      ...progressNotes.map(
        (content, index) =>
          ({
            id: `${rowId}-progress-${index}`,
            sender: "progress",
            content,
          }) satisfies ChatMessage
      ),
      {
        id: `${rowId}-assistant`,
        sender: "assistant",
        content: finalResponse,
        runCost,
      } satisfies ChatMessage,
    ];
  });
}

function formatUsd(value: number | undefined) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "A$0.00";
  }
  return `A$${numeric.toFixed(2)}`;
}

function renderRunCost(runCost?: RunCost) {
  if (!runCost) {
    return null;
  }
  const finalCost = Number(runCost.final_cost_usd);
  const aiCost = Number(runCost.ai_cost_usd);
  const surcharge = Number(runCost.surcharge_usd);
  const displayCost = Number.isFinite(finalCost) ? finalCost : aiCost;
  if (!Number.isFinite(displayCost)) {
    return null;
  }
  const hoverParts = [
    runCost.model ? `Model: ${runCost.model}` : "",
    Number.isFinite(Number(runCost.input_tokens))
      ? `Input: ${Math.round(Number(runCost.input_tokens)).toLocaleString()}`
      : "",
    Number.isFinite(Number(runCost.output_tokens))
      ? `Output: ${Math.round(Number(runCost.output_tokens)).toLocaleString()}`
      : "",
    Number.isFinite(aiCost) ? `Raw: ${formatUsd(aiCost * USD_TO_AUD_RATE)}` : "",
    Number.isFinite(surcharge) ? `10%: ${formatUsd(surcharge * USD_TO_AUD_RATE)}` : "",
    `Total: ${formatUsd(displayCost * USD_TO_AUD_RATE)}`,
  ].filter(Boolean);
  return (
    <span className="assistant-run-cost-wrap">
      <span className="assistant-run-cost-trigger" tabIndex={0}>
        <span className="assistant-run-cost">
          {formatUsd(displayCost * USD_TO_AUD_RATE)}
        </span>
        <div className="assistant-run-cost-tooltip" role="tooltip">
          {hoverParts.map((part) => (
            <p key={part}>{part}</p>
          ))}
        </div>
      </span>
    </span>
  );
}

function applyCompletedTaskSnapshot(
  payload: ConversationSnapshotResponse,
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>,
  assistantMessageId: string
) {
  const mappedMessages = mapBackendMessages(payload.messages);
  if (mappedMessages.length > 0) {
    setMessages(mappedMessages);
    return true;
  }
  const finalResponse = String(payload.final_response ?? "").trim();
  if (finalResponse) {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === assistantMessageId ? { ...message, content: finalResponse } : message
      )
    );
    return true;
  }
  return false;
}

function appendProgressMessage(
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>,
  assistantMessageId: string,
  content: string
) {
  setMessages((prev) => {
    const next = [...prev];
    const assistantIndex = next.findIndex((msg) => msg.id === assistantMessageId);
    const insertionIndex = assistantIndex === -1 ? next.length : assistantIndex;
    next.splice(insertionIndex, 0, {
      id: createConversationId(),
      sender: "progress",
      content,
    });
    return next;
  });
}

function isProgressSubtask(content: string) {
  return content === "Code generated." || content === "Code run.";
}

function readStoredActiveProjectId() {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY) || "";
}

function createLocalProject(name = "New model", question = ""): ModellingProject {
  const now = new Date().toISOString();
  return {
    id: createConversationId(),
    name,
    question,
    status: "draft",
    conversationId: createConversationId(),
    modelBuilderState: createEmptyModelBuilderState(),
    activeValidatedVariableIds: [],
    updatedAt: now,
  };
}

function toText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function toTextArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => toText(item)).filter(Boolean) : [];
}

function toRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function createEmptyModelBuilderState(): ModelBuilderState {
  return { variables: [], assumptions: [], nodes: [], edges: [] };
}

function parseModelBuilderState(value: unknown): ModelBuilderState {
  if (!value || typeof value !== "object") {
    return createEmptyModelBuilderState();
  }
  return normalizeModelSpec(value as ModelBuilderSpec) || createEmptyModelBuilderState();
}

function parseProjectModelBuilderState(row: Record<string, unknown>): ModelBuilderState {
  const legacyState = parseModelBuilderState(row.model_builder_state);
  const assumptionsState = parseModelBuilderState({ assumptions: row.model_assumptions });
  const graphState = parseModelBuilderState(row.model_graph_state);
  const hasAssumptionsColumn = Array.isArray(row.model_assumptions);
  const hasGraphColumn = Boolean(row.model_graph_state && typeof row.model_graph_state === "object");
  return {
    variables: legacyState.variables,
    assumptions: hasAssumptionsColumn ? assumptionsState.assumptions : legacyState.assumptions,
    nodes: hasGraphColumn ? graphState.nodes : legacyState.nodes,
    edges: hasGraphColumn ? graphState.edges : legacyState.edges,
  };
}

function toModelGraphState(state: ModelBuilderState) {
  return {
    nodes: state.nodes,
    edges: state.edges,
  };
}

function mapProjectRow(row: Record<string, unknown>): ModellingProject {
  return {
    id: toText(row.id) || createConversationId(),
    name: toText(row.name) || "Untitled model",
    question: toText(row.question),
    status: toText(row.status) === "archived" ? "archived" : toText(row.status) === "active" ? "active" : "draft",
    conversationId: toText(row.conversation_id) || createConversationId(),
    modelBuilderState: parseProjectModelBuilderState(row),
    activeValidatedVariableIds: toTextArray(row.active_validated_variable_ids),
    updatedAt: toText(row.updated_at) || new Date().toISOString(),
  };
}

function projectInsertPayload(userId: string, draft: ModellingProject) {
  return {
    user_id: userId,
    name: draft.name,
    question: draft.question,
    status: draft.status,
    conversation_id: draft.conversationId,
    model_builder_state: draft.modelBuilderState,
    active_validated_variable_ids: draft.activeValidatedVariableIds,
    model_assumptions: draft.modelBuilderState.assumptions,
    model_graph_state: toModelGraphState(draft.modelBuilderState),
  };
}

async function insertProjectRow(userId: string, draft: ModellingProject) {
  return supabase
    .from("modelling_projects")
    .insert(projectInsertPayload(userId, draft))
    .select(PROJECT_SELECT_COLUMNS)
    .single();
}

async function updateProjectModelBuilderState(projectId: string, nextState: ModelBuilderState) {
  return supabase
    .from("modelling_projects")
    .update({
      model_builder_state: nextState,
      model_assumptions: nextState.assumptions,
      model_graph_state: toModelGraphState(nextState),
      updated_at: new Date().toISOString(),
    })
    .eq("id", projectId);
}

function mapVariableRow(row: Record<string, unknown>): ValidatedVariable {
  const status = toText(row.validation_status);
  const evidenceArtifact = toRecord(row.evidence_artifact);
  const contents = toRecord(evidenceArtifact?.contents);
  const contentsSummary = toText(row.contentsSummary) || toText(row.contents_summary) || toText(evidenceArtifact?.contents_summary);
  return {
    id: toText(row.id) || createConversationId(),
    name: toText(row.name) || toText(row.label) || "variable",
    label: toText(row.label) || toText(row.name) || "Variable",
    sourceName: toText(row.source_name),
    metric: toText(row.metric),
    unit: toText(row.unit),
    geography: toText(row.geography),
    frequency: toText(row.frequency),
    seasonalTreatment: toText(row.seasonal_treatment),
    transformSummary: toText(row.transform_summary),
    contentsSummary: contentsSummary || undefined,
    contents: contents || undefined,
    validationStatus: status === "validated" ? "validated" : status === "rejected" ? "rejected" : "candidate",
  };
}

function normalizeModelSpec(spec: ModelBuilderSpec | null): ModelBuilderState | null {
  if (!spec) {
    return null;
  }

  const variables = (spec.variables || []).map((variable, index): ValidatedVariable => {
    const id = toText(variable.id) || toText(variable.name) || `variable-${index + 1}`;
    const status = toText(variable.validationStatus);
    return {
      id,
      name: toText(variable.name) || id,
      label: toText(variable.label) || toText(variable.name) || `Variable ${index + 1}`,
      sourceName: toText(variable.sourceName),
      metric: toText(variable.metric),
      unit: toText(variable.unit),
      geography: toText(variable.geography),
      frequency: toText(variable.frequency),
      seasonalTreatment: toText(variable.seasonalTreatment),
      transformSummary: toText(variable.transformSummary),
      contentsSummary: toText(variable.contentsSummary) || undefined,
      contents: toRecord(variable.contents) || undefined,
      validationStatus: status === "candidate" || status === "rejected" ? status : "validated",
    };
  });

  const assumptions = (spec.assumptions || [])
    .map((assumption, index): ModelAssumption => {
      const raw = assumption as Record<string, unknown>;
      return {
        id: toText(assumption.id) || `assumption-${index + 1}`,
        variableId: toText(assumption.variableId) || toText(assumption.variable) || undefined,
        nodeId: toText(raw.nodeId) || toText(raw.node_id) || toText(raw.calculationNodeId) || undefined,
        label: toText(assumption.label) || "Assumption",
        valueText: toText(assumption.valueText),
        method: toText(raw.method) || undefined,
        inputs: toTextArray(raw.inputs),
        output: toText(raw.output) || undefined,
        logicSummary: toText(raw.logicSummary) || toText(raw.logic_summary) || undefined,
        parameters: toRecord(raw.parameters) || undefined,
        calculationLogic: toRecord(raw.calculationLogic) || toRecord(raw.calculation_logic) || undefined,
        calculationSpec: toRecord(raw.calculationSpec) || toRecord(raw.calculation_spec) || undefined,
      };
    });

  const nodes = (spec.nodes || []).map((node, index): ModelNode => {
    const id = toText(node.id) || `node-${index + 1}`;
    const nodeType = toText(node.nodeType);
    return {
      id,
      label: toText(node.label) || id,
      nodeType:
        nodeType === "assumption" || nodeType === "calculation" || nodeType === "result"
          ? nodeType
          : "variable",
      variableId: toText(node.variableId) || undefined,
      assumptionId: toText((node as Record<string, unknown>).assumptionId) || toText((node as Record<string, unknown>).assumption_id) || undefined,
      expression: toText(node.expression) || undefined,
      method: toText((node as Record<string, unknown>).method) || undefined,
      inputs: toTextArray((node as Record<string, unknown>).inputs),
      output: toText((node as Record<string, unknown>).output) || undefined,
      sourceCalculationId: toText((node as Record<string, unknown>).sourceCalculationId) || toText((node as Record<string, unknown>).source_calculation_id) || undefined,
      logicSummary: toText((node as Record<string, unknown>).logicSummary) || toText((node as Record<string, unknown>).logic_summary) || undefined,
      tooltip: toText((node as Record<string, unknown>).tooltip) || undefined,
      parameters: toRecord((node as Record<string, unknown>).parameters) || undefined,
      calculationLogic: toRecord((node as Record<string, unknown>).calculationLogic) || toRecord((node as Record<string, unknown>).calculation_logic) || undefined,
      calculationSpec: toRecord((node as Record<string, unknown>).calculationSpec) || toRecord((node as Record<string, unknown>).calculation_spec) || undefined,
      positionX: Number.isFinite(Number(node.positionX)) ? Number(node.positionX) : undefined,
      positionY: Number.isFinite(Number(node.positionY)) ? Number(node.positionY) : undefined,
    };
  });

  const edges = (spec.edges || []).flatMap((edge, index): ModelEdge[] => {
    const sourceNodeId = toText(edge.sourceNodeId) || toText(edge.from);
    const targetNodeId = toText(edge.targetNodeId) || toText(edge.to);
    if (!sourceNodeId || !targetNodeId) {
      return [];
    }
    return [
      {
        id: toText(edge.id) || `edge-${index + 1}`,
        sourceNodeId,
        targetNodeId,
        operator: toText(edge.operator) || undefined,
        label: toText(edge.label) || undefined,
      },
    ];
  });

  const graph = withOperationNodes(nodes, edges);
  return { variables, assumptions, nodes: graph.nodes, edges: graph.edges };
}

function buildFallbackGraph(variables: ValidatedVariable[]): { nodes: ModelNode[]; edges: ModelEdge[] } {
  if (variables.length === 0) {
    return {
      nodes: [
        { id: "variable-1", label: "Variable 1", nodeType: "variable", positionX: 24, positionY: 72 },
        { id: "variable-2", label: "Variable 2", nodeType: "variable", positionX: 24, positionY: 152 },
        { id: "operation-multiply-model-result", label: "×", nodeType: "calculation", expression: "×", positionX: 220, positionY: 112 },
        { id: "model-result", label: "Model output", nodeType: "result", positionX: 360, positionY: 112 },
      ],
      edges: [
        { id: "variable-1-multiply", sourceNodeId: "variable-1", targetNodeId: "operation-multiply-model-result" },
        { id: "variable-2-multiply", sourceNodeId: "variable-2", targetNodeId: "operation-multiply-model-result" },
        { id: "multiply-result", sourceNodeId: "operation-multiply-model-result", targetNodeId: "model-result" },
      ],
    };
  }

  const inputNodes = variables.slice(0, 5).map((variable, index): ModelNode => ({
    id: variable.id,
    label: variable.name,
    nodeType: "variable",
    variableId: variable.id,
    positionX: 24,
    positionY: 28 + index * 72,
  }));
  const resultNode = { id: "model-result", label: "Result", nodeType: "result" as const, positionX: 360, positionY: 72 };
  const operationNode = {
    id: "operation-add-model-result",
    label: variables.length > 1 ? "+" : "=",
    nodeType: "calculation" as const,
    expression: variables.length > 1 ? "+" : "=",
    positionX: 220,
    positionY: 72,
  };
  return {
    nodes: [...inputNodes, operationNode, resultNode],
    edges: [
      ...inputNodes.map((node) => ({
        id: `${node.id}-${operationNode.id}`,
        sourceNodeId: node.id,
        targetNodeId: operationNode.id,
      })),
      {
        id: `${operationNode.id}-result`,
        sourceNodeId: operationNode.id,
        targetNodeId: resultNode.id,
      },
    ],
  };
}

function ProductTitle() {
  return (
    <div className="product-title nisaba-title-trigger" tabIndex={0}>
      <div className="brand-mark">
        <NisabaLogo />
      </div>
      <div className="product-title-text">
        <div className="product-title-row product-title-row-main">
          <h1>Nisaba</h1>
        </div>
        <div className="product-title-row product-title-row-subtitle">
          <div className="product-subtitle-group">
          <div className="info-action">
            <span className="subtitle-info-trigger">
              <span>economic modelling studio</span>
              <span className="subtitle-info-icon" aria-hidden="true" tabIndex={0}>
                <svg viewBox="0 0 12 12" focusable="false">
                  <circle cx="6" cy="6" r="5.25" fill="none" stroke="currentColor" strokeWidth="1" />
                  <circle cx="6" cy="3.45" r="0.7" fill="currentColor" />
                  <rect x="5.4" y="4.95" width="1.2" height="3.75" rx="0.6" fill="currentColor" />
                </svg>
                <div className="header-tooltip info-tooltip" role="tooltip">
                  <p>
                    Nisaba is named for the Sumerian keeper of writing, records,
                    grain accounts, and measured knowledge.
                  </p>
                  <p>
                    In this workspace, that myth becomes a practical rule:
                    every useful metric should be recorded, validated, and
                    reproducible before it becomes part of a model.
                  </p>
                  <p>
                    Nisaba uses the AusData MCP for Australian official data and
                    global macro context, then saves projects, chat history,
                    validated variables, assumptions, model structure, runs, and
                    exports as linked modelling records.
                  </p>
                  <p>
                    Produced by{" "}
                    <a href="https://dottieaistudio.com.au/" target="_blank" rel="noreferrer">
                      Dottie AI Studio
                    </a>
                    {" · "}
                    open source{" "}
                    <a
                      href="https://github.com/J-King-Dottie/ausdata-ai-harness"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Nisaba/AusData MCP
                    </a>
                  </p>
                  <div className="info-tooltip-summary">
                    <div className="table-scroll">
                      <table className="info-tooltip-table">
                        <thead>
                          <tr>
                            <th>Route</th>
                            <th>Provider</th>
                            <th>Datasets</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>Domestic</td>
                            <td>ABS</td>
                            <td>1,221</td>
                          </tr>
                          <tr>
                            <td>Domestic</td>
                            <td>DCCEEW</td>
                            <td>1</td>
                          </tr>
                          <tr>
                            <td>Domestic</td>
                            <td>RBA</td>
                            <td>71</td>
                          </tr>
                          <tr>
                            <td>Macro</td>
                            <td>OECD</td>
                            <td>1,464</td>
                          </tr>
                          <tr>
                            <td>Macro</td>
                            <td>World Bank</td>
                            <td>28,377</td>
                          </tr>
                          <tr>
                            <td>Macro</td>
                            <td>IMF</td>
                            <td>132</td>
                          </tr>
                          <tr>
                            <td>Macro</td>
                            <td>UN Comtrade</td>
                            <td>1</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </span>
            </span>
          </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProjectsPane({
  projects,
  activeProjectId,
  loading,
  displayName,
  authBusy,
  collapsed,
  style,
  onToggleCollapsed,
  onCreateProject,
  onSelectProject,
  onRenameProject,
  onDeleteProject,
  onSignOut,
}: {
  projects: ModellingProject[];
  activeProjectId: string;
  loading: boolean;
  displayName: string;
  authBusy: boolean;
  collapsed: boolean;
  style?: CSSProperties;
  onToggleCollapsed: () => void;
  onCreateProject: () => void;
  onSelectProject: (project: ModellingProject) => void;
  onRenameProject: (projectId: string, name: string) => void;
  onDeleteProject: (project: ModellingProject) => void;
  onSignOut: () => void;
}) {
  const activeProject = projects.find((project) => project.id === activeProjectId);
  const [draftProjectName, setDraftProjectName] = useState(activeProject?.name ?? "");

  useEffect(() => {
    setDraftProjectName(activeProject?.name ?? "");
  }, [activeProject?.name, activeProjectId]);

  const commitProjectName = (project: ModellingProject) => {
    const nextName = draftProjectName.trim() || "Untitled model";
    setDraftProjectName(nextName);
    if (nextName !== project.name) {
      onRenameProject(project.id, nextName);
    }
  };

  const workspaceMenu = (
    <div className="workspace-menu">
      <button type="button" className="workspace-menu-button" aria-label="Workspace menu">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="5" cy="12" r="1.7" />
          <circle cx="12" cy="12" r="1.7" />
          <circle cx="19" cy="12" r="1.7" />
        </svg>
      </button>
      <div className="workspace-menu-popover" role="menu">
        <div className="workspace-menu-user" title={displayName}>{displayName}</div>
        <button type="button" onClick={onSignOut} disabled={authBusy} role="menuitem">
          Sign out
        </button>
      </div>
    </div>
  );

  const collapseButton = (
    <button
      type="button"
      className="projects-collapse-button"
      onClick={onToggleCollapsed}
      aria-label={collapsed ? "Expand projects" : "Collapse projects"}
      title={collapsed ? "Expand projects" : "Collapse projects"}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        {collapsed ? (
          <path d="M9 5.8 15.2 12 9 18.2" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" />
        ) : (
          <path d="M15 5.8 8.8 12 15 18.2" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" />
        )}
      </svg>
    </button>
  );

  return (
    <aside className={`workspace-pane projects-pane${collapsed ? " collapsed" : ""}`} style={style} aria-label="Projects">
      <div className="projects-pane-brand-row">
        {collapsed ? collapseButton : (
          <>
            <ProductTitle />
            {collapseButton}
          </>
        )}
      </div>
      <div className="pane-heading projects-heading">
        <button
          type="button"
          className="pane-icon-button"
          onClick={onCreateProject}
          aria-label="Create project"
          title={collapsed ? undefined : "Create project"}
          data-tooltip={collapsed ? "New project" : undefined}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.9" />
          </svg>
        </button>
      </div>
      <div className="project-list">
        {loading ? (
          <div className="project-item project-item-loading" aria-live="polite">
            <span>Loading projects...</span>
          </div>
        ) : projects.map((project) => {
          const isActive = project.id === activeProjectId;
          return (
            <div
              key={project.id}
              className={`project-item${isActive ? " active" : ""}`}
              role="button"
              tabIndex={0}
              data-tooltip={project.name}
              title={project.name}
              onClick={() => onSelectProject(project)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectProject(project);
                }
              }}
            >
              <svg className="project-folder-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M3.8 7.2c0-1 .8-1.8 1.8-1.8h4l1.9 2h6.9c1 0 1.8.8 1.8 1.8v7.6c0 1-.8 1.8-1.8 1.8H5.6c-1 0-1.8-.8-1.8-1.8V7.2z"
                  fill="none"
                  stroke="currentColor"
                  strokeLinejoin="round"
                  strokeWidth="1.7"
                />
              </svg>
              {!collapsed && isActive ? (
                <input
                  className="project-name-input"
                  value={draftProjectName}
                  aria-label="Model title"
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => setDraftProjectName(event.target.value)}
                  onBlur={() => commitProjectName(project)}
                  onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                    event.stopPropagation();
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                    if (event.key === "Escape") {
                      setDraftProjectName(project.name);
                      event.currentTarget.blur();
                    }
                  }}
                />
              ) : !collapsed ? (
                <span className="project-name">{project.name}</span>
              ) : null}
              {!collapsed && (
                <button
                  type="button"
                  className="project-delete-button"
                  aria-label={`Delete ${project.name}`}
                  title="Delete project"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteProject(project);
                  }}
                  onKeyDown={(event) => event.stopPropagation()}
                >
                  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="projects-pane-footer">
        {workspaceMenu}
      </div>
    </aside>
  );
}

function modelNodeSize(node: ModelNode) {
  if (node.nodeType === "calculation") {
    if (isCustomCalculationNode(node)) {
      return { width: 96, height: 34 };
    }
    return { width: 40, height: 40 };
  }
  return { width: 122, height: 42 };
}

function isCustomCalculationNode(node: ModelNode) {
  return node.nodeType === "calculation" && Boolean(node.assumptionId || node.method || node.expression === "custom");
}

function mathSymbol(value: string) {
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return "";
  }
  if (["divide", "division", "denominator", "/", "÷"].includes(normalized)) {
    return "÷";
  }
  if (["multiply", "multiplication", "product", "*", "x", "×"].includes(normalized)) {
    return "×";
  }
  if (["add", "addition", "sum", "plus", "+"].includes(normalized)) {
    return "+";
  }
  if (["subtract", "minus", "difference", "-", "−"].includes(normalized)) {
    return "−";
  }
  if (["equals", "result", "="].includes(normalized)) {
    return "=";
  }
  if (["numerator", "input"].includes(normalized)) {
    return "";
  }
  return value;
}

function mathEdgeLabel(edge: ModelEdge) {
  const value = edge.label || edge.operator || "";
  return mathSymbol(value);
}

function nonEqualsMathEdgeLabel(edge: ModelEdge) {
  const label = mathEdgeLabel(edge);
  return label === "=" ? "" : label;
}

function mathNodeLabel(node: ModelNode) {
  const value = `${node.label || ""} ${node.expression || ""}`.trim().toLowerCase();
  if (!value) {
    return "";
  }
  if (value.includes("divide") || value.includes("division") || value.includes("ratio") || value.includes("÷") || value.includes("/")) {
    return "÷";
  }
  if (value.includes("multiply") || value.includes("multiplication") || value.includes("product") || value.includes("×") || value.includes("*")) {
    return "×";
  }
  if (value.includes("add") || value.includes("sum") || value.includes("plus") || value.includes("+")) {
    return "+";
  }
  if (value.includes("subtract") || value.includes("minus") || value.includes("difference") || value.includes("−") || value.includes("-")) {
    return "−";
  }
  if (value.includes("equals") || value.includes("=")) {
    return "=";
  }
  return "";
}

function compactJson(value: unknown) {
  if (!value || typeof value !== "object") {
    return "";
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined && item !== null && item !== "")
    .slice(0, 5)
    .map(([key, item]) => `${key}: ${Array.isArray(item) ? item.join(", ") : String(item)}`);
  return entries.join("; ");
}

function calculationReplaySummary(value: unknown) {
  const spec = toRecord(value);
  const replay = toRecord(spec?.replay);
  return toText(replay?.formula) || toText(replay?.code) || "";
}

function labelForNodeId(nodeId: string, nodeMap: Map<string, ModelNode>, variables: ValidatedVariable[]) {
  const node = nodeMap.get(nodeId);
  if (!node) {
    return nodeId;
  }
  const variable = variables.find((item) => item.id === (node.variableId || node.id));
  return variable?.label || variable?.name || node.label || node.id;
}

function modelNodeTooltip(
  node: ModelNode,
  nodeMap: Map<string, ModelNode>,
  variables: ValidatedVariable[],
  assumptions: ModelAssumption[]
) {
  const variable = variables.find((item) => item.id === (node.variableId || node.id));
  const assumption = assumptions.find((item) => item.id === node.assumptionId || item.nodeId === node.id);
  if (variable) {
    return [
      variable.label || variable.name,
      variable.contentsSummary || [variable.sourceName, variable.metric, variable.geography, variable.frequency, variable.unit].filter(Boolean).join(" · "),
      variable.transformSummary,
    ].filter(Boolean).join("\n");
  }
  if (assumption || node.assumptionId) {
    const inputLabels = (node.inputs || assumption?.inputs || []).map((id) => labelForNodeId(id, nodeMap, variables)).join(", ");
    const replaySummary = calculationReplaySummary(node.calculationSpec || assumption?.calculationSpec);
    return [
      assumption?.valueText || node.logicSummary || node.tooltip || node.label,
      node.method || assumption?.method ? `Method: ${node.method || assumption?.method}` : "",
      inputLabels ? `Inputs: ${inputLabels}` : "",
      node.output || assumption?.output ? `Output: ${node.output || assumption?.output}` : "",
      replaySummary ? `Replay: ${replaySummary}` : "",
      compactJson(node.parameters || assumption?.parameters),
    ].filter(Boolean).join("\n");
  }
  if (node.nodeType === "calculation") {
    const inputLabels = (node.inputs || []).map((id) => labelForNodeId(id, nodeMap, variables)).join(", ");
    return [
      node.label,
      node.expression ? `Operation: ${node.expression}` : "",
      inputLabels ? `Inputs: ${inputLabels}` : "",
    ].filter(Boolean).join("\n");
  }
  if (node.nodeType === "result") {
    return [node.label, node.logicSummary || node.tooltip || "Model output"].filter(Boolean).join("\n");
  }
  return node.tooltip || node.label;
}

function operationIdFor(targetNodeId: string, symbol: string) {
  const operationName =
    symbol === "÷" ? "divide" : symbol === "×" ? "multiply" : symbol === "+" ? "add" : symbol === "−" ? "subtract" : "operation";
  return `operation-${operationName}-${targetNodeId}`.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

function clearEdgeMath(edge: ModelEdge): ModelEdge {
  return { ...edge, operator: undefined, label: undefined };
}

function withOperationNodes(nodes: ModelNode[], edges: ModelEdge[]): { nodes: ModelNode[]; edges: ModelEdge[] } {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const nextNodes = [...nodes];
  const nextEdges: ModelEdge[] = [];
  const operationGroups = new Map<
    string,
    {
      id: string;
      symbol: string;
      targetNodeId: string;
      sourceNodeIds: string[];
      sourceEdges: ModelEdge[];
    }
  >();

  edges.forEach((edge) => {
    const symbol = nonEqualsMathEdgeLabel(edge);
    const source = nodeMap.get(edge.sourceNodeId);
    const target = nodeMap.get(edge.targetNodeId);
    if (!symbol || !source || !target || source.nodeType === "calculation" || target.nodeType === "calculation") {
      nextEdges.push(clearEdgeMath(edge));
      return;
    }

    const operationNodeId = operationIdFor(edge.targetNodeId, symbol);
    const groupKey = `${operationNodeId}:${edge.targetNodeId}`;
    const group =
      operationGroups.get(groupKey) ||
      {
        id: operationNodeId,
        symbol,
        targetNodeId: edge.targetNodeId,
        sourceNodeIds: [],
        sourceEdges: [],
      };
    group.sourceNodeIds.push(edge.sourceNodeId);
    group.sourceEdges.push(edge);
    operationGroups.set(groupKey, group);
  });

  operationGroups.forEach((group) => {
    const target = nodeMap.get(group.targetNodeId);
    if (!target) {
      return;
    }
    const sourceNodes = group.sourceNodeIds.flatMap((sourceNodeId) => {
      const source = nodeMap.get(sourceNodeId);
      return source ? [source] : [];
    });
    const averageSourceX =
      sourceNodes.reduce((total, node) => total + Number(node.positionX ?? 0), 0) / Math.max(1, sourceNodes.length);
    const averageSourceY =
      sourceNodes.reduce((total, node) => total + Number(node.positionY ?? 0), 0) / Math.max(1, sourceNodes.length);
    const operationNode: ModelNode = nodeMap.get(group.id) || {
      id: group.id,
      label: group.symbol,
      nodeType: "calculation",
      expression: group.symbol,
      positionX: Math.round(((averageSourceX || 24) + Number(target.positionX ?? 300)) / 2),
      positionY: Math.round(averageSourceY || Number(target.positionY ?? 90)),
    };
    if (!nodeMap.has(group.id)) {
      nodeMap.set(group.id, operationNode);
      nextNodes.push(operationNode);
    }
    group.sourceEdges.forEach((edge, index) => {
      nextEdges.push({
        id: `${edge.id || `${edge.sourceNodeId}-${group.id}`}-input-${index}`,
        sourceNodeId: edge.sourceNodeId,
        targetNodeId: group.id,
      });
    });
    nextEdges.push({
      id: `${group.id}-${group.targetNodeId}`,
      sourceNodeId: group.id,
      targetNodeId: group.targetNodeId,
    });
  });

  return { nodes: nextNodes, edges: repairLegacyOperationBypassEdges(nextNodes, nextEdges) };
}

function repairLegacyOperationBypassEdges(nodes: ModelNode[], edges: ModelEdge[]): ModelEdge[] {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const operationToResult = edges.filter((edge) => {
    const source = nodeMap.get(edge.sourceNodeId);
    const target = nodeMap.get(edge.targetNodeId);
    return source?.nodeType === "calculation" && target?.nodeType === "result";
  });
  if (!operationToResult.length) {
    return edges;
  }

  const rewiredEdges: ModelEdge[] = [];
  edges.forEach((edge) => {
    const source = nodeMap.get(edge.sourceNodeId);
    const target = nodeMap.get(edge.targetNodeId);
    if (!source || target?.nodeType !== "result") {
      rewiredEdges.push(edge);
      return;
    }
    if (source.nodeType === "calculation") {
      rewiredEdges.push(edge);
      return;
    }
    const matchingOperationEdge = operationToResult.find((candidate) => candidate.targetNodeId === edge.targetNodeId);
    if (!matchingOperationEdge) {
      rewiredEdges.push(edge);
      return;
    }
    const alreadyFeedsOperation = edges.some(
      (candidate) =>
        candidate.sourceNodeId === edge.sourceNodeId &&
        candidate.targetNodeId === matchingOperationEdge.sourceNodeId
    );
    if (alreadyFeedsOperation) {
      return;
    }
    rewiredEdges.push({
      id: `${edge.id || `${edge.sourceNodeId}-${matchingOperationEdge.sourceNodeId}`}-rewired`,
      sourceNodeId: edge.sourceNodeId,
      targetNodeId: matchingOperationEdge.sourceNodeId,
    });
  });
  return rewiredEdges;
}

function truncateNodeLabel(value: string, maxLength = 34) {
  const text = value.trim();
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1).trim()}…`;
}

function positionedNodes(nodes: ModelNode[]): ModelNode[] {
  return nodes.map((node, index) => ({
    ...node,
    positionX: Number.isFinite(Number(node.positionX)) ? Number(node.positionX) : 28 + (index % 2) * 172,
    positionY: Number.isFinite(Number(node.positionY)) ? Number(node.positionY) : 28 + Math.floor(index / 2) * 82,
  }));
}

function visibleModelGraph(nodes: ModelNode[], edges: ModelEdge[]) {
  const allNodes = positionedNodes(nodes);
  const visibleIds = new Set(allNodes.map((node) => node.id));

  return {
    nodes: allNodes,
    edges: edges
      .filter((edge) => visibleIds.has(edge.sourceNodeId) && visibleIds.has(edge.targetNodeId))
      .map((edge) => clearEdgeMath(edge)),
  };
}

interface ModelPoint {
  x: number;
  y: number;
}

interface ModelRect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

function expandedNodeRect(node: ModelNode, padding = 12): ModelRect {
  const size = modelNodeSize(node);
  const x = node.positionX ?? 0;
  const y = node.positionY ?? 0;
  return {
    x1: x - padding,
    y1: y - padding,
    x2: x + size.width + padding,
    y2: y + size.height + padding,
  };
}

function segmentIntersectsRect(a: ModelPoint, b: ModelPoint, rect: ModelRect) {
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minY = Math.min(a.y, b.y);
  const maxY = Math.max(a.y, b.y);
  if (a.y === b.y) {
    return a.y >= rect.y1 && a.y <= rect.y2 && maxX >= rect.x1 && minX <= rect.x2;
  }
  if (a.x === b.x) {
    return a.x >= rect.x1 && a.x <= rect.x2 && maxY >= rect.y1 && minY <= rect.y2;
  }
  return false;
}

function pathCrossesAnyNode(points: ModelPoint[], obstacles: ModelRect[]) {
  return points.slice(0, -1).some((point, index) =>
    obstacles.some((rect) => segmentIntersectsRect(point, points[index + 1], rect))
  );
}

function pathLength(points: ModelPoint[]) {
  return points.slice(0, -1).reduce((total, point, index) => {
    const next = points[index + 1];
    return total + Math.abs(next.x - point.x) + Math.abs(next.y - point.y);
  }, 0);
}

function labelPointForPath(points: ModelPoint[]) {
  const total = pathLength(points);
  let travelled = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const length = Math.abs(next.x - current.x) + Math.abs(next.y - current.y);
    if (travelled + length >= total / 2) {
      const remaining = total / 2 - travelled;
      const directionX = Math.sign(next.x - current.x);
      const directionY = Math.sign(next.y - current.y);
      return {
        x: current.x + directionX * remaining,
        y: current.y + directionY * remaining,
      };
    }
    travelled += length;
  }
  return points[Math.max(0, Math.floor(points.length / 2))];
}

function pointsToPath(points: ModelPoint[]) {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");
}

function routeModelEdge(
  source: ModelNode,
  target: ModelNode,
  allNodes: ModelNode[],
  edgeIndex: number,
  allEdges: ModelEdge[] = []
) {
  const sourceSize = modelNodeSize(source);
  const targetSize = modelNodeSize(target);
  const sourceX = source.positionX ?? 0;
  const sourceY = source.positionY ?? 0;
  const targetX = target.positionX ?? 0;
  const targetY = target.positionY ?? 0;
  const sourceCenter = { x: sourceX + sourceSize.width / 2, y: sourceY + sourceSize.height / 2 };
  const targetCenter = { x: targetX + targetSize.width / 2, y: targetY + targetSize.height / 2 };
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  const obstacles = allNodes
    .filter((node) => node.id !== source.id && node.id !== target.id)
    .map((node) => expandedNodeRect(node));
  const laneSpread = ((edgeIndex % 5) - 2) * 10;
  const centerAlignedY = Math.abs(sourceCenter.y - targetCenter.y) < 3;
  if (Math.abs(dx) >= Math.abs(dy) && centerAlignedY && sourceX + sourceSize.width <= targetX) {
    const points = [
      { x: sourceX + sourceSize.width, y: sourceCenter.y },
      { x: targetX, y: targetCenter.y },
    ];
    const labelPoint = labelPointForPath(points);
    return { path: pointsToPath(points), labelX: labelPoint.x, labelY: labelPoint.y };
  }

  if (target.nodeType === "calculation" && Math.abs(dx) >= Math.abs(dy)) {
    const incomingEdges = allEdges.filter((edge) => edge.targetNodeId === target.id);
    const incomingSources = incomingEdges
      .flatMap((edge) => {
        const node = allNodes.find((candidate) => candidate.id === edge.sourceNodeId);
        return node && node.id !== target.id ? [node] : [];
      })
      .filter((node) => node.nodeType !== "calculation");
    const sourcesOnLeft = incomingSources.filter((node) => {
      const size = modelNodeSize(node);
      return (node.positionX ?? 0) + size.width <= targetX;
    });
    if (sourcesOnLeft.length >= 2 && sourceX + sourceSize.width <= targetX) {
      const nearestRight = Math.max(...sourcesOnLeft.map((node) => (node.positionX ?? 0) + modelNodeSize(node).width));
      const trunkX = Math.round((nearestRight + targetX) / 2);
      const start = { x: sourceX + sourceSize.width, y: sourceCenter.y };
      const end = { x: targetX, y: targetCenter.y };
      const points = [
        start,
        { x: trunkX, y: start.y },
        { x: trunkX, y: end.y },
        end,
      ];
      const labelPoint = labelPointForPath(points);
      return { path: pointsToPath(points), labelX: labelPoint.x, labelY: labelPoint.y };
    }
  }

  if (Math.abs(dx) >= Math.abs(dy)) {
    const exitsRight = dx >= 0;
    const start = {
      x: sourceX + (exitsRight ? sourceSize.width : 0),
      y: sourceCenter.y,
    };
    const end = {
      x: targetX + (exitsRight ? 0 : targetSize.width),
      y: targetCenter.y,
    };
    const middle = (start.x + end.x) / 2 + laneSpread;
    const direction = exitsRight ? 1 : -1;
    const candidates = [
      middle,
      start.x + direction * 46,
      end.x - direction * 46,
      ...obstacles.flatMap((rect) => [rect.x1 - direction * 18, rect.x2 + direction * 18]),
    ];
    const best = candidates
      .map((laneX) => ({
        laneX,
        points: [
          start,
          { x: laneX, y: start.y },
          { x: laneX, y: end.y },
          end,
        ],
      }))
      .sort((a, b) => {
        const aCrosses = pathCrossesAnyNode(a.points, obstacles) ? 1 : 0;
        const bCrosses = pathCrossesAnyNode(b.points, obstacles) ? 1 : 0;
        return aCrosses - bCrosses || pathLength(a.points) - pathLength(b.points);
      })[0];
    const labelPoint = labelPointForPath(best.points);
    return { path: pointsToPath(best.points), labelX: labelPoint.x, labelY: labelPoint.y };
  }

  const exitsDown = dy >= 0;
  const start = {
    x: sourceCenter.x,
    y: sourceY + (exitsDown ? sourceSize.height : 0),
  };
  const end = {
    x: targetCenter.x,
    y: targetY + (exitsDown ? 0 : targetSize.height),
  };
  const middle = (start.y + end.y) / 2 + laneSpread;
  const direction = exitsDown ? 1 : -1;
  const candidates = [
    middle,
    start.y + direction * 46,
    end.y - direction * 46,
    ...obstacles.flatMap((rect) => [rect.y1 - direction * 18, rect.y2 + direction * 18]),
  ];
  const best = candidates
    .map((laneY) => ({
      laneY,
      points: [
        start,
        { x: start.x, y: laneY },
        { x: end.x, y: laneY },
        end,
      ],
    }))
    .sort((a, b) => {
      const aCrosses = pathCrossesAnyNode(a.points, obstacles) ? 1 : 0;
      const bCrosses = pathCrossesAnyNode(b.points, obstacles) ? 1 : 0;
      return aCrosses - bCrosses || pathLength(a.points) - pathLength(b.points);
    })[0];
  const labelPoint = labelPointForPath(best.points);
  return { path: pointsToPath(best.points), labelX: labelPoint.x, labelY: labelPoint.y };
}

function centeredModelViewport(nodes: ModelNode[], canvasWidth: number, canvasHeight: number) {
  if (!nodes.length) {
    return { zoom: 1, panX: 0, panY: 0 };
  }
  const bounds = nodes.reduce(
    (acc, node) => {
      const size = modelNodeSize(node);
      const x = node.positionX ?? 0;
      const y = node.positionY ?? 0;
      return {
        minX: Math.min(acc.minX, x),
        minY: Math.min(acc.minY, y),
        maxX: Math.max(acc.maxX, x + size.width),
        maxY: Math.max(acc.maxY, y + size.height),
      };
    },
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }
  );
  const graphCenterX = (bounds.minX + bounds.maxX) / 2;
  const graphCenterY = (bounds.minY + bounds.maxY) / 2;
  return {
    zoom: 1,
    panX: Math.round(canvasWidth / 2 - graphCenterX),
    panY: Math.round(canvasHeight / 2 - graphCenterY),
  };
}

function ModelFlowDiagram({
  nodes,
  edges,
  variables,
  assumptions,
  onMoveNode,
}: {
  nodes: ModelNode[];
  edges: ModelEdge[];
  variables: ValidatedVariable[];
  assumptions: ModelAssumption[];
  onMoveNode: (nodeId: string, position: { x: number; y: number }) => void;
}) {
  const canvasWidth = 560;
  const canvasHeight = 420;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [dragState, setDragState] = useState<{
    nodeId: string;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const [panState, setPanState] = useState<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const [viewport, setViewport] = useState({ zoom: 1, panX: 0, panY: 0 });
  const lastCenteredGraphRef = useRef("");
  const gridSize = 20;
  const graph = visibleModelGraph(nodes, edges);
  const graphNodes = graph.nodes;
  const graphEdges = graph.edges;
  const nodeMap = new Map(graphNodes.map((node) => [node.id, node]));
  const graphViewportKey = graphNodes
    .map((node) => `${node.id}:${node.positionX ?? 0}:${node.positionY ?? 0}`)
    .join("|");

  useEffect(() => {
    if (lastCenteredGraphRef.current === graphViewportKey) {
      return;
    }
    lastCenteredGraphRef.current = graphViewportKey;
    if (dragState || panState) {
      return;
    }
    setViewport(centeredModelViewport(graphNodes, canvasWidth, canvasHeight));
  }, [graphViewportKey]);

  const pointFromEvent = (event: { clientX: number; clientY: number }) => {
    const svg = svgRef.current;
    if (!svg) {
      return { x: 0, y: 0 };
    }
    const rect = svg.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvasWidth,
      y: ((event.clientY - rect.top) / rect.height) * canvasHeight,
    };
  };

  const worldPointFromEvent = (event: MouseEvent<SVGSVGElement> | MouseEvent<SVGGElement>) => {
    const svg = svgRef.current;
    if (!svg) {
      return { x: 0, y: 0 };
    }
    const rect = svg.getBoundingClientRect();
    const point = {
      x: ((event.clientX - rect.left) / rect.width) * canvasWidth,
      y: ((event.clientY - rect.top) / rect.height) * canvasHeight,
    };
    return {
      x: (point.x - viewport.panX) / viewport.zoom,
      y: (point.y - viewport.panY) / viewport.zoom,
    };
  };

  const beginDrag = (event: MouseEvent<SVGGElement>, node: ModelNode) => {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const point = worldPointFromEvent(event);
    setDragState({
      nodeId: node.id,
      offsetX: point.x - (node.positionX ?? 0),
      offsetY: point.y - (node.positionY ?? 0),
    });
  };

  const moveDrag = (event: MouseEvent<SVGSVGElement>) => {
    if (!dragState) {
      return;
    }
    event.preventDefault();
    const node = nodeMap.get(dragState.nodeId);
    if (!node) {
      return;
    }
    const size = modelNodeSize(node);
    const point = worldPointFromEvent(event);
    const nextX = Math.round((point.x - dragState.offsetX) / gridSize) * gridSize;
    const nextY = Math.round((point.y - dragState.offsetY) / gridSize) * gridSize;
    onMoveNode(dragState.nodeId, {
      x: Math.max(-240, Math.min(960 - size.width, nextX)),
      y: Math.max(-180, Math.min(720 - size.height, nextY)),
    });
  };

  const beginPan = (event: MouseEvent<SVGSVGElement>) => {
    if (event.button !== 1) {
      return;
    }
    event.preventDefault();
    setPanState({
      startX: event.clientX,
      startY: event.clientY,
      panX: viewport.panX,
      panY: viewport.panY,
    });
  };

  const movePan = (event: MouseEvent<SVGSVGElement>) => {
    if (!panState || dragState) {
      return;
    }
    event.preventDefault();
    setViewport((prev) => ({
      ...prev,
      panX: panState.panX + event.clientX - panState.startX,
      panY: panState.panY + event.clientY - panState.startY,
    }));
  };

  const endDrag = () => {
    setDragState(null);
    setPanState(null);
  };

  const zoomCanvas = (event: WheelEvent<SVGSVGElement>) => {
    event.preventDefault();
    const point = pointFromEvent(event);
    setViewport((prev) => {
      const zoomFactor = event.deltaY < 0 ? 1.08 : 0.92;
      const nextZoom = Math.max(0.45, Math.min(2.4, prev.zoom * zoomFactor));
      const worldX = (point.x - prev.panX) / prev.zoom;
      const worldY = (point.y - prev.panY) / prev.zoom;
      return {
        zoom: nextZoom,
        panX: point.x - worldX * nextZoom,
        panY: point.y - worldY * nextZoom,
      };
    });
  };

  return (
    <svg
      ref={svgRef}
      className="model-flow"
      viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
      role="img"
      aria-label="Model flow"
      onMouseDown={beginPan}
      onMouseMove={(event) => {
        moveDrag(event);
        movePan(event);
      }}
      onMouseUp={endDrag}
      onMouseLeave={endDrag}
      onWheel={zoomCanvas}
      onAuxClick={(event) => {
        if (event.button === 1) {
          event.preventDefault();
        }
      }}
    >
      <defs>
        <marker id="model-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto" markerUnits="strokeWidth">
          <path className="model-arrow-head" d="M0 0 9 4.5 0 9z" />
        </marker>
        <pattern id="model-grid" width={gridSize} height={gridSize} patternUnits="userSpaceOnUse">
          <path className="model-grid-line" d={`M ${gridSize} 0 L 0 0 0 ${gridSize}`} />
        </pattern>
      </defs>
      <g transform={`translate(${viewport.panX} ${viewport.panY}) scale(${viewport.zoom})`}>
        <rect className="model-grid-fill" x="-1200" y="-900" width="2800" height="2200" fill="url(#model-grid)" />
      {graphEdges.map((edge, edgeIndex) => {
        const source = nodeMap.get(edge.sourceNodeId);
        const target = nodeMap.get(edge.targetNodeId);
        if (!source || !target) {
          return null;
        }
        const route = routeModelEdge(source, target, graphNodes, edgeIndex, graphEdges);
        return (
          <g key={edge.id} className="model-edge">
            <path d={route.path} markerEnd="url(#model-arrow)" />
          </g>
        );
      })}
      {graphNodes.map((node) => {
        const size = modelNodeSize(node);
        const x = node.positionX ?? 0;
        const y = node.positionY ?? 0;
        return (
          <g
            key={node.id}
            className={`model-node model-node-${node.nodeType}${isCustomCalculationNode(node) ? " model-node-custom-calculation" : ""}${dragState?.nodeId === node.id ? " dragging" : ""}`}
            transform={`translate(${x} ${y})`}
            onMouseDown={(event) => beginDrag(event, node)}
          >
            <title>{modelNodeTooltip(node, nodeMap, variables, assumptions)}</title>
            <rect width={size.width} height={size.height} rx={node.nodeType === "calculation" ? "20" : "10"} />
            <text x={size.width / 2} y={size.height / 2}>
              {node.nodeType === "calculation"
                ? isCustomCalculationNode(node)
                  ? truncateNodeLabel(node.label, 16)
                  : mathNodeLabel(node) || truncateNodeLabel(node.label, 6)
                : truncateNodeLabel(node.label, 32)}
            </text>
          </g>
        );
      })}
      </g>
    </svg>
  );
}

function ModelBuilderPane({
  variables,
  assumptions,
  nodes,
  edges,
  style,
  onMoveNode,
}: {
  variables: ValidatedVariable[];
  assumptions: ModelAssumption[];
  nodes: ModelNode[];
  edges: ModelEdge[];
  style?: CSSProperties;
  onMoveNode: (nodeId: string, position: { x: number; y: number }) => void;
}) {
  const [variablesOpen, setVariablesOpen] = useState(false);
  const [assumptionsOpen, setAssumptionsOpen] = useState(false);
  return (
    <aside className="workspace-pane model-pane" style={style} aria-label="Model Builder">
      <div className="model-flow-shell">
        <div className="canvas-info-stack" aria-label="Model context">
          <section className={`canvas-info-panel canvas-variables-panel${variablesOpen ? " open" : ""}`} aria-label="Approved model variables">
            <button
              type="button"
              className="canvas-info-toggle canvas-variables-toggle"
              onClick={() => setVariablesOpen((open) => !open)}
              aria-expanded={variablesOpen}
            >
              <span>Variables</span>
              <small>{variables.length}</small>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.4" />
              </svg>
            </button>
            {variablesOpen ? (
              <div className="canvas-variable-list">
                {variables.length ? (
                  variables.slice(0, 6).map((variable) => {
                    const fallbackDescription = [
                      variable.metric,
                      variable.geography,
                      variable.frequency,
                      variable.unit,
                      variable.sourceName,
                    ].filter(Boolean).join(" · ");
                    const description = variable.contentsSummary || fallbackDescription;
                    return (
                      <p key={variable.id}>
                        <span>{variable.label || variable.name}</span>
                        {description || variable.transformSummary ? <small>{description || variable.transformSummary}</small> : null}
                      </p>
                    );
                  })
                ) : (
                  <p className="pane-empty">None active.</p>
                )}
              </div>
            ) : null}
          </section>
          <section className={`canvas-info-panel canvas-assumptions-panel${assumptionsOpen ? " open" : ""}`} aria-label="Model assumptions">
            <button
              type="button"
              className="canvas-info-toggle canvas-assumptions-toggle"
              onClick={() => setAssumptionsOpen((open) => !open)}
              aria-expanded={assumptionsOpen}
            >
              <span>Assumptions</span>
              <small>{assumptions.length}</small>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.4" />
              </svg>
            </button>
            {assumptionsOpen ? (
              <div className="assumption-list canvas-assumption-list">
                {assumptions.length ? (
                  assumptions.slice(0, 5).map((assumption) => (
                    <p
                      key={assumption.id}
                      title={[assumption.valueText, assumption.method ? `Method: ${assumption.method}` : "", assumption.output ? `Output: ${assumption.output}` : ""].filter(Boolean).join("\n")}
                    >
                      <span>{assumption.label}</span>
                      {assumption.valueText ? <small>{assumption.valueText}</small> : null}
                    </p>
                  ))
                ) : (
                  <p className="pane-empty">None set.</p>
                )}
              </div>
            ) : null}
          </section>
        </div>
        <ModelFlowDiagram
          nodes={nodes}
          edges={edges}
          variables={variables}
          assumptions={assumptions}
          onMoveNode={onMoveNode}
        />
      </div>
    </aside>
  );
}

function PaneResizeHandle({
  label,
  active,
  onMouseDown,
  onDoubleClick,
}: {
  label: string;
  active: boolean;
  onMouseDown: (event: MouseEvent<HTMLDivElement>) => void;
  onDoubleClick?: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      className={`pane-resize-handle${active ? " active" : ""}`}
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
    >
    </div>
  );
}

function App() {
  const initialProject = createLocalProject("Untitled model");
  const storedActiveProjectId = readStoredActiveProjectId();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string>(
    () => initialProject.conversationId
  );
  const [projects, setProjects] = useState<ModellingProject[]>([]);
  const [activeProjectId, setActiveProjectId] = useState(storedActiveProjectId || initialProject.id);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [validatedVariables, setValidatedVariables] = useState<ValidatedVariable[]>([]);
  const [modelBuilderState, setModelBuilderState] = useState<ModelBuilderState>(initialProject.modelBuilderState);
  const [projectsPaneWidth, setProjectsPaneWidth] = useState(DEFAULT_PROJECTS_PANE_WIDTH);
  const [projectsPaneCollapsed, setProjectsPaneCollapsed] = useState(false);
  const [workspaceSplitPercent, setWorkspaceSplitPercent] = useState(DEFAULT_WORKSPACE_SPLIT_PERCENT);
  const [activeResizeHandle, setActiveResizeHandle] = useState<"" | "projects" | "model">("");
  const [authReady, setAuthReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeRunTaskId, setActiveRunTaskId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [queuedMessage, setQueuedMessage] = useState("");
  const [queuedMode, setQueuedMode] = useState<"" | "queued" | "steer">("");
  const [latestExportUrl, setLatestExportUrl] = useState("");
  const [latestExportStatus, setLatestExportStatus] = useState("");
  const scrollRef = useRef<HTMLElement | null>(null);
  const workspaceGridRef = useRef<HTMLDivElement | null>(null);
  const centreModelSplitRef = useRef<HTMLDivElement | null>(null);
  const pendingRef = useRef<PendingMessage | null>(null);
  const lastProgressRef = useRef("");
  const pollFailureCountRef = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const hydratedConversationRef = useRef("");
  const queuedSubmitRef = useRef(false);
  const lastChatPersistKeyRef = useRef("");
  const lastModelBuilderPersistKeyRef = useRef("");
  const modelBuilderPersistTimerRef = useRef<number | null>(null);
  const displayName =
    String(
      session?.user?.user_metadata?.display_name ||
        session?.user?.user_metadata?.full_name ||
        session?.user?.email ||
        ""
    ).trim() || "Signed in";
  const displayedVariables = modelBuilderState.variables.length
    ? modelBuilderState.variables
    : validatedVariables;
  const displayedAssumptions = modelBuilderState.assumptions;
  const fallbackGraph = buildFallbackGraph(displayedVariables);
  const displayedNodes = modelBuilderState.nodes.length
    ? modelBuilderState.nodes
    : fallbackGraph.nodes;
  const displayedEdges = modelBuilderState.edges.length
    ? modelBuilderState.edges
    : fallbackGraph.edges;

  const persistModelBuilderState = (nextState: ModelBuilderState) => {
    if (!session?.user?.id || !activeProjectId) {
      return;
    }
    const persistKey = JSON.stringify(nextState);
    lastModelBuilderPersistKeyRef.current = persistKey;
    if (modelBuilderPersistTimerRef.current !== null) {
      window.clearTimeout(modelBuilderPersistTimerRef.current);
    }
    modelBuilderPersistTimerRef.current = window.setTimeout(() => {
      void updateProjectModelBuilderState(activeProjectId, nextState).then(({ error: projectError }) => {
        if (projectError) {
          console.error("Failed to persist model builder layout", projectError);
        }
      });
    }, 350);
  };

  const moveModelNode = (nodeId: string, position: { x: number; y: number }) => {
    const baseState: ModelBuilderState = {
      variables: displayedVariables,
      assumptions: displayedAssumptions,
      nodes: displayedNodes,
      edges: displayedEdges,
    };
    const nextState: ModelBuilderState = {
      ...baseState,
      nodes: positionedNodes(baseState.nodes).map((node) =>
        node.id === nodeId ? { ...node, positionX: position.x, positionY: position.y } : node
      ),
    };
    setModelBuilderState(nextState);
    setProjects((prev) =>
      prev.map((project) =>
        project.id === activeProjectId
          ? { ...project, modelBuilderState: nextState, updatedAt: new Date().toISOString() }
          : project
      )
    );
    persistModelBuilderState(nextState);
  };

  const syncComposerHeight = () => {
    const element = composerRef.current;
    if (!element) {
      return;
    }
    element.style.height = "0px";
    const maxHeight = Number.parseFloat(window.getComputedStyle(element).maxHeight || "0");
    const nextHeight = element.scrollHeight;
    element.style.height = `${nextHeight}px`;
    element.style.overflowY = maxHeight > 0 && nextHeight > maxHeight ? "auto" : "hidden";
  };

  const beginWorkspaceResize = (handle: "projects" | "model", event: MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setActiveResizeHandle(handle);
    if (handle === "projects") {
      setProjectsPaneCollapsed(false);
    }
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";

    const resizeFromClientX = (clientX: number) => {
      if (handle === "projects") {
        const rect = workspaceGridRef.current?.getBoundingClientRect();
        if (!rect) {
          return;
        }
        const maxWidth = Math.min(MAX_PROJECTS_PANE_WIDTH, Math.max(MIN_PROJECTS_PANE_WIDTH, rect.width - 720));
        const nextWidth = clientX - rect.left;
        setProjectsPaneWidth(Math.max(MIN_PROJECTS_PANE_WIDTH, Math.min(nextWidth, maxWidth)));
        return;
      }

      const rect = centreModelSplitRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      const rawPercent = ((clientX - rect.left) / Math.max(rect.width, 1)) * 100;
      const minPercent = Math.max(26, (MIN_WORKSPACE_PANE_WIDTH / Math.max(rect.width, 1)) * 100);
      const maxPercent = Math.min(82, 100 - (MIN_WORKSPACE_PANE_WIDTH / Math.max(rect.width, 1)) * 100);
      setWorkspaceSplitPercent(Math.max(minPercent, Math.min(rawPercent, maxPercent)));
    };

    const handleMouseMove = (moveEvent: globalThis.MouseEvent) => {
      moveEvent.preventDefault();
      resizeFromClientX(moveEvent.clientX);
    };

    const handleMouseUp = () => {
      setActiveResizeHandle("");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };

    resizeFromClientX(event.clientX);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
  };

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isStreaming]);

  useEffect(() => {
    syncComposerHeight();
  }, [input]);

  const syncPendingState = (payload: ConversationSnapshotResponse) => {
    const nextMessage = String(payload.pending_user_message ?? "").trim();
    const rawMode = String(payload.pending_user_mode ?? "").trim().toLowerCase();
    const nextMode = rawMode === "queued" || rawMode === "steer" ? rawMode : "";
    setQueuedMessage(nextMessage);
    setQueuedMode(nextMode);
    setLatestExportUrl(String(payload.latest_export_url ?? "").trim());
    setLatestExportStatus(String(payload.latest_export_status ?? "").trim().toLowerCase());
    if (payload.model_builder_state && typeof payload.model_builder_state === "object") {
      const nextModelBuilderState = parseModelBuilderState(payload.model_builder_state);
      const nextPersistKey = JSON.stringify(nextModelBuilderState);
      if (nextPersistKey !== lastModelBuilderPersistKeyRef.current) {
        lastModelBuilderPersistKeyRef.current = nextPersistKey;
        setModelBuilderState(nextModelBuilderState);
        setProjects((prev) =>
          prev.map((project) =>
            project.id === activeProjectId
              ? { ...project, modelBuilderState: nextModelBuilderState, updatedAt: new Date().toISOString() }
              : project
          )
        );
      }
    }
  };

  const storePendingMessage = async (message: string, mode: "queued" | "steer") => {
    const response = await fetch(`${API_BASE}/api/pending-message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        message,
        mode,
      }),
    });
    if (!response.ok) {
      throw new Error(`Failed to store pending message: ${response.status}`);
    }
    const payload = (await response.json()) as ConversationSnapshotResponse;
    syncPendingState(payload);
    return payload;
  };

  const consumePendingMessage = async () => {
    const response = await fetch(`${API_BASE}/api/pending-message/consume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    if (!response.ok) {
      throw new Error(`Failed to clear pending message: ${response.status}`);
    }
    const payload = (await response.json()) as ConversationSnapshotResponse;
    syncPendingState(payload);
    return payload;
  };

  const loadStoredChatHistory = async (projectId: string) => {
    const { data, error: chatError } = await supabase
      .from("modelling_chat_messages")
      .select("id,user_message,progress_notes,final_response,run_cost,run_index,status,conversation_id,created_at")
      .eq("project_id", projectId)
      .order("created_at", { ascending: true });
    if (chatError) {
      console.error("Failed to load stored chat history", chatError);
      return [];
    }
    return mapStoredChatRuns(data);
  };

  useEffect(() => {
    let active = true;

    supabase.auth.getSession().then(({ data, error: sessionError }) => {
      if (!active) {
        return;
      }
      if (sessionError) {
        setAuthError(sessionError.message);
      }
      setSession(data.session ?? null);
      setAuthReady(true);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthReady(true);
      setAuthError(null);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, activeProjectId);
    }
  }, [activeProjectId]);

  useEffect(() => {
    if (!authReady || !session) {
      return;
    }

    let active = true;
    setProjectsLoading(true);

    void supabase
      .from("modelling_projects")
      .select(PROJECT_SELECT_COLUMNS)
      .neq("status", "archived")
      .order("updated_at", { ascending: false })
      .then(({ data, error: projectError }) => {
        if (!active) {
          return;
        }
        if (projectError) {
          console.error("Failed to load modelling projects", projectError);
          setError(projectError.message);
          setProjectsLoading(false);
          return;
        }
        const loadedProjects = Array.isArray(data)
          ? data.map((row) => mapProjectRow(row as Record<string, unknown>))
          : [];
        if (!loadedProjects.length) {
          const draft = createLocalProject("Untitled model");
          void insertProjectRow(session.user.id, draft)
            .then(({ data: createdData, error: createError }) => {
              if (!active) {
                return;
              }
              if (createError || !createdData) {
                console.error("Failed to create initial modelling project", createError);
                return;
              }
              const createdProject = mapProjectRow(createdData as Record<string, unknown>);
              setProjects([createdProject]);
              setActiveProjectId(createdProject.id);
              setConversationId(createdProject.conversationId);
              setModelBuilderState(createdProject.modelBuilderState);
              setMessages([]);
              setQueuedMessage("");
              setQueuedMode("");
              setLatestExportUrl("");
              setLatestExportStatus("");
              hydratedConversationRef.current = "";
              setProjectsLoading(false);
            });
          return;
        }
        const storedId = readStoredActiveProjectId();
        const nextActiveProject =
          loadedProjects.find((project) => project.id === storedId) || loadedProjects[0];
        setProjects(loadedProjects);
        setActiveProjectId(nextActiveProject.id);
        setConversationId(nextActiveProject.conversationId);
        setModelBuilderState(nextActiveProject.modelBuilderState);
        if (nextActiveProject.conversationId !== conversationId) {
          setMessages([]);
        }
        setQueuedMessage("");
        setQueuedMode("");
        setLatestExportUrl("");
        setLatestExportStatus("");
        hydratedConversationRef.current = "";
        setProjectsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [authReady, session]);

  useEffect(() => {
    if (!authReady || !session || !activeProjectId) {
      return;
    }

    let active = true;

    async function loadModelBuilderState() {
      const activeProject = projects.find((project) => project.id === activeProjectId);
      const activeVariableIds = activeProject?.activeValidatedVariableIds || [];
      if (!activeVariableIds.length) {
        setValidatedVariables([]);
        return;
      }

      const variablesResult = await supabase
        .from("validated_variables")
        .select("id,name,label,source_name,metric,unit,geography,frequency,seasonal_treatment,transform_summary,validation_status,evidence_artifact")
        .in("id", activeVariableIds);
      if (!active) {
        return;
      }
      if (variablesResult.error) {
        console.error("Failed to load active project variables", variablesResult.error);
        setValidatedVariables([]);
        return;
      }

      const loadedVariables = Array.isArray(variablesResult.data)
        ? variablesResult.data.map((row) => mapVariableRow(row as Record<string, unknown>))
        : [];
      const byId = new Map(loadedVariables.map((variable) => [variable.id, variable]));
      setValidatedVariables(activeVariableIds.flatMap((id) => byId.get(id) || []));
    }

    void loadModelBuilderState();

    return () => {
      active = false;
    };
  }, [activeProjectId, authReady, projects, session]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    lastChatPersistKeyRef.current = "";
  }, [activeProjectId, conversationId]);

  useEffect(() => {
    if (!authReady || !session || !activeProjectId || !conversationId) {
      return;
    }
    if (hydratedConversationRef.current === conversationId) {
      return;
    }

    let active = true;
    hydratedConversationRef.current = conversationId;
    if (!pendingRef.current) {
      setIsStreaming(false);
      lastProgressRef.current = "";
    }

    void (async () => {
      try {
        const storedMessages = await loadStoredChatHistory(activeProjectId);
        if (!active) {
          return;
        }
        if (!pendingRef.current) {
          setMessages(storedMessages);
        }

        const response = await fetch(`${API_BASE}/api/conversation/${encodeURIComponent(conversationId)}`);
        if (!response.ok) {
          throw new Error(`Failed to load conversation: ${response.status}`);
        }
        const payload = (await response.json()) as ConversationSnapshotResponse;
        if (!active) {
          return;
        }
        const backendMessages = mapBackendMessages(payload.messages);
        const runStatus = String(payload.run_status ?? "").trim().toLowerCase();
        const taskId = String(payload.task_id ?? "").trim();
        if (backendMessages.length && runStatus === "processing") {
          const existingPending = pendingRef.current;
          const assistantMessageId = existingPending?.id || createConversationId();
          const hasAssistantMessage = backendMessages.some((message) => message.sender === "assistant");
          setMessages(
            hasAssistantMessage
              ? backendMessages
              : [
                  ...backendMessages,
                  {
                    id: assistantMessageId,
                    sender: "assistant",
                    content: "",
                  },
                ]
          );
          pendingRef.current = {
            id: assistantMessageId,
            userId: existingPending?.userId || createConversationId(),
            taskId: taskId || existingPending?.taskId,
          };
          if (taskId) {
            setActiveRunTaskId(taskId);
          }
          setIsStreaming(true);
        }
        syncPendingState(payload);
        if (runStatus === "processing") {
          const existingPending = pendingRef.current;
          if (!existingPending) {
            const assistantMessageId = createConversationId();
            pendingRef.current = {
              id: assistantMessageId,
              userId: createConversationId(),
              taskId: taskId || undefined,
            };
            setMessages((prev) =>
              prev.some((message) => message.sender === "assistant" && !message.content.trim())
                ? prev
                : [
                    ...prev,
                    {
                      id: assistantMessageId,
                      sender: "assistant",
                      content: "",
                    },
                  ]
            );
          }
          if (taskId) {
            setActiveRunTaskId(taskId);
          }
          lastProgressRef.current = "";
          setIsStreaming(true);
          return;
        }
        const latestError = String(payload.latest_error ?? "").trim();
        if (latestError) {
          setError(latestError);
        }
      } catch (loadError) {
        console.error("Failed to load saved conversation", loadError);
      }
    })();

    return () => {
      active = false;
    };
  }, [activeProjectId, authReady, conversationId, session]);

  const switchProject = (project: ModellingProject) => {
    if (project.id === activeProjectId) {
      return;
    }
    if (isStreaming) {
      void fetch(`${API_BASE}/api/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId }),
        keepalive: true,
      }).catch((cancelError) => {
        console.error("Failed to cancel active run before project switch", cancelError);
      });
    }
    setActiveProjectId(project.id);
    setConversationId(project.conversationId);
    setMessages([]);
    setInput("");
    setError(null);
    setIsStreaming(false);
    setActiveRunTaskId("");
    setQueuedMessage("");
    setQueuedMode("");
    setLatestExportUrl("");
    setLatestExportStatus("");
    setValidatedVariables([]);
    setModelBuilderState(project.modelBuilderState);
    pendingRef.current = null;
    lastProgressRef.current = "";
    queuedSubmitRef.current = false;
    hydratedConversationRef.current = "";
    lastChatPersistKeyRef.current = "";
    lastModelBuilderPersistKeyRef.current = JSON.stringify(project.modelBuilderState);
  };

  const createProject = async () => {
    const draft = createLocalProject("Untitled model");
    let nextProject = draft;

    if (session?.user?.id) {
      const { data, error: projectError } = await insertProjectRow(session.user.id, draft);
      if (projectError) {
        console.error("Failed to create modelling project", projectError);
      } else if (data) {
        nextProject = mapProjectRow(data as Record<string, unknown>);
      }
    }

    setProjects((prev) => [nextProject, ...prev.filter((project) => project.id !== nextProject.id)]);
    switchProject(nextProject);
  };

  const renameProject = (projectId: string, name: string) => {
    const nextName = name.trim() || "Untitled model";
    setProjects((prev) =>
      prev.map((project) =>
        project.id === projectId ? { ...project, name: nextName, updatedAt: new Date().toISOString() } : project
      )
    );
    void supabase
      .from("modelling_projects")
      .update({ name: nextName, updated_at: new Date().toISOString() })
      .eq("id", projectId)
      .then(({ error: projectError }) => {
        if (projectError) {
          console.error("Failed to rename modelling project", projectError);
        }
      });
  };

  const activateProject = (project: ModellingProject) => {
    setActiveProjectId(project.id);
    setConversationId(project.conversationId);
    setMessages([]);
    setInput("");
    setError(null);
    setIsStreaming(false);
    setActiveRunTaskId("");
    setQueuedMessage("");
    setQueuedMode("");
    setLatestExportUrl("");
    setLatestExportStatus("");
    setValidatedVariables([]);
    setModelBuilderState(project.modelBuilderState);
    pendingRef.current = null;
    lastProgressRef.current = "";
    queuedSubmitRef.current = false;
    hydratedConversationRef.current = "";
    lastChatPersistKeyRef.current = "";
    lastModelBuilderPersistKeyRef.current = JSON.stringify(project.modelBuilderState);
  };

  const deleteProject = async (project: ModellingProject) => {
    const confirmed = window.confirm(`Delete "${project.name}"? This will remove its chat, variables, and model state.`);
    if (!confirmed) {
      return;
    }

    if (isStreaming && project.id === activeProjectId) {
      void fetch(`${API_BASE}/api/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: project.conversationId }),
        keepalive: true,
      }).catch((cancelError) => {
        console.error("Failed to cancel active run before deleting project", cancelError);
      });
    }

    if (session?.user?.id) {
      const { error: projectError } = await supabase
        .from("modelling_projects")
        .delete()
        .eq("id", project.id)
        .eq("user_id", session.user.id);
      if (projectError) {
        console.error("Failed to delete modelling project", projectError);
        setError(projectError.message);
        return;
      }
    }

    void fetch(`${API_BASE}/api/clear`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: project.conversationId }),
      keepalive: true,
    }).catch((clearError) => {
      console.error("Failed to clear deleted project runtime state", clearError);
    });

    const remainingProjects = projects.filter((candidate) => candidate.id !== project.id);
    if (remainingProjects.length > 0) {
      setProjects(remainingProjects);
      if (project.id === activeProjectId) {
        activateProject(remainingProjects[0]);
      }
      return;
    }

    const draft = createLocalProject("Untitled model");
    let nextProject = draft;
    if (session?.user?.id) {
      const { data, error: createError } = await insertProjectRow(session.user.id, draft);
      if (createError) {
        console.error("Failed to create replacement project", createError);
      } else if (data) {
        nextProject = mapProjectRow(data as Record<string, unknown>);
      }
    }
    setProjects([nextProject]);
    activateProject(nextProject);
  };

  const updateActiveProjectQuestion = (question: string) => {
    const activeProject = projects.find((project) => project.id === activeProjectId);
    if (!activeProject || activeProject.question.trim()) {
      return;
    }
    const nextName = question.length > 48 ? `${question.slice(0, 45).trim()}...` : question;
    setProjects((prev) =>
      prev.map((project) =>
        project.id === activeProjectId
          ? {
              ...project,
              name: project.name === "Untitled model" ? nextName : project.name,
              question,
              status: "active",
              updatedAt: new Date().toISOString(),
            }
          : project
      )
    );
    void supabase
      .from("modelling_projects")
      .update({
        name: activeProject.name === "Untitled model" ? nextName : activeProject.name,
        question,
        status: "active",
        updated_at: new Date().toISOString(),
      })
      .eq("id", activeProjectId)
      .then(({ error: projectError }) => {
        if (projectError) {
          console.error("Failed to update project question", projectError);
        }
      });
  };

  const resetConversation = async () => {
    const previousConversationId = conversationId;
    const nextConversationId = createConversationId();

    setMessages([]);
    setInput("");
    setError(null);
    setIsStreaming(false);
    setActiveRunTaskId("");
    setQueuedMessage("");
    setQueuedMode("");
    setLatestExportUrl("");
    setLatestExportStatus("");
    pendingRef.current = null;
    lastProgressRef.current = "";
    queuedSubmitRef.current = false;
    hydratedConversationRef.current = "";
    lastChatPersistKeyRef.current = "";
    lastModelBuilderPersistKeyRef.current = "";
    setConversationId(nextConversationId);
    setProjects((prev) =>
      prev.map((project) =>
        project.id === activeProjectId ? { ...project, conversationId: nextConversationId } : project
      )
    );
    void supabase
      .from("modelling_projects")
      .update({ conversation_id: nextConversationId, updated_at: new Date().toISOString() })
      .eq("id", activeProjectId)
      .then(({ error: projectError }) => {
        if (projectError) {
          console.error("Failed to update project conversation", projectError);
        }
      });
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }

    void fetch(`${API_BASE}/api/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: previousConversationId }),
      keepalive: true,
    }).catch((resetError) => {
      console.error("Failed to reset conversation", resetError);
    });
  };

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setAuthError("Enter your email and password.");
      return;
    }
    setAuthBusy(true);
    setAuthError(null);
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    if (signInError) {
      setAuthError(signInError.message);
    } else {
      setPassword("");
    }
    setAuthBusy(false);
  };

  const handleSignOut = async () => {
    setAuthBusy(true);
    await resetConversation();
    const { error: signOutError } = await supabase.auth.signOut();
    if (signOutError) {
      setAuthError(signOutError.message);
    }
    setAuthBusy(false);
  };

  const startPromptRun = async (trimmedPrompt: string) => {
    updateActiveProjectQuestion(trimmedPrompt);

    const userMessage: ChatMessage = {
      id: createConversationId(),
      sender: "user",
      content: trimmedPrompt,
    };

    const assistantMessage: ChatMessage = {
      id: createConversationId(),
      sender: "assistant",
      content: "",
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");
    setIsStreaming(true);
    setError(null);
    lastProgressRef.current = "";
    pollFailureCountRef.current = 0;

    const pendingState: PendingMessage = {
      id: assistantMessage.id,
      userId: userMessage.id,
    };
    pendingRef.current = pendingState;

    try {
      const activeProject = projects.find((project) => project.id === activeProjectId);
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
          message: trimmedPrompt,
          project_id: activeProjectId,
          project_name: activeProject?.name ?? "",
          user_id: session?.user?.id ?? "",
        }),
      });

      if (!response.ok) {
        throw new Error(
          `Request failed with status ${response.status} ${response.statusText}`
        );
      }
      const payload = (await response.json()) as ChatAcceptedResponse;
      const taskId = String(payload.task_id ?? "").trim();
      if (!taskId) {
        throw new Error("Chat response missing task id.");
      }
      pendingRef.current = {
        ...pendingState,
        taskId,
      };
      setActiveRunTaskId(taskId);
      const rawInitialProgress = String(payload.latest_progress ?? "").trim();
      const initialProgress = simplifyStatusMessage(rawInitialProgress) || rawInitialProgress;
      if (initialProgress && initialProgress !== lastProgressRef.current) {
        lastProgressRef.current = initialProgress;
        appendProgressMessage(setMessages, assistantMessage.id, initialProgress);
      }
    } catch (err) {
      console.error(err);
      const errorMessage = err instanceof Error ? err.message : "Failed to reach the server.";
      setError(errorMessage);
      setMessages((prev) =>
        prev.map((messageItem) =>
          messageItem.id === assistantMessage.id
            ? { ...messageItem, content: errorMessage }
            : messageItem
        )
      );
      setIsStreaming(false);
      setActiveRunTaskId("");
      pendingRef.current = null;
    }
  };

  const submitPrompt = async (prompt: string) => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt) return;

    if (isStreaming) {
      try {
        await storePendingMessage(trimmedPrompt, "queued");
        setInput("");
        setError(null);
      } catch (err) {
        console.error(err);
        const message =
          err instanceof Error ? err.message : "Failed to queue the message.";
        setError(message);
      }
      return;
    }

    await startPromptRun(trimmedPrompt);
  };

  const handleSteer = async () => {
    const message = queuedMessage.trim();
    if (!message || queuedMode !== "queued") {
      return;
    }
    try {
      await storePendingMessage(message, "steer");
      setError(null);
    } catch (err) {
      console.error(err);
      const nextError =
        err instanceof Error ? err.message : "Failed to steer the active run.";
      setError(nextError);
    }
  };

  const handleClearQueued = async () => {
    if (!queuedMessage.trim()) {
      return;
    }
    try {
      await consumePendingMessage();
      setError(null);
    } catch (err) {
      console.error(err);
      const nextError =
        err instanceof Error ? err.message : "Failed to clear the queued message.";
      setError(nextError);
    }
  };

  useEffect(() => {
    if (!conversationId || !activeRunTaskId || !isStreaming || !pendingRef.current) {
      return;
    }

    let cancelled = false;
    const assistantMessageId = pendingRef.current.id;
    const taskId = activeRunTaskId;

    const pollOnce = async () => {
      try {
        const response = await fetch(
          `${API_BASE}/api/chat/task-status/${encodeURIComponent(conversationId)}/${encodeURIComponent(taskId)}`
        );
        if (!response.ok) {
          throw new Error(`Failed to poll task: ${response.status}`);
        }
        const payload = (await response.json()) as ConversationSnapshotResponse;
        if (cancelled) {
          return;
        }

        syncPendingState(payload);

        pollFailureCountRef.current = 0;
        setError((prev) => (prev === "Connection interrupted. Retrying..." ? null : prev));

        const runStatus = String(payload.run_status ?? "").trim().toLowerCase();
        const rawLatestProgress = String(payload.latest_progress ?? "").trim();
        const latestProgress = simplifyStatusMessage(rawLatestProgress) || rawLatestProgress;
        const latestError = String(payload.latest_error ?? "").trim();

        if (latestProgress && latestProgress !== lastProgressRef.current) {
          lastProgressRef.current = latestProgress;
          appendProgressMessage(setMessages, assistantMessageId, latestProgress);
        }

        if (runStatus === "completed") {
          const storedMessages = await loadStoredChatHistory(activeProjectId);
          if (storedMessages.length) {
            setMessages(storedMessages);
          } else {
            applyCompletedTaskSnapshot(payload, setMessages, assistantMessageId);
          }
          setIsStreaming(false);
          setActiveRunTaskId("");
          pendingRef.current = null;
          const queuedAfterRun = String(payload.pending_user_message ?? "").trim();
          const queuedModeAfterRun = String(payload.pending_user_mode ?? "").trim().toLowerCase();
          if (queuedAfterRun && queuedModeAfterRun === "queued" && !queuedSubmitRef.current) {
            queuedSubmitRef.current = true;
            try {
              await consumePendingMessage();
              await startPromptRun(queuedAfterRun);
            } catch (queuedError) {
              console.error(queuedError);
              const nextError =
                queuedError instanceof Error
                  ? queuedError.message
                  : "Failed to start the queued message.";
              setError(nextError);
            } finally {
              queuedSubmitRef.current = false;
            }
          }
          return;
        }

        if (runStatus === "failed" || runStatus === "cancelled") {
          const errorText = latestError || "The background run ended before returning a response.";
          setError(errorText);
          if (!applyCompletedTaskSnapshot(payload, setMessages, assistantMessageId)) {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId ? { ...message, content: errorText } : message
              )
            );
          }
          setIsStreaming(false);
          setActiveRunTaskId("");
          pendingRef.current = null;
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        console.error(err);
        pollFailureCountRef.current += 1;
        if (pollFailureCountRef.current < MAX_POLL_FAILURES) {
          setError("Connection interrupted. Retrying...");
          return;
        }
        const message =
          err instanceof Error ? err.message : "Failed to reach the server.";
        setError(message);
        setMessages((prev) =>
          prev.map((messageItem) =>
            messageItem.id === assistantMessageId
              ? { ...messageItem, content: message }
              : messageItem
          )
        );
        setIsStreaming(false);
        setActiveRunTaskId("");
        pendingRef.current = null;
      }
    };

    void pollOnce();
    const timer = window.setInterval(() => {
      void pollOnce();
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeProjectId, activeRunTaskId, conversationId, isStreaming]);

  useEffect(() => {
    if (!conversationId || isStreaming || latestExportStatus !== "processing") {
      return;
    }

    let cancelled = false;

    const pollExport = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/conversation/${encodeURIComponent(conversationId)}`);
        if (!response.ok) {
          throw new Error(`Failed to poll export status: ${response.status}`);
        }
        const payload = (await response.json()) as ConversationSnapshotResponse;
        if (cancelled) {
          return;
        }
        syncPendingState(payload);
      } catch (err) {
        if (!cancelled) {
          console.error(err);
        }
      }
    };

    void pollExport();
    const timer = window.setInterval(() => {
      void pollExport();
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [conversationId, isStreaming, latestExportStatus]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await submitPrompt(input);
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const form = event.currentTarget.form;
      form?.requestSubmit();
    }
  };

  const handleComposerBeforeInput = (event: FormEvent<HTMLTextAreaElement>) => {
    const nativeEvent = event.nativeEvent as InputEvent | undefined;
    if (nativeEvent?.isComposing) {
      return;
    }
    if (nativeEvent?.inputType === "insertLineBreak") {
      event.preventDefault();
      const form = event.currentTarget.form;
      form?.requestSubmit();
    }
  };

  const lastCompletedAssistantIndex = messages.reduce((lastIndex, message, index) => {
    if (message.sender === "assistant" && message.content.trim()) {
      return index;
    }
    return lastIndex;
  }, -1);

  if (!authReady) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <ProductTitle />
          <p>Checking your session.</p>
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="auth-shell">
        <form className="auth-card" onSubmit={handleLogin}>
          <ProductTitle />
          <label className="auth-field">
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              placeholder="Email"
              disabled={authBusy}
            />
          </label>
          <label className="auth-field">
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              placeholder="Password"
              disabled={authBusy}
            />
          </label>
          {authError && <div className="auth-error">{authError}</div>}
          <button type="submit" className="auth-submit" disabled={authBusy}>
            {authBusy ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    );
  }

  const projectsPaneDisplayWidth = projectsPaneCollapsed ? COLLAPSED_PROJECTS_PANE_WIDTH : projectsPaneWidth;

  return (
    <div className="app-shell">
      <div ref={workspaceGridRef} className="workspace-grid">
        <ProjectsPane
          projects={projects}
          activeProjectId={activeProjectId}
          loading={projectsLoading}
          displayName={displayName}
          authBusy={authBusy}
          collapsed={projectsPaneCollapsed}
          style={{
            flex: `0 0 ${projectsPaneDisplayWidth}px`,
            maxWidth: `${projectsPaneDisplayWidth}px`,
          }}
          onToggleCollapsed={() => {
            if (projectsPaneCollapsed) {
              setProjectsPaneWidth(DEFAULT_PROJECTS_PANE_WIDTH);
            }
            setProjectsPaneCollapsed((collapsed) => !collapsed);
          }}
          onCreateProject={() => void createProject()}
          onSelectProject={switchProject}
          onRenameProject={renameProject}
          onDeleteProject={(project) => void deleteProject(project)}
          onSignOut={() => void handleSignOut()}
        />

        <PaneResizeHandle
          label="Resize projects"
          active={activeResizeHandle === "projects"}
          onMouseDown={(event) => beginWorkspaceResize("projects", event)}
          onDoubleClick={(event) => {
            event.preventDefault();
            setProjectsPaneWidth(DEFAULT_PROJECTS_PANE_WIDTH);
            setProjectsPaneCollapsed(false);
          }}
        />

        <div ref={centreModelSplitRef} className="workspace-split">
        <section
          className="center-workspace"
          style={{
            flexGrow: workspaceSplitPercent,
            flexBasis: 0,
          }}
          aria-label="AI workspace"
        >
      <main ref={scrollRef} className="app-main">
        <section className="chat-panel">
          {messages.map((message, index) =>
            message.sender === "progress" ? (
              <article key={message.id} className="bubble-row progress">
                <div className={`progress-step${isProgressSubtask(message.content) ? " progress-step-subtask" : ""}`}>
                  <span className="progress-rail" aria-hidden="true">
                    <span className="progress-marker" />
                  </span>
                  <span className="progress-line">{message.content}</span>
                </div>
              </article>
            ) : message.sender === "assistant" ? (
              <article key={message.id} className="bubble-row assistant-text">
                {message.content ? (
                  <div className="assistant-text-block">
                    {renderContentBlocks(message.content)}
                    <div className="assistant-meta-row">
                      {renderRunCost(message.runCost)}
                      {!isStreaming && latestExportUrl && index === lastCompletedAssistantIndex ? (
                        <div className="assistant-export-link">
                          <a href={`${API_BASE}${latestExportUrl}`} target="_blank" rel="noreferrer" aria-label="Download Excel export">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path fill="currentColor" d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Zm0 1.5L17.5 7H14ZM9.2 10.6h1.6l1.3 2.2 1.3-2.2H15l-2.1 3.2 2.2 3.6h-1.7l-1.4-2.4-1.4 2.4H8.9l2.2-3.6Z"/>
                            </svg>
                          </a>
                        </div>
                      ) : !isStreaming &&
                        latestExportStatus === "processing" &&
                        index === lastCompletedAssistantIndex ? (
                        <div className="assistant-export-pending" aria-live="polite" aria-label="Preparing Excel export">
                          <span className="assistant-export-spinner" aria-hidden="true" />
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <div className="thinking-line" aria-live="polite" aria-label="Thinking">
                    <AusDataLoader />
                  </div>
                )}
              </article>
            ) : (
              <article
                key={message.id}
                className="bubble-row user"
              >
                <div className="bubble">
                  <div className="rich-content">{renderContentBlocks(message.content)}</div>
                </div>
              </article>
            )
          )}

          {error && <div className="error-banner">{error}</div>}
        </section>
      </main>

      <footer className="app-footer">
        <form onSubmit={handleSubmit} className="composer">
          {queuedMessage && (
            <div className="queued-message-banner">
              <div className="queued-message-copy">
                <span className="queued-message-label">
                  {queuedMode === "steer" ? "Steering next" : "Queued"}
                </span>
                <span className="queued-message-text">{queuedMessage}</span>
              </div>
              <div className="queued-message-actions">
                {isStreaming && queuedMode === "queued" ? (
                  <button
                    type="button"
                    className="queued-message-pill"
                    onClick={() => void handleSteer()}
                  >
                    Steer
                  </button>
                ) : queuedMode === "steer" ? (
                  <span className="queued-message-pill queued-message-pill-passive">
                    Steering
                  </span>
                ) : null}
                <button
                  type="button"
                  className="queued-message-clear"
                  onClick={() => void handleClearQueued()}
                  aria-label="Delete queued message"
                >
                  ×
                </button>
              </div>
            </div>
          )}
          <div className="composer-input-shell">
            <textarea
              ref={composerRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              onBeforeInput={handleComposerBeforeInput}
              placeholder="Ask Nisaba to define, validate, or run the model..."
              enterKeyHint="send"
              rows={1}
            />
            <button
              type="submit"
              disabled={!input.trim()}
              className="icon-send-button"
              aria-label={isStreaming ? "Queue message" : "Send message"}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M3.4 20.6 21 12 3.4 3.4l2.8 6.8 8 1.8-8 1.8-2.8 6.8Z"
                  fill="currentColor"
                />
              </svg>
            </button>
          </div>
        </form>
      </footer>
        </section>

        <PaneResizeHandle
          label="Resize model builder"
          active={activeResizeHandle === "model"}
          onMouseDown={(event) => beginWorkspaceResize("model", event)}
          onDoubleClick={(event) => {
            event.preventDefault();
            setWorkspaceSplitPercent(DEFAULT_WORKSPACE_SPLIT_PERCENT);
          }}
        />

        <ModelBuilderPane
          variables={displayedVariables}
          assumptions={displayedAssumptions}
          nodes={displayedNodes}
          edges={displayedEdges}
          onMoveNode={moveModelNode}
          style={{
            flexGrow: 100 - workspaceSplitPercent,
            flexBasis: 0,
          }}
        />
        </div>
      </div>
    </div>
  );
}

export default App;
