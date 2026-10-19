---
name: Private Statement Reader
description: A native Mac document utility with source evidence above financial checks.
colors:
  ink: "#1d1d1f"
  muted: "#5b5b61"
  background: "#f5f5f7"
  sidebar: "#ececee"
  surface: "#ffffff"
  subtle: "#f1f1f3"
  control: "#ffffff"
  line: "#d4d4d8"
  hover: "#e3e3e7"
  accent: "#0066d9"
  primary-hover: "#0054ba"
  selection: "#dceaff"
  selected: "#0066d9"
  selected-ink: "#ffffff"
  selected-muted: "#e5efff"
  warning: "#805000"
  warning-surface: "#fff1ce"
  error: "#b3261e"
  error-surface: "#ffebe9"
  success: "#28633e"
  success-surface: "#e4f1e8"
  dark-ink: "#f5f5f7"
  dark-muted: "#b6b6be"
  dark-background: "#202024"
  dark-sidebar: "#28282d"
  dark-surface: "#2c2c31"
  dark-subtle: "#34343a"
  dark-control: "#3b3b42"
  dark-line: "#4a4a52"
  dark-hover: "#45454d"
  dark-accent: "#78b7ff"
  dark-selection: "#214b7a"
  dark-selected: "#075db8"
  dark-selected-ink: "#ffffff"
  dark-selected-muted: "#e3efff"
  dark-warning: "#ffd17c"
  dark-warning-surface: "#47391f"
  dark-error: "#ffb4ab"
  dark-error-surface: "#4c2a29"
  dark-success: "#a5ddb5"
  dark-success-surface: "#283c2f"
typography:
  empty-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.35
  subheading:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "14px"
    fontWeight: 600
  tag:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "10px"
    fontWeight: 400
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.35
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "15px"
    fontWeight: 600
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  secondary:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.5
rounded:
  tag: "3px"
  text-block: "4px"
  control: "5px"
  review: "6px"
  evidence: "7px"
spacing:
  control-block: "3px"
  control-inline: "10px"
  row: "8px"
  group: "12px"
  section: "16px"
  workspace-inline: "24px"
components:
  button-primary:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.selected-ink}"
    rounded: "{rounded.control}"
    padding: "3px 10px"
  button-secondary:
    backgroundColor: "{colors.control}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "3px 10px"
  search-field:
    backgroundColor: "{colors.control}"
    textColor: "{colors.ink}"
    rounded: "{rounded.review}"
    padding: "3px 7px"
  document-selected:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.selected-ink}"
    rounded: "{rounded.control}"
    padding: "8px"
  warning-tag:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.warning}"
    rounded: "{rounded.tag}"
    padding: "2px 5px"
  evidence-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.evidence}"
---

# Design System: Private Statement Reader

## Overview

**Creative North Star: "The Native Mac Document Utility"**

The user selected native macOS appearance and conventions. The shipped interface combines an AppKit window, menus, unified compact toolbar and file panels with a compact WKWebView document workspace. System typography, neutral surfaces and blue selection place the document and its evidence ahead of decorative identity.

The workspace remains dense enough for statement processing while keeping uncertain values readable. Scan images appear before extracted fields. Light and dark appearance follow the system by default, with app-specific Light and Dark options in the View menu. White scan paper retains its original appearance in both themes.

**Key Characteristics:**

- Actual AppKit window controls, toolbar icons, menus and file panels.
- System typography and compact neutral controls.
- Independent library and workspace scroll panes.
- Full-width source evidence above flat property fields.
- Explicit amber uncertainty and red error states.

## Colors

The palette uses one blue accent with neutral surfaces and distinct semantic states. The frontmatter records the CSS values; dark-prefixed primitives correspond to the dark appearance overrides.

### Primary

- **System Blue:** the accent colors identify links, focus and source document icons. The selected colors fill the active library row and primary buttons, with separate selected text and secondary text colors.
- **Pale Blue Selection:** the selection colors identify selected table rows and text ranges without competing with source evidence.

### Neutral

- **Document Gray:** background and sidebar colors separate working regions. In the native host, the sidebar is transparent over the actual macOS sidebar material.
- **Paper Surface:** surface and control colors carry evidence bodies, inputs and buttons. Subtle surfaces group source captions and table headings; line and hover colors provide boundaries and interactive states.
- **Primary and Secondary Text:** ink carries values and headings; muted carries field labels, captions and progress notes.

### Semantic States

- **Amber:** warning and warning-surface identify unresolved, estimated, conflicting, missing and AI-derived readings.
- **Red:** error and error-surface identify processing or saving failures.
- **Green:** success and success-surface identify existing positive row tags. They do not certify source accuracy.

**The Source Paper Rule.** Scan imagery remains white-backed and uninverted in both appearances.

## Typography

All web text uses the system family, with Apple San Francisco resolved on macOS. Native controls use AppKit typography. Tabular numerals support amount comparison; a decorative display or monospace family is not part of this system.

- Document titles use the title role; source issue headings use a smaller semibold treatment (14px).
- Section headings use the section role; empty-state headings remain compact (19px).
- Values and ordinary controls use the body role.
- Secondary prose uses the secondary role, usually constrained to 85–90 characters in measure.
- Property labels, captions and library metadata use the label scale; the library group headings are semibold.

## Layout

The native window starts at a working size (1380 × 900px), has a minimum size (960 × 650px), and remembers its frame. Its unified compact toolbar sits above the content. The native host hides the browser fallback header; no HTML imitation of window controls is used.

The library is a fixed sidebar (230px). The workspace consumes the remaining width with padding (20px 24px 28px). Both regions scroll independently while the page body stays fixed to the window. Toggling the sidebar gives its space to the workspace. The standalone browser header is compact (52px); native content uses the full available webview height.

Source cards occupy a single full-width column. Their source viewport has a vertical cap (320px) and local scrolling. Images have a minimum display width (680px), allowing horizontal inspection instead of shrinking tiny statement text. Property fields use three columns, with descriptions spanning the full row. Six financial checks appear per page.

At narrower widths (1100px), the optional table and inspector stack, and workspace padding tightens. At 850px, properties use two columns and title controls wrap. The 600px browser fallback uses a narrower sidebar (180px) and one property column; this is below the native window minimum.

## Elevation & Depth

Most hierarchy comes from neutral layers and thin separators rather than shadow. AppKit supplies the actual window and toolbar material. Evidence and review containers use a single border. The transient saved-message toast uses the only authored web shadow (`0 4px 16px #0002`). There is no authored interface animation.

Keyboard focus uses an explicit outline (3px) offset from its control (3px). Native file panels, menus and toolbar states remain owned by AppKit.

## Shapes

Controls have small rounded corners, with slightly larger radii for search and source containers. Thin borders define boundaries. Compact tags use the smallest radius. Source images preserve rectangular document geometry and are clipped only by their scrolling viewport.

## Components

### Native Toolbar and Menus

The real AppKit toolbar uses SF Symbols for sidebar, import, finished files and people. It supports customization and saved configuration. File, Edit and View menus expose import, finished files, find, sidebar visibility, people, reload and app appearance. Standard native open/save panels handle files.

### Library Navigation

The library groups documents under Processing, Queued, Completed and Unrecognized. Each row has a small stroked document icon, filename and progress metadata. The active row has a blue fill, white filename and pale secondary text. Long filenames wrap.

### Buttons and Fields

Web buttons and fields have a compact minimum height (26px). Primary buttons use the selected blue; secondary actions use the control surface. Hover uses the neutral hover surface, active uses selection, and disabled controls reduce opacity. Search uses the native browser search input affordance. Text fields retain visible placeholder and keyboard focus states.

### Source Evidence

The screenshot is the first region of every financial check card. A caption identifies whether it is a row crop, source table or full page. The issue heading and messages precede the flat values grid. Missing, estimated and conflicting financial values or dates stay visible in amber, with earlier readings where available. The default Financial checks list contains financial errors and estimated financial values or dates. Information notes for name guesses, AI narration, weak aggregate confidence and a missing match from another OCR pass remain available in a separate collapsed section and a filter when there is no concrete numeric conflict. No warnings are removed or transaction values changed by this grouping; arithmetic does not independently verify the printed source.

The source failure state provides an original-PDF link and page reference. Pending documents show provisional checks without correction controls. Finished documents offer optional row inspection. Manual confirmation does not conceal arithmetic or inference warnings.

### Optional Inspection

The source inspector, corrections, row table and advanced layout controls sit inside a collapsed disclosure by default. Every source caption also offers a keyboard-accessible Inspect full page button, including during processing. It opens the full-resolution page with local horizontal scrolling rather than shrinking the scan. Automatic extraction and saved outputs do not require approval or calibration. Selected rows, focus, current source page and save feedback use the same neutral and blue vocabulary.

## Do's and Don'ts

### Do:

- Do use actual AppKit controls for window, toolbar, menus and file panels.
- Do preserve compact system typography and blue selection in both appearances.
- Do show source imagery above uncertain values at a readable width.
- Do retain semantic labels alongside color for every unresolved reading.
- Do keep library and workspace scrolling independent.

### Don't:

- Don't add simulated macOS traffic lights or duplicate the native toolbar in HTML.
- Don't invert scan paper when changing appearance.
- Don't present arithmetic agreement or an OCR score as proof of source accuracy.
- Don't turn optional inspection, confirmation or layout controls into completion gates.
- Don't replace the selected native Mac conventions with marketing typography or decorative animation.
