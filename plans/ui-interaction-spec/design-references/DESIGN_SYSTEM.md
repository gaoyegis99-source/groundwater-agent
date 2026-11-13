# Groundwater Agent UI Design System v1.0

## Product character
A professional groundwater research workspace: calm, precise, evidence-oriented, and agent-led. It should feel like scientific software, not a GIS control room, generic admin dashboard, or decorative AI landing page.

## Visual tokens
| Token | Value | Usage |
|---|---|---|
| Brand / primary | `#2563EB` | Primary actions, active navigation, links |
| Brand deep | `#163B73` | Top-level brand/header accents, strong headings |
| Accent cyan | `#0EA5E9` | Secondary emphasis, data/knowledge highlights |
| Success | `#10B981` | Verified, completed, published |
| Warning | `#F59E0B` | Waiting, needs review, partial limitation |
| Danger | `#EF4444` | Failed, rejected, invalid |
| App background | `#F5F8FC` | Main canvas |
| Surface | `#FFFFFF` | Cards, dialogs, panels |
| Surface subtle | `#F0F5FB` | Secondary sections |
| Border | `#DCE5F0` | Dividers and card outlines |
| Text primary | `#172B4D` | Main content |
| Text secondary | `#64748B` | Metadata and helper text |

## Typography
- Primary UI font: system sans / Inter-compatible; Chinese fallback: `PingFang SC`, `Microsoft YaHei`, `Noto Sans CJK SC`.
- Page title: 24–28px / semibold.
- Section title: 16–18px / semibold.
- Body: 14–16px / regular.
- Metadata: 12–13px; never below 12px.
- Line height: 1.45–1.65 for scientific explanatory text.
- Numeric result values use tabular numerals.

## Layout grid
- Desktop left navigation: 232–248px.
- Central conversation: max-width 820px; comfortable reading measure 680–820px.
- Workbench side panel: 360–420px, resizable; collapse when not needed.
- Full workbench route: content max-width 1280px.
- Main spacing scale: 4 / 8 / 12 / 16 / 24 / 32px.
- Card radius: 10–14px; button radius: 8–10px.
- Avoid heavy shadows; use subtle border and restrained elevation.

## Component rules
- Primary button: solid blue, one dominant action per surface.
- Secondary button: white surface, blue/neutral border.
- Tertiary action: text button.
- Status chips always include text; color is supplementary.
- Cards use consistent header, content, optional footer anatomy.
- Tabs use one shared active indicator and spacing.
- Form controls share height, focus ring, disabled and error styles.
- Scientific charts include axis units, legend, source/time range and truncation/NoData disclosure.
- Maps are shown only when spatial results are relevant; always provide legend, CRS/units and layer provenance.
- Technical identifiers and checksums stay in expandable details.

## Status semantics
- Blue: active/running/current.
- Green: verified/succeeded/published.
- Amber: waiting/needs user input/review/limited evidence.
- Red: failed/rejected/invalid.
- Grey: queued/not started/not applicable/unknown.
Unknown must never be rendered as success.

## Page-specific composition
1. Home: central research prompt and a small set of domain task starters; no map dashboard.
2. Session: conversation first, task header only when a Task exists, workbench on demand.
3. Task workbench: Data / Plan & Parameters / Execution / Results.
4. Plan & confirmation: readable research plan first; technical DAG collapsed.
5. WorkflowRun: truthful business-stage timeline; node details on demand.
6. Results: summary and scientific quality first; choose map/chart/table/report by artifact type.
7. Data library: directory → version detail → preview, preserving exact resource identity.

## Responsive behavior
- >= 1280px: navigation + conversation; optional workbench side panel.
- 1024–1279px: compact navigation; workbench as route/drawer.
- 768–1023px: single content column; side panels become full-height drawer.
- < 768px: single-column conversation; workbench pages open full-screen; composer remains reachable.
- Do not rely on color alone; all statuses have text and accessible labels.

## Reference image interpretation
The images in `design-references/` are visual references, not production screenshots or exact pixel specifications. Generated text and sample data inside images may be inaccurate. Codex must implement the layout, hierarchy, component language and visual style while binding all values to real APIs and product metadata.
