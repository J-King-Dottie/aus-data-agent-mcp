import { Fragment, type CSSProperties, type Dispatch, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode, type SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  getBezierPath,
  useReactFlow,
  type EdgeProps,
  type NodeChange,
  type NodeProps,
  type Node as ReactFlowNode,
  type Edge as ReactFlowEdge,
} from "@xyflow/react";
import ReactECharts from "echarts-for-react";
import "@xyflow/react/dist/style.css";
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
  periodStart: string;
  periodEnd: string;
  transformSummary: string;
  nodeDescription?: string;
  contentsSummary?: string;
  contents?: Record<string, unknown>;
  validationStatus: "candidate" | "validated" | "rejected";
}

interface ModelNode {
  id: string;
  node_title: string;
  node_description: string;
  nodeType: "variable" | "calculation" | "result";
  variableId?: string;
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
  nodes?: Array<Partial<ModelNode>>;
  edges?: Array<Partial<ModelEdge> & { from?: string; to?: string }>;
  node_data?: Record<string, unknown>;
}

interface ModelBuilderState {
  variables: ValidatedVariable[];
  nodes: ModelNode[];
  edges: ModelEdge[];
  node_data: Record<string, unknown>;
}

interface ModelNodeSize {
  width: number;
  height: number;
}

interface ModelNodeRenderMeta {
  size: ModelNodeSize;
  titleLines: string[];
  noteLines: string[];
  note: string;
  noteSegments: Array<{ text: string; repeatedAssumption: boolean }>;
  expanded: boolean;
}

type ModelFlowNodeData = {
  modelNode: ModelNode;
  meta: ModelNodeRenderMeta;
  onToggleExpanded: (nodeId: string) => void;
  onOpenChart: (nodeId: string) => void;
  onHoverNode: (nodeId: string) => void;
  onLeaveNode: (nodeId: string) => void;
};

type ModelFlowEdgeData = {
  highlighted: boolean;
};

type ModelFlowNode = ReactFlowNode<ModelFlowNodeData, "modelNode">;
type ModelFlowEdge = ReactFlowEdge<ModelFlowEdgeData, "modelEdge">;

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
  "id,name,question,status,conversation_id,model_builder_state,model_graph_state,node_data,active_validated_variable_ids,updated_at";
const VALIDATED_VARIABLE_SELECT_COLUMNS =
  "id,name,label,source_name,metric,unit,geography,frequency,seasonal_treatment,period_start,period_end,transform_summary,node_description,contents_summary,validation_status,validated_data";
const VALIDATED_VARIABLE_LIBRARY_SELECT_COLUMNS =
  "id,name,label,source_name,metric,unit,geography,frequency,seasonal_treatment,period_start,period_end,transform_summary,node_description,contents_summary,validation_status";
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
  lineType?: "solid" | "dashed" | "dotted";
  opacity?: number;
  points: ChartPoint[];
}

interface ChartSpec {
  type?: ChartType;
  title?: string;
  xLabel?: string;
  yLabel?: string;
  series: ChartSeries[];
}

type ModelNodeChartKind = "saved" | "calculated";

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
  const includeZeroAxisMin = (value: { min: number; max: number }) => {
    if (value.min >= 0) {
      return 0;
    }
    return undefined;
  };
  const includeZeroAxisMax = (value: { min: number; max: number }) => {
    if (value.max <= 0) {
      return 0;
    }
    return undefined;
  };

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
          min: includeZeroAxisMin,
          max: includeZeroAxisMax,
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
          min: scatterNumericX ? includeZeroAxisMin : undefined,
          max: scatterNumericX ? includeZeroAxisMax : undefined,
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
          min: includeZeroAxisMin,
          max: includeZeroAxisMax,
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
          type: series.lineType || "solid",
          opacity: series.opacity ?? 1,
        },
        areaStyle: isAreaLike ? { opacity: isStacked ? 0.82 : 0.18 } : undefined,
        itemStyle: {
          borderRadius: isBarLike ? [4, 4, 0, 0] : 0,
          opacity: series.opacity ?? 1,
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
    if (prev.some((message) => message.sender === "progress" && message.content === content)) {
      return prev;
    }
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

function mergeProcessingMessages(
  currentMessages: ChatMessage[],
  backendMessages: ChatMessage[],
  assistantMessageId: string
) {
  const base = backendMessages.length ? [...backendMessages] : [...currentMessages];
  const progressContents = new Set(base.filter((message) => message.sender === "progress").map((message) => message.content));
  const localProgress = currentMessages.filter(
    (message) => message.sender === "progress" && !progressContents.has(message.content)
  );
  let merged = [...base];
  const assistantIndex = merged.findIndex((message) => message.sender === "assistant");
  const insertIndex = assistantIndex === -1 ? merged.length : assistantIndex;
  if (localProgress.length) {
    merged.splice(insertIndex, 0, ...localProgress);
  }
  if (!merged.some((message) => message.sender === "assistant")) {
    merged.push({
      id: assistantMessageId,
      sender: "assistant",
      content: "",
    });
  }
  return merged;
}

function latestProgressContent(messages: ChatMessage[]) {
  const progressMessages = messages.filter((message) => message.sender === "progress" && message.content.trim());
  return progressMessages.length ? progressMessages[progressMessages.length - 1].content : "";
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

function variablePeriodLabel(variable: Pick<ValidatedVariable, "periodStart" | "periodEnd">) {
  if (variable.periodStart && variable.periodEnd) {
    return variable.periodStart === variable.periodEnd
      ? variable.periodStart
      : `${variable.periodStart} to ${variable.periodEnd}`;
  }
  return variable.periodStart || variable.periodEnd || "";
}

function searchableVariableText(variable: ValidatedVariable) {
  return [
    variable.id,
    variable.name,
    variable.label,
    variable.metric,
    variable.sourceName,
    variable.geography,
    variable.frequency,
    variable.seasonalTreatment,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function meaningfulTokens(value: string) {
  const stopwords = new Set([
    "abs",
    "australia",
    "australian",
    "quarterly",
    "annual",
    "history",
    "path",
    "project",
    "projection",
    "to",
    "from",
    "the",
    "and",
    "of",
    "total",
  ]);
  return value
    .toLowerCase()
    .replace(/other-res/g, "other residential")
    .replace(/[^a-z0-9]+/g, " ")
    .split(/\s+/)
    .filter((token) => token.length > 2 && !stopwords.has(token));
}

function variableForModelNode(
  node: ModelNode,
  variables: ValidatedVariable[],
  variableMap?: Map<string, ValidatedVariable>
) {
  if (node.nodeType !== "variable") {
    return undefined;
  }
  const byId = variableMap || new Map(variables.map((variable) => [variable.id, variable]));
  const explicit = byId.get(node.variableId || "") || byId.get(node.id);
  if (explicit) {
    return explicit;
  }
  const nodeTokens = meaningfulTokens([node.node_title, node.tooltip, node.node_description].filter(Boolean).join(" "));
  if (!nodeTokens.length) {
    return undefined;
  }
  const scored = variables
    .map((variable) => {
      const variableText = searchableVariableText(variable);
      const score = nodeTokens.reduce((total, token) => total + (variableText.includes(token) ? 1 : 0), 0);
      return { variable, score };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score);
  if (!scored.length || (scored.length > 1 && scored[0].score === scored[1].score)) {
    return undefined;
  }
  return scored[0].variable;
}

function chartPointFromUnknown(value: unknown): ChartPoint | null {
  if (Array.isArray(value)) {
    const x = toText(value[0]) || String(value[0] ?? "");
    const y = Number(value[1]);
    return x && Number.isFinite(y) ? { x, y } : null;
  }
  const record = toRecord(value);
  if (!record) {
    return null;
  }
  const x = toText(record.x) || toText(record.period) || toText(record.TIME_PERIOD) || String(record.x ?? record.period ?? "");
  const rawY = record.y ?? record.value ?? record.OBS_VALUE;
  const y = Number(rawY);
  return x && Number.isFinite(y) ? { x, y } : null;
}

function pointsFromSavedCalculatedEntry(entry: Record<string, unknown>): ChartPoint[] {
  const points = Array.isArray(entry.points) ? entry.points : [];
  if (points.length) {
    return points.map(chartPointFromUnknown).filter((point): point is ChartPoint => Boolean(point));
  }
  const columns = Array.isArray(entry.columns) ? entry.columns.map((item) => toText(item)).filter(Boolean) : ["period", "value"];
  const records = Array.isArray(entry.records) ? entry.records : [];
  const periodIndex = Math.max(0, columns.findIndex((column) => /time|period|date|quarter|year/i.test(column)));
  const valueIndex = Math.max(0, columns.findIndex((column) => ["value", "y", "obs_value"].includes(column.toLowerCase())));
  return records
    .map((record) => {
      if (!Array.isArray(record)) {
        return chartPointFromUnknown(record);
      }
      const x = toText(record[periodIndex]) || String(record[periodIndex] ?? "");
      const y = Number(record[valueIndex]);
      return x && Number.isFinite(y) ? { x, y } : null;
    })
    .filter((point): point is ChartPoint => Boolean(point));
}

function seriesFromSavedNodeDataEntry(entry: Record<string, unknown>, fallbackTitle: string): ChartSeries[] {
  const series = Array.isArray(entry.series) ? entry.series : [];
  const parsedSeries = series
    .map((item, index): ChartSeries | null => {
      const record = toRecord(item);
      if (!record) {
        return null;
      }
      const points = Array.isArray(record.points)
        ? record.points.map(chartPointFromUnknown).filter((point): point is ChartPoint => Boolean(point))
        : [];
      if (!points.length) {
        return null;
      }
      return {
        name: toText(record.name) || toText(record.label) || `Series ${index + 1}`,
        color: [NISABA_THEME.rust, NISABA_THEME.umber, NISABA_THEME.secondaryGreen, NISABA_THEME.green][index % 4],
        points,
      };
    })
    .filter((item): item is ChartSeries => Boolean(item));
  if (parsedSeries.length) {
    return parsedSeries;
  }
  const points = pointsFromSavedCalculatedEntry(entry);
  return points.length
    ? [
        {
          name: toText(entry.node_title) || fallbackTitle,
          color: NISABA_THEME.rust,
          points,
        },
      ]
    : [];
}

function savedChartSpecForNode(
  node: ModelNode,
  node_data: Record<string, unknown>
): { chartSpec: ChartSpec; dataKind: ModelNodeChartKind; title: string } | null {
  const entry = toRecord(node_data[node.id]);
  if (!entry) {
    return null;
  }
  const title = toText(entry.node_title) || node.node_title;
  const series = seriesFromSavedNodeDataEntry(entry, title);
  if (!series.length) {
    return null;
  }
  const dataKind: ModelNodeChartKind = toText(entry.data_kind) === "saved" ? "saved" : "calculated";
  return {
    title,
    dataKind,
    chartSpec: {
      type: "line",
      title,
      xLabel: "Period",
      yLabel: toText(entry.unit) || "Value",
      series,
    },
  };
}

function createEmptyModelBuilderState(): ModelBuilderState {
  return { variables: [], nodes: [], edges: [], node_data: {} };
}

function parseModelBuilderState(value: unknown): ModelBuilderState {
  if (!value || typeof value !== "object") {
    return createEmptyModelBuilderState();
  }
  return normalizeModelSpec(value as ModelBuilderSpec) || createEmptyModelBuilderState();
}

function parseProjectModelBuilderState(row: Record<string, unknown>): ModelBuilderState {
  const legacyState = parseModelBuilderState(row.model_builder_state);
  const graphState = parseModelBuilderState(row.model_graph_state);
  const hasGraphColumn = Boolean(row.model_graph_state && typeof row.model_graph_state === "object");
  const nodes = hasGraphColumn ? graphState.nodes : legacyState.nodes;
  return {
    variables: legacyState.variables,
    nodes,
    edges: hasGraphColumn ? graphState.edges : legacyState.edges,
    node_data: toRecord(row.node_data) || {},
  };
}

function toModelGraphState(state: ModelBuilderState) {
  return {
    nodes: state.nodes,
    edges: state.edges,
  };
}

function modelStateWithoutVariable(state: ModelBuilderState, variableId: string): ModelBuilderState {
  const removedNodeIds = new Set(
    state.nodes
      .filter((node) => node.variableId === variableId || node.id === variableId)
      .map((node) => node.id)
  );
  const nextNodeData = Object.fromEntries(
    Object.entries(state.node_data || {}).filter(([nodeId]) => !removedNodeIds.has(nodeId))
  );
  return {
    variables: state.variables.filter((variable) => variable.id !== variableId),
    nodes: state.nodes.filter((node) => !removedNodeIds.has(node.id)),
    edges: state.edges.filter(
      (edge) => !removedNodeIds.has(edge.sourceNodeId) && !removedNodeIds.has(edge.targetNodeId)
    ),
    node_data: nextNodeData,
  };
}

function modelStateWithVariable(state: ModelBuilderState, variable: ValidatedVariable): ModelBuilderState {
  const variables = state.variables.some((item) => item.id === variable.id)
    ? state.variables
    : [...state.variables, variable];
  const hasNode = state.nodes.some((node) => node.variableId === variable.id || node.id === variable.id);
  if (hasNode) {
    return { ...state, variables };
  }
  const existingVariableNodes = state.nodes.filter((node) => node.nodeType === "variable").length;
  const node: ModelNode = {
    id: variable.id,
    node_title: variable.label || variable.name,
    node_description: variable.nodeDescription || "",
    nodeType: "variable",
    variableId: variable.id,
    positionX: 80 + (existingVariableNodes % 2) * 390,
    positionY: 80 + Math.floor(existingVariableNodes / 2) * 210,
  };
  return {
    ...state,
    variables,
    nodes: [...state.nodes, node],
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
    model_graph_state: toModelGraphState(draft.modelBuilderState),
    node_data: draft.modelBuilderState.node_data || {},
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
      model_graph_state: toModelGraphState(nextState),
      updated_at: new Date().toISOString(),
    })
    .eq("id", projectId);
}

function mapVariableRow(row: Record<string, unknown>): ValidatedVariable {
  const status = toText(row.validation_status);
  const validatedData = toRecord(row.validated_data);
  const contents = validatedData;
  const contentsSummary = toText(row.contentsSummary) || toText(row.contents_summary);
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
    periodStart: toText(row.period_start),
    periodEnd: toText(row.period_end),
    transformSummary: toText(row.transform_summary),
    nodeDescription:
      toText(row.nodeDescription) ||
      toText(row.node_description) ||
      toText(contents?.node_description) ||
      undefined,
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
      periodStart: toText(variable.periodStart) || toText((variable as Record<string, unknown>).period_start),
      periodEnd: toText(variable.periodEnd) || toText((variable as Record<string, unknown>).period_end),
      transformSummary: toText(variable.transformSummary),
      nodeDescription: toText((variable as Record<string, unknown>).nodeDescription) || toText((variable as Record<string, unknown>).node_description) || undefined,
      contentsSummary: toText(variable.contentsSummary) || undefined,
      contents: toRecord(variable.contents) || undefined,
      validationStatus: status === "candidate" || status === "rejected" ? status : "validated",
    };
  });

  const nodes = (spec.nodes || []).map((node, index): ModelNode => {
    const id = toText(node.id) || `node-${index + 1}`;
    const nodeType = toText(node.nodeType);
    const raw = node as Record<string, unknown>;
    return {
      id,
      node_title: toText(raw.node_title) || id,
      node_description: toText(raw.node_description),
      nodeType:
        nodeType === "calculation" || nodeType === "result"
          ? nodeType
          : "variable",
      variableId: toText(node.variableId) || undefined,
      expression: toText(node.expression) || undefined,
      method: toText(raw.method) || undefined,
      inputs: toTextArray(raw.inputs),
      output: toText(raw.output) || undefined,
      sourceCalculationId: toText(raw.sourceCalculationId) || toText(raw.source_calculation_id) || undefined,
      logicSummary: toText(raw.logicSummary) || toText(raw.logic_summary) || undefined,
      tooltip: toText(raw.tooltip) || undefined,
      parameters: toRecord(raw.parameters) || undefined,
      calculationLogic: toRecord(raw.calculationLogic) || toRecord(raw.calculation_logic) || undefined,
      calculationSpec: toRecord(raw.calculationSpec) || toRecord(raw.calculation_spec) || undefined,
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

  return {
    variables,
    nodes,
    edges,
    node_data: toRecord(spec.node_data) || {},
  };
}

function buildFallbackGraph(variables: ValidatedVariable[]): { nodes: ModelNode[]; edges: ModelEdge[] } {
  if (variables.length === 0) {
    return { nodes: [], edges: [] };
  }

  const inputNodes = variables.slice(0, 5).map((variable, index): ModelNode => ({
    id: variable.id,
    node_title: variable.name,
    node_description: variable.nodeDescription || "",
    nodeType: "variable",
    variableId: variable.id,
    positionX: 24,
    positionY: 28 + index * 72,
  }));
  const resultNode = {
    id: "model-result",
    node_title: "Result",
    node_description: "",
    nodeType: "calculation" as const,
    expression: variables.length > 1 ? "+" : "",
    inputs: inputNodes.map((node) => node.id),
    positionX: 360,
    positionY: 72,
  };
  return {
    nodes: [...inputNodes, resultNode],
    edges: inputNodes.map((node) => ({
      id: `${node.id}-${resultNode.id}`,
      sourceNodeId: node.id,
      targetNodeId: resultNode.id,
    })),
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
                    In Sumerian mythology, Nisaba was the goddess of writing,
                    accounting, and the keeping of records.
                  </p>
                  <p>
                    Writing emerged in Mesopotamian bureaucracies to count what
                    mattered. Grain, livestock, labour, taxes.
                  </p>
                  <p>
                    This system does the same. Designed for Australian analysts,
                    it combines detailed domestic data with global macro sources.
                  </p>
                  <p>
                    Nisaba is not a general chatbot. It is a data discovery,
                    retrieval, and analysis workflow. Use Claude or ChatGPT for
                    broad research. Use Nisaba when you want actual data, from
                    the actual source.
                  </p>
                  <p>
                    Produced by{" "}
                    <a href="https://dottieaistudio.com.au/" target="_blank" rel="noreferrer">
                      Dottie AI Studio
                    </a>
                    {" · "}
                    open source{" "}
                    <a
                      href="https://github.com/J-King-Dottie/aus-data-agent-mcp"
                      target="_blank"
                      rel="noreferrer"
                    >
                      J-King-Dottie/aus-data-agent-mcp
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

function modelNodeSize(node: ModelNode, renderMeta?: Map<string, ModelNodeRenderMeta>): ModelNodeSize {
  const metaSize = renderMeta?.get(node.id)?.size;
  if (metaSize) {
    return metaSize;
  }
  return { width: 260, height: 160 };
}

function modelNodeDynamicSize(titleLineCount: number, noteLineCount: number, expanded: boolean): ModelNodeSize {
  const verticalPadding = 12;
  const titleHeight = Math.max(1, titleLineCount) * 21;
  if (!expanded || noteLineCount === 0) {
    return { width: 260, height: Math.max(58, verticalPadding * 2 + titleHeight) };
  }
  const noteHeight = Math.max(1, noteLineCount) * 16;
  return { width: 260, height: Math.max(118, verticalPadding * 2 + titleHeight + 12 + noteHeight) };
}

function isCustomCalculationNode(node: ModelNode) {
  return node.nodeType === "calculation" && Boolean(node.method || node.expression === "custom");
}

function descriptionForNode(node: ModelNode, variables: ValidatedVariable[]) {
  if (node.node_description) {
    return node.node_description;
  }
  const variable = variableForModelNode(node, variables);
  return variable?.nodeDescription || "";
}

function wrapNoteLines(value: string, maxChars: number, maxLines?: number) {
  const words = value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .flatMap((word) => {
      if (word.length <= maxChars) {
        return [word];
      }
      const chunks: string[] = [];
      for (let index = 0; index < word.length; index += maxChars) {
        chunks.push(word.slice(index, index + maxChars));
      }
      return chunks;
    });
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= maxChars) {
      current = next;
      continue;
    }
    if (current) {
      lines.push(current);
    }
    current = word;
    if (maxLines && lines.length >= maxLines) {
      break;
    }
  }
  if (current && (!maxLines || lines.length < maxLines)) {
    lines.push(current);
  }
  if (maxLines && lines.length === maxLines && words.join(" ").length > lines.join(" ").length) {
    lines[maxLines - 1] = truncateNodeLabel(lines[maxLines - 1], maxChars);
  }
  return lines;
}

function truncateNodeLabel(value: string, maxLength = 34) {
  const text = value.trim();
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1).trim()}…`;
}

function noteSentences(value: string) {
  return value
    .replace(/\s+/g, " ")
    .trim()
    .match(/[^.!?]+[.!?]+|[^.!?]+$/g)
    ?.map((sentence) => sentence.trim())
    .filter(Boolean) || [];
}

function repeatedAssumptionSentences(notes: string[]) {
  const counts = new Map<string, number>();
  notes.forEach((note) => {
    const seenInNote = new Set<string>();
    noteSentences(note).forEach((sentence) => {
      const normalized = sentence.toLowerCase();
      if (!/\bassumes?\b/.test(normalized) || seenInNote.has(normalized)) {
        return;
      }
      seenInNote.add(normalized);
      counts.set(normalized, (counts.get(normalized) || 0) + 1);
    });
  });
  return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([sentence]) => sentence));
}

function noteSegments(value: string, repeatedAssumptions: Set<string>) {
  const sentences = noteSentences(value);
  if (!sentences.length) {
    return [{ text: value, repeatedAssumption: false }];
  }
  return sentences.map((sentence, index) => ({
    text: `${index > 0 ? " " : ""}${sentence}`,
    repeatedAssumption: repeatedAssumptions.has(sentence.toLowerCase()),
  }));
}

function positionedNodes(nodes: ModelNode[]): ModelNode[] {
  return nodes.map((node, index) => ({
    ...node,
    positionX: Number.isFinite(Number(node.positionX)) ? Number(node.positionX) : 72 + (index % 2) * 280,
    positionY: Number.isFinite(Number(node.positionY)) ? Number(node.positionY) : 32 + Math.floor(index / 2) * 168,
  }));
}

function layoutModelNodes(
  nodes: ModelNode[],
  edges: ModelEdge[],
  renderMeta?: Map<string, ModelNodeRenderMeta>
): ModelNode[] {
  const baseNodes = positionedNodes(nodes);
  const nodeMap = new Map(baseNodes.map((node) => [node.id, node]));
  const incoming = new Map<string, ModelEdge[]>();
  const outgoing = new Map<string, ModelEdge[]>();
  const indegree = new Map(baseNodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    if (!nodeMap.has(edge.sourceNodeId) || !nodeMap.has(edge.targetNodeId)) {
      return;
    }
    incoming.set(edge.targetNodeId, [...(incoming.get(edge.targetNodeId) || []), edge]);
    outgoing.set(edge.sourceNodeId, [...(outgoing.get(edge.sourceNodeId) || []), edge]);
    indegree.set(edge.targetNodeId, (indegree.get(edge.targetNodeId) || 0) + 1);
  });

  const rank = new Map(baseNodes.map((node) => [node.id, 0]));
  const queue = baseNodes.filter((node) => (indegree.get(node.id) || 0) === 0);
  const visited = new Set<string>();
  while (queue.length) {
    const node = queue.shift();
    if (!node || visited.has(node.id)) {
      continue;
    }
    visited.add(node.id);
    (outgoing.get(node.id) || []).forEach((edge) => {
      rank.set(edge.targetNodeId, Math.max(rank.get(edge.targetNodeId) || 0, (rank.get(node.id) || 0) + 1));
      const nextIndegree = (indegree.get(edge.targetNodeId) || 0) - 1;
      indegree.set(edge.targetNodeId, nextIndegree);
      if (nextIndegree === 0) {
        const target = nodeMap.get(edge.targetNodeId);
        if (target) {
          queue.push(target);
        }
      }
    });
  }

  baseNodes.forEach((node, index) => {
    if (!visited.has(node.id)) {
      rank.set(node.id, Math.max(rank.get(node.id) || 0, Math.floor(index / 2)));
    }
  });

  const ranks = new Map<number, ModelNode[]>();
  baseNodes.forEach((node) => {
    const nodeRank = rank.get(node.id) || 0;
    ranks.set(nodeRank, [...(ranks.get(nodeRank) || []), node]);
  });

  const rowGap = 92;
  const columnGap = 340;
  const top = 40;
  const centerX = 430;
  const rankHeights = new Map<number, number>();
  ranks.forEach((rankNodes, nodeRank) => {
    rankHeights.set(
      nodeRank,
      Math.max(...rankNodes.map((node) => modelNodeSize(node, renderMeta).height))
    );
  });
  const rankY = new Map<number, number>();
  [...ranks.keys()]
    .sort((a, b) => a - b)
    .forEach((nodeRank, index, sortedRanks) => {
      if (index === 0) {
        rankY.set(nodeRank, top);
        return;
      }
      const previousRank = sortedRanks[index - 1];
      rankY.set(nodeRank, (rankY.get(previousRank) || top) + (rankHeights.get(previousRank) || 0) + rowGap);
    });
  const positioned = new Map<string, ModelNode>();
  [...ranks.entries()]
    .sort(([a], [b]) => a - b)
    .forEach(([nodeRank, rankNodes]) => {
      const orderedNodes = [...rankNodes].sort((a, b) => {
        const typeOrder = { variable: 0, calculation: 1, result: 2 };
        return typeOrder[a.nodeType] - typeOrder[b.nodeType] || (a.positionX ?? 0) - (b.positionX ?? 0);
      });
      const rowWidth = (orderedNodes.length - 1) * columnGap;
      orderedNodes.forEach((node, index) => {
        const size = modelNodeSize(node, renderMeta);
        positioned.set(node.id, {
          ...node,
          positionX: Math.round(centerX - rowWidth / 2 + index * columnGap - size.width / 2),
          positionY: rankY.get(nodeRank) || top,
        });
      });
    });

  return baseNodes.map((node) => positioned.get(node.id) || node);
}

function visibleModelGraph(nodes: ModelNode[], edges: ModelEdge[]) {
  const allNodes = positionedNodes(nodes);
  const visibleIds = new Set(allNodes.map((node) => node.id));

  return {
    nodes: allNodes,
    edges: edges.filter((edge) => visibleIds.has(edge.sourceNodeId) && visibleIds.has(edge.targetNodeId)),
  };
}

function reactFlowHandleForSide(side: "left" | "right" | "top" | "bottom") {
  if (side === "left") {
    return Position.Left;
  }
  if (side === "right") {
    return Position.Right;
  }
  if (side === "top") {
    return Position.Top;
  }
  return Position.Bottom;
}

function handlePairForModelEdge(source: ModelNode, target: ModelNode) {
  const dy = (target.positionY ?? 0) - (source.positionY ?? 0);
  return dy >= 0
    ? { sourceHandle: "bottom", targetHandle: "top" }
    : { sourceHandle: "top", targetHandle: "bottom" };
}

function modelFlowNodeClass(node: ModelNode, meta: ModelNodeRenderMeta) {
  return `model-node model-node-${node.nodeType}${isCustomCalculationNode(node) ? " model-node-custom-calculation" : ""}${meta.note ? " model-node-collapsible" : ""}${meta.expanded ? " expanded" : " collapsed"}`;
}

function ModelFlowNodeView({ data }: NodeProps<ModelFlowNode>) {
  const { modelNode: node, meta, onToggleExpanded, onOpenChart, onHoverNode, onLeaveNode } = data;
  const handleSides: Array<"top" | "right" | "bottom" | "left"> = ["top", "right", "bottom", "left"];
  return (
    <div
      className={modelFlowNodeClass(node, meta)}
      style={{ width: meta.size.width, height: meta.size.height }}
      role={meta.note ? "button" : undefined}
      tabIndex={0}
      aria-expanded={meta.note ? meta.expanded : undefined}
      onClick={(event) => {
        event.stopPropagation();
        onToggleExpanded(node.id);
      }}
      onKeyDown={(event) => {
        if ((event.key === "Enter" || event.key === " ") && meta.note) {
          event.preventDefault();
          onToggleExpanded(node.id);
        }
      }}
      onMouseEnter={() => onHoverNode(node.id)}
      onMouseLeave={() => onLeaveNode(node.id)}
    >
      {handleSides.map((side) => (
        <Fragment key={side}>
          <Handle
            id={side}
            type="source"
            position={reactFlowHandleForSide(side)}
            className={`model-node-handle model-node-handle-${side}`}
          />
          <Handle
            id={side}
            type="target"
            position={reactFlowHandleForSide(side)}
            className={`model-node-handle model-node-handle-${side}`}
          />
        </Fragment>
      ))}
      <div className="model-node-card-text">
        <div className="model-node-card-title">
          <span>{node.node_title}</span>
          <button
            type="button"
            className="model-node-chart-button"
            aria-label={`Show chart for ${node.node_title}`}
            title="Show chart preview"
            onPointerDown={(event) => {
              event.stopPropagation();
            }}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onOpenChart(node.id);
            }}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 19h16" />
              <path d="M7 16V9" />
              <path d="M12 16V5" />
              <path d="M17 16v-4" />
            </svg>
          </button>
        </div>
        {meta.expanded && meta.note ? (
          <div className="model-node-card-note">
            {meta.noteSegments.map((segment, index) => (
              <span
                key={`${node.id}-note-${index}`}
                className={segment.repeatedAssumption ? "model-node-repeated-assumption" : undefined}
              >
                {segment.text}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ModelFlowEdgeView({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps<ModelFlowEdge>) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.22,
  });
  return (
    <BaseEdge
      id={id}
      path={path}
      className={`model-edge-main${data?.highlighted ? " highlighted" : ""}`}
    />
  );
}

const MODEL_FLOW_NODE_TYPES = { modelNode: ModelFlowNodeView };
const MODEL_FLOW_EDGE_TYPES = { modelEdge: ModelFlowEdgeView };

function fitModelFlowView(
  fitView: (options: { padding: number; duration: number; minZoom: number; maxZoom: number }) => void | Promise<boolean>
) {
  window.setTimeout(() => {
    fitView({ padding: 0.22, duration: 260, minZoom: 0.18, maxZoom: 1.15 });
  }, 0);
}

function buildModelRenderMeta(
  graphNodes: ModelNode[],
  variables: ValidatedVariable[],
  collapsedNodeIds: Record<string, boolean>
) {
  const notesByNode = new Map(graphNodes.map((node) => [node.id, descriptionForNode(node, variables)]));
  const repeatedAssumptions = repeatedAssumptionSentences([...notesByNode.values()]);
  return new Map<string, ModelNodeRenderMeta>(
    graphNodes.map((node) => {
      const note = notesByNode.get(node.id) || "";
      const titleLines = wrapNoteLines(node.node_title, 28, 2);
      const expanded = Boolean(note) && !collapsedNodeIds[node.id];
      const noteLines = expanded ? wrapNoteLines(note, 42) : [];
      return [
        node.id,
        {
          size: modelNodeDynamicSize(titleLines.length || 1, noteLines.length, expanded),
          titleLines,
          noteLines,
          note,
          noteSegments: noteSegments(note, repeatedAssumptions),
          expanded,
        },
      ];
    })
  );
}

function ModelNodeChartModal({
  node,
  node_data,
  onClose,
}: {
  node: ModelNode;
  node_data: Record<string, unknown>;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const savedChart = savedChartSpecForNode(node, node_data);
  const chartSpec = savedChart?.chartSpec || null;
  const dataKind = savedChart?.dataKind || (node.nodeType === "variable" ? "saved" : "calculated");
  const title = savedChart?.title || node.node_title;

  return (
    <div className="variable-chart-overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="variable-chart-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`${title} chart`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="variable-chart-modal-header">
          <div>
            <h3>{title}</h3>
            <p>
              <span className={`variable-chart-kind variable-chart-kind-${dataKind}`}>
                {dataKind === "saved" ? "Saved source data" : "Calculated preview"}
              </span>
            </p>
          </div>
          <button type="button" className="variable-chart-close" aria-label="Close chart" onClick={onClose}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        {chartSpec ? (
          <div>
            <ChartBlock spec={chartSpec} />
          </div>
        ) : (
          <div className="variable-chart-empty">No saved chart data found for this node.</div>
        )}
        <div className="variable-chart-footer">
          {dataKind === "saved"
            ? "Saved data · not refreshed from source"
            : "Saved calculated data · refresh only from chat when requested"}
        </div>
      </section>
    </div>
  );
}

function ModelFlowDiagram(props: {
  nodes: ModelNode[];
  edges: ModelEdge[];
  variables: ValidatedVariable[];
  node_data: Record<string, unknown>;
  onMoveNode: (nodeId: string, position: { x: number; y: number }) => void;
  onLayoutNodes: (nodes: ModelNode[]) => void;
}) {
  return (
    <ReactFlowProvider>
      <ModelFlowDiagramInner {...props} />
    </ReactFlowProvider>
  );
}

function ModelFlowDiagramInner({
  nodes,
  edges,
  variables,
  node_data,
  onMoveNode,
  onLayoutNodes,
}: {
  nodes: ModelNode[];
  edges: ModelEdge[];
  variables: ValidatedVariable[];
  node_data: Record<string, unknown>;
  onMoveNode: (nodeId: string, position: { x: number; y: number }) => void;
  onLayoutNodes: (nodes: ModelNode[]) => void;
}) {
  const [collapsedNodeIds, setCollapsedNodeIds] = useState<Record<string, boolean>>({});
  const [chartNodeId, setChartNodeId] = useState("");
  const [hoveredNodeId, setHoveredNodeId] = useState("");
  const [flowNodes, setFlowNodes] = useState<ModelFlowNode[]>([]);
  const lastCenteredGraphRef = useRef("");
  const { fitView } = useReactFlow<ModelFlowNode, ModelFlowEdge>();
  const graph = useMemo(() => visibleModelGraph(nodes, edges), [nodes, edges]);
  const graphNodes = graph.nodes;
  const graphEdges = graph.edges;
  const nodeMap = useMemo(() => new Map(graphNodes.map((node) => [node.id, node])), [graphNodes]);
  const chartNode = chartNodeId ? nodeMap.get(chartNodeId) : undefined;
  const renderMeta = useMemo(
    () => buildModelRenderMeta(graphNodes, variables, collapsedNodeIds),
    [collapsedNodeIds, graphNodes, variables]
  );
  const graphViewportKey = graphNodes
    .map((node) => node.id)
    .join("|")
    + "::"
    + graphEdges.map((edge) => `${edge.sourceNodeId}->${edge.targetNodeId}`).join("|");
  const nodeLayoutKey = graphNodes
    .map((node) => `${node.id}:${node.positionX ?? 0}:${node.positionY ?? 0}:${renderMeta.get(node.id)?.expanded ? "e" : "c"}`)
    .join("|");

  const toggleNodeExpanded = useCallback((nodeId: string) => {
    const meta = renderMeta.get(nodeId);
    if (!meta?.note) {
      return;
    }
    setCollapsedNodeIds((prev) => ({
      ...prev,
      [nodeId]: !prev[nodeId],
    }));
  }, [renderMeta]);

  const openNodeChart = useCallback((nodeId: string) => {
    setChartNodeId(nodeId);
  }, []);

  const leaveNode = useCallback((nodeId: string) => {
    setHoveredNodeId((current) => current === nodeId ? "" : current);
  }, []);

  const modelNodeTypes = useMemo(() => MODEL_FLOW_NODE_TYPES, []);
  const modelEdgeTypes = useMemo(() => MODEL_FLOW_EDGE_TYPES, []);

  const nextFlowNodes = useMemo<ModelFlowNode[]>(() => graphNodes.map((node) => {
      const meta = renderMeta.get(node.id) || {
        size: modelNodeDynamicSize(1, 0, false),
        titleLines: [node.node_title],
        noteLines: [],
        note: "",
        noteSegments: [],
        expanded: false,
      };
    return {
      id: node.id,
      type: "modelNode",
      position: { x: node.positionX ?? 0, y: node.positionY ?? 0 },
      data: {
        modelNode: node,
        meta,
        onToggleExpanded: toggleNodeExpanded,
        onOpenChart: openNodeChart,
        onHoverNode: setHoveredNodeId,
        onLeaveNode: leaveNode,
      },
      draggable: true,
      selectable: false,
      style: { width: meta.size.width, height: meta.size.height },
    };
  }), [graphNodes, leaveNode, openNodeChart, renderMeta, toggleNodeExpanded]);

  const flowEdges = useMemo<ModelFlowEdge[]>(() => graphEdges.flatMap((edge) => {
    const source = nodeMap.get(edge.sourceNodeId);
    const target = nodeMap.get(edge.targetNodeId);
    if (!source || !target) {
      return [];
    }
    const handles = handlePairForModelEdge(source, target);
    const highlighted = Boolean(hoveredNodeId && (edge.sourceNodeId === hoveredNodeId || edge.targetNodeId === hoveredNodeId));
    return [{
      id: edge.id,
      type: "modelEdge",
      source: edge.sourceNodeId,
      target: edge.targetNodeId,
      sourceHandle: handles.sourceHandle,
      targetHandle: handles.targetHandle,
      data: { highlighted },
      selectable: false,
    }];
  }), [graphEdges, hoveredNodeId, nodeMap, renderMeta]);

  useEffect(() => {
    setFlowNodes(nextFlowNodes);
    if (lastCenteredGraphRef.current !== graphViewportKey) {
      lastCenteredGraphRef.current = graphViewportKey;
      fitModelFlowView(fitView);
    }
  }, [fitView, graphViewportKey, nodeLayoutKey, nextFlowNodes]);

  const onFlowNodesChange = useCallback((changes: NodeChange<ModelFlowNode>[]) => {
    setFlowNodes((current) => applyNodeChanges(changes, current) as ModelFlowNode[]);
  }, []);

  const onNodeDragStop = useCallback((_event: unknown, node: ModelFlowNode) => {
    const modelNode = nodeMap.get(node.id);
    const size = modelNode ? modelNodeSize(modelNode, renderMeta) : { width: 260, height: 160 };
    const nextX = Math.round(node.position.x / 20) * 20;
    const nextY = Math.round(node.position.y / 20) * 20;
    onMoveNode(node.id, {
      x: Math.max(-2200, Math.min(4200 - size.width, nextX)),
      y: Math.max(-2200, Math.min(5200 - size.height, nextY)),
    });
  }, [nodeMap, onMoveNode, renderMeta]);

  const expandAllNodes = () => {
    const nextCollapsed = {};
    const nextMeta = buildModelRenderMeta(graphNodes, variables, nextCollapsed);
    const nextNodes = layoutModelNodes(graphNodes, graphEdges, nextMeta);
    setCollapsedNodeIds({});
    onLayoutNodes(nextNodes);
    fitModelFlowView(fitView);
  };

  const collapseAllNodes = () => {
    const nextCollapsed = Object.fromEntries(
      graphNodes
        .filter((node) => Boolean(renderMeta.get(node.id)?.note))
        .map((node) => [node.id, true])
    );
    const nextMeta = buildModelRenderMeta(graphNodes, variables, nextCollapsed);
    const nextNodes = layoutModelNodes(graphNodes, graphEdges, nextMeta);
    setCollapsedNodeIds(nextCollapsed);
    onLayoutNodes(nextNodes);
    fitModelFlowView(fitView);
  };

  return (
    <>
      {graphNodes.length ? (
        <div
          className="model-flow-toolbar"
          aria-label="Canvas controls"
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <button type="button" className="model-flow-tool-button" aria-label="Expand all notes" onClick={expandAllNodes}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5" />
              <path d="M9 9 3.8 3.8M15 9l5.2-5.2M9 15l-5.2 5.2M15 15l5.2 5.2" />
            </svg>
          </button>
          <button type="button" className="model-flow-tool-button" aria-label="Collapse all notes" onClick={collapseAllNodes}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5" />
              <path d="M8 8 3.8 3.8M16 8l4.2-4.2M8 16l-4.2 4.2M16 16l4.2 4.2" />
            </svg>
          </button>
        </div>
      ) : null}
      {!graphNodes.length ? (
        <div className="model-flow-empty" aria-live="polite">
          <p>
            <span>Describe the model you want to build.</span>
            <span>Nisaba will find and validate data.</span>
            <span>Let your ideas take shape.</span>
          </p>
        </div>
      ) : null}
      <ReactFlow
        className="model-flow"
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={modelNodeTypes}
        edgeTypes={modelEdgeTypes}
        onNodesChange={onFlowNodesChange}
        onNodeDragStop={onNodeDragStop}
        minZoom={0.18}
        maxZoom={3.2}
        defaultEdgeOptions={{ type: "modelEdge" }}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        fitView
        fitViewOptions={{ padding: 0.22, minZoom: 0.18, maxZoom: 1.15 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Lines} gap={20} size={0.8} className="model-flow-background" />
      </ReactFlow>
    {chartNode ? (
      <ModelNodeChartModal
        node={chartNode}
        node_data={node_data}
        onClose={() => setChartNodeId("")}
      />
    ) : null}
    </>
  );
}

function ModelBuilderPane({
  globalVariables,
  activeVariableIds,
  variables,
  nodes,
  edges,
  node_data,
  style,
  onAddVariable,
  onRemoveVariable,
  onDeleteVariable,
  onMoveNode,
  onLayoutNodes,
}: {
  globalVariables: ValidatedVariable[];
  activeVariableIds: string[];
  variables: ValidatedVariable[];
  nodes: ModelNode[];
  edges: ModelEdge[];
  node_data: Record<string, unknown>;
  style?: CSSProperties;
  onAddVariable: (variableId: string) => void;
  onRemoveVariable: (variableId: string) => void;
  onDeleteVariable: (variableId: string) => void;
  onMoveNode: (nodeId: string, position: { x: number; y: number }) => void;
  onLayoutNodes: (nodes: ModelNode[]) => void;
}) {
  const [libraryOpen, setLibraryOpen] = useState(false);
  const activeIds = new Set(activeVariableIds);
  return (
    <aside className="workspace-pane model-pane" style={style} aria-label="Model Builder">
      <div className="model-flow-shell">
        <div
          className="variable-library"
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            className="variable-library-toggle"
            aria-expanded={libraryOpen}
            onClick={() => setLibraryOpen((open) => !open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 5.5h16M4 12h16M4 18.5h16" />
              <path d="M7 3.5v4M12 10v4M17 16.5v4" />
            </svg>
            <span>Variables</span>
            <small>{activeIds.size}/{globalVariables.length}</small>
          </button>
          {libraryOpen ? (
            <div className="variable-library-panel" role="dialog" aria-label="Variable library">
              <div className="variable-library-header">
                <span>Variable library</span>
                <small>{globalVariables.length} saved</small>
              </div>
              <div className="variable-library-list">
                {globalVariables.length ? (
                  globalVariables.map((variable) => {
                    const isActive = activeIds.has(variable.id);
                    const meta = [
                      variable.sourceName,
                      variable.geography,
                      variable.frequency,
                      variable.unit,
                      variablePeriodLabel(variable),
                    ].filter(Boolean).join(" · ");
                    return (
                      <div key={variable.id} className={`variable-library-row${isActive ? " active" : ""}`}>
                        <div className="variable-library-copy">
                          <div className="variable-library-title">
                            <span>{variable.label || variable.name}</span>
                            {isActive ? <span className="variable-library-check">Active</span> : null}
                          </div>
                          {meta ? <small>{meta}</small> : null}
                        </div>
                        <div className="variable-library-actions">
                          <button
                            type="button"
                            onClick={() => (isActive ? onRemoveVariable(variable.id) : onAddVariable(variable.id))}
                          >
                            {isActive ? "Remove" : "Add"}
                          </button>
                          <button
                            type="button"
                            className="variable-library-delete"
                            aria-label={`Delete ${variable.label || variable.name}`}
                            onClick={() => onDeleteVariable(variable.id)}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M5 7h14M10 11v6M14 11v6M9 7l1-2h4l1 2M7 7l1 13h8l1-13" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <p className="variable-library-empty">No validated variables saved yet.</p>
                )}
              </div>
            </div>
          ) : null}
        </div>
          <ModelFlowDiagram
            nodes={nodes}
            edges={edges}
            variables={variables}
            node_data={node_data}
            onMoveNode={onMoveNode}
            onLayoutNodes={onLayoutNodes}
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
  const [globalValidatedVariables, setGlobalValidatedVariables] = useState<ValidatedVariable[]>([]);
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
  const fallbackGraph = buildFallbackGraph(displayedVariables);
  const displayedNodes = modelBuilderState.nodes.length
    ? modelBuilderState.nodes
    : fallbackGraph.nodes;
  const displayedEdges = modelBuilderState.edges.length
    ? modelBuilderState.edges
    : fallbackGraph.edges;
  const displayedNodeData = modelBuilderState.node_data || {};

  const persistProjectVariableState = (
    projectId: string,
    activeIds: string[],
    nextState: ModelBuilderState
  ) => {
    return supabase
      .from("modelling_projects")
      .update({
        active_validated_variable_ids: activeIds,
        model_builder_state: nextState,
        model_graph_state: toModelGraphState(nextState),
        updated_at: new Date().toISOString(),
      })
      .eq("id", projectId);
  };

  const applyCurrentProjectVariableState = (activeIds: string[], nextState: ModelBuilderState) => {
    setModelBuilderState(nextState);
    setValidatedVariables(nextState.variables);
    setProjects((prev) =>
      prev.map((project) =>
        project.id === activeProjectId
          ? {
              ...project,
              activeValidatedVariableIds: activeIds,
              modelBuilderState: nextState,
              updatedAt: new Date().toISOString(),
            }
          : project
      )
    );
    void persistProjectVariableState(activeProjectId, activeIds, nextState).then(({ error: projectError }) => {
      if (projectError) {
        console.error("Failed to update project variables", projectError);
        setError(projectError.message);
      }
    });
  };

  const addVariableToCurrentProject = (variableId: string) => {
    const variable = globalValidatedVariables.find((item) => item.id === variableId);
    const activeProject = projects.find((project) => project.id === activeProjectId);
    const currentActiveIds = Array.from(
      new Set([...(activeProject?.activeValidatedVariableIds || []), ...displayedVariables.map((item) => item.id)])
    );
    if (!variable || !activeProject || currentActiveIds.includes(variableId)) {
      return;
    }
    const baseState: ModelBuilderState = {
      variables: displayedVariables,
      nodes: modelBuilderState.nodes.length ? displayedNodes : [],
      edges: modelBuilderState.edges.length ? displayedEdges : [],
      node_data: displayedNodeData,
    };
    const nextState = modelStateWithVariable(baseState, variable);
    applyCurrentProjectVariableState([...currentActiveIds, variableId], nextState);
  };

  const removeVariableFromCurrentProject = (variableId: string) => {
    const activeProject = projects.find((project) => project.id === activeProjectId);
    const currentActiveIds = Array.from(
      new Set([...(activeProject?.activeValidatedVariableIds || []), ...displayedVariables.map((item) => item.id)])
    );
    if (!activeProject || !currentActiveIds.includes(variableId)) {
      return;
    }
    const baseState: ModelBuilderState = {
      variables: displayedVariables,
      nodes: modelBuilderState.nodes.length ? displayedNodes : [],
      edges: modelBuilderState.edges.length ? displayedEdges : [],
      node_data: displayedNodeData,
    };
    const nextState = modelStateWithoutVariable(baseState, variableId);
    const activeIds = currentActiveIds.filter((id) => id !== variableId);
    applyCurrentProjectVariableState(activeIds, nextState);
  };

  const deleteGlobalVariable = async (variableId: string) => {
    const variable = globalValidatedVariables.find((item) => item.id === variableId);
    if (!variable) {
      return;
    }
    const label = variable.label || variable.name;
    const affectedProjects = projects.filter((project) => project.activeValidatedVariableIds.includes(variableId));
    const affectedProjectNames = affectedProjects.map((project) => project.name || "Untitled project").join(", ");
    const affectedWarning = affectedProjects.length
      ? `\n\nIt is active in ${affectedProjects.length} project${affectedProjects.length === 1 ? "" : "s"}: ${affectedProjectNames}.`
      : "";
    if (!window.confirm(`Delete "${label}" from the global variable library? This removes it from projects that use it.${affectedWarning}`)) {
      return;
    }
    const nextProjects = projects.map((project) => {
      if (!project.activeValidatedVariableIds.includes(variableId)) {
        return project;
      }
      return {
        ...project,
        activeValidatedVariableIds: project.activeValidatedVariableIds.filter((id) => id !== variableId),
        modelBuilderState: modelStateWithoutVariable(project.modelBuilderState, variableId),
        updatedAt: new Date().toISOString(),
      };
    });
    const { error: deleteError } = await supabase
      .from("validated_variables")
      .delete()
      .eq("id", variableId);
    if (deleteError) {
      console.error("Failed to delete validated variable", deleteError);
      setError(deleteError.message);
      return;
    }
    await Promise.all(
      affectedProjects.map((project) => {
        const nextProject = nextProjects.find((item) => item.id === project.id);
        if (!nextProject) {
          return Promise.resolve();
        }
        return persistProjectVariableState(
          nextProject.id,
          nextProject.activeValidatedVariableIds,
          nextProject.modelBuilderState
        );
      })
    );
    setGlobalValidatedVariables((prev) => prev.filter((item) => item.id !== variableId));
    setProjects(nextProjects);
    const activeProject = nextProjects.find((project) => project.id === activeProjectId);
    if (activeProject) {
      setModelBuilderState(activeProject.modelBuilderState);
      setValidatedVariables(activeProject.modelBuilderState.variables);
    }
  };

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
      nodes: displayedNodes,
      edges: displayedEdges,
      node_data: displayedNodeData,
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

  const layoutModelBuilderNodes = (layoutNodes: ModelNode[]) => {
    const nextState: ModelBuilderState = {
      variables: displayedVariables,
      nodes: layoutNodes,
      edges: displayedEdges,
      node_data: displayedNodeData,
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
    if (!authReady || !session) {
      setGlobalValidatedVariables([]);
      return;
    }

    let active = true;
    void supabase
      .from("validated_variables")
      .select(VALIDATED_VARIABLE_LIBRARY_SELECT_COLUMNS)
      .eq("validation_status", "validated")
      .order("updated_at", { ascending: false })
      .then(({ data, error: variableError }) => {
        if (!active) {
          return;
        }
        if (variableError) {
          console.error("Failed to load global validated variables", variableError);
          setError(variableError.message);
          setGlobalValidatedVariables([]);
          return;
        }
        setGlobalValidatedVariables(
          Array.isArray(data) ? data.map((row) => mapVariableRow(row as Record<string, unknown>)) : []
        );
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
        .select(VALIDATED_VARIABLE_SELECT_COLUMNS)
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
          setMessages((prev) => {
            const mergedMessages = mergeProcessingMessages(prev, backendMessages, assistantMessageId);
            const latestProgress = latestProgressContent(mergedMessages);
            if (latestProgress) {
              lastProgressRef.current = latestProgress;
            }
            return mergedMessages;
          });
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
    let polling = false;
    let pollTimer: number | undefined;
    const assistantMessageId = pendingRef.current.id;
    const taskId = activeRunTaskId;

    const pollOnce = async () => {
      if (cancelled || polling) {
        return;
      }
      polling = true;
      let shouldContinuePolling = true;
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
          shouldContinuePolling = false;
          const storedMessages = await loadStoredChatHistory(activeProjectId);
          if (storedMessages.length) {
            setMessages(storedMessages);
          } else {
            applyCompletedTaskSnapshot(payload, setMessages, assistantMessageId);
          }
          void supabase
            .from("validated_variables")
            .select(VALIDATED_VARIABLE_LIBRARY_SELECT_COLUMNS)
            .eq("validation_status", "validated")
            .order("updated_at", { ascending: false })
            .then(({ data, error: variableError }) => {
              if (variableError) {
                console.error("Failed to refresh global validated variables", variableError);
                return;
              }
              setGlobalValidatedVariables(
                Array.isArray(data) ? data.map((row) => mapVariableRow(row as Record<string, unknown>)) : []
              );
            });
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
          shouldContinuePolling = false;
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
        shouldContinuePolling = false;
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
      } finally {
        polling = false;
        if (shouldContinuePolling && !cancelled) {
          pollTimer = window.setTimeout(() => {
            void pollOnce();
          }, 1500);
        }
      }
    };

    void pollOnce();

    return () => {
      cancelled = true;
      if (pollTimer !== undefined) {
        window.clearTimeout(pollTimer);
      }
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
          globalVariables={globalValidatedVariables}
          activeVariableIds={Array.from(
            new Set([
              ...(projects.find((project) => project.id === activeProjectId)?.activeValidatedVariableIds || []),
              ...displayedVariables.map((variable) => variable.id),
            ])
          )}
          variables={displayedVariables}
          nodes={displayedNodes}
          edges={displayedEdges}
          node_data={displayedNodeData}
          onAddVariable={addVariableToCurrentProject}
          onRemoveVariable={removeVariableFromCurrentProject}
          onDeleteVariable={deleteGlobalVariable}
          onMoveNode={moveModelNode}
          onLayoutNodes={layoutModelBuilderNodes}
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
