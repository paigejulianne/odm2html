# odm2html

Convert CDISC ODM XML — including REDCap exports — into standalone HTML forms with shared CSS/JS, HTML5 + JavaScript validation, and REDCap branching/calc support.

---

## Contents

- [Overview](#overview)
- [Install & requirements](#install--requirements)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Output files](#output-files)
- [Method of operation](#method-of-operation)
- [Configuration reference (`rules.json`)](#configuration-reference-rulesjson)
- [REDCap field-type reference](#redcap-field-type-reference)
- [REDCap expression compilation](#redcap-expression-compilation)
- [Customization examples](#customization-examples)
- [Known limitations](#known-limitations)

---

## Overview

`odm2html.py` takes a CDISC ODM XML file and produces:

1. One HTML file **per `FormDef`** in the ODM.
2. One **shared CSS** file used by every HTML output from that ODM.
3. One **shared JS** file used by every HTML output from that ODM.

It auto-detects REDCap exports (presence of the `https://projectredcap.org` namespace) and switches to REDCap-aware rendering: `redcap:FieldType` drives widget choice, `redcap:TextValidationType` drives input subtype, multi-checkbox items are collapsed into one `<fieldset>`, and `redcap:BranchingLogic` / `redcap:Calculation` are compiled to JavaScript so the form behaves at runtime.

For non-REDCap ODM the script falls back to a generic path: the input type comes from ODM `DataType`, and a codelist becomes a radio group (≤ threshold options) or a `<select>` (above threshold).

Every transformation — class names, HTML templates per widget kind, type maps, validation behavior, and per-field overrides — is configurable through `rules.json`.

---

## Install & requirements

- **Python 3.8+** (uses `from __future__ import annotations` so older 3.x will work, but tested on 3.14).
- **No external Python dependencies.** Uses only `xml.etree.ElementTree`, `argparse`, `json`, `html`, `pathlib`, `re`, `copy`.

```powershell
# Windows
py install default        # if Python isn't already installed
python odm2html.py --help
```

```bash
# macOS / Linux
python3 odm2html.py --help
```

---

## Quick start

```powershell
# Convert with the default rules
python odm2html.py input.xml -o output

# Convert with a custom rules file
python odm2html.py input.xml -o output -r rules.json
```

For an ODM with three forms named `Demographics`, `Vitals`, `Labs`, that produces:

```
output.css
output.js
output_Demographics.html
output_Vitals.html
output_Labs.html
```

Open any `.html` file in a browser. The CSS and JS load alongside via relative paths.

For an ODM with **one** form and an `-o output.html`, the file is written exactly at that path (no suffix added), still alongside `output.css` / `output.js`.

---

## CLI reference

```
python odm2html.py INPUT -o OUT [-r RULES]
```

| Arg | Required | Description |
|---|---|---|
| `INPUT` | yes | Path to the ODM XML file. |
| `-o`, `--out` | yes | Output path/prefix. If it ends in `.html` and there's one form, that exact path is used. Otherwise treated as a stem: per-form files are written as `<stem>_<formName>.html`. Always also writes `<stem>.css` and `<stem>.js` in the same directory. |
| `-r`, `--rules` | no | Path to a `rules.json` file. Any keys present override the script defaults (deep-merge). |

Form names in filenames are sanitized: anything outside `[A-Za-z0-9._-]` becomes `_`.

---

## Output files

For a given output stem `<prefix>` and study with N forms, you get:

| File | Content |
|---|---|
| `<prefix>.css` | Stylesheet shared by all generated forms. Default is baked into the script; override via `assets.cssPath`. |
| `<prefix>.js` | Runtime shared by all generated forms. Handles HTML5 + custom-pattern validation, REDCap calc auto-evaluation, and BranchingLogic show/hide. Override via `assets.jsPath`. |
| `<prefix>_<formName>.html` | One per `FormDef`. Each `<head>` `<link>`s to the CSS, the `<body>` ends with `<script src=...>` for the JS. |

The HTML pages reference the CSS/JS by **relative href** equal to the file basename, so the three (or 2 + N) files must stay co-located. To rename the link target, set `assets.cssFilename` / `assets.jsFilename`.

---

## Method of operation

The pipeline, top-down:

```
ODM XML
  │
  │   ET.parse → root element
  ▼
detect_redcap(root)      ← any `redcap:*` attribute anywhere?
  │
  ▼
OdmModel(mdv)            ← index FormDef / ItemGroupDef / ItemDef / CodeList by OID
  │
  ▼
Renderer(rules, redcap_mode)
  │
  ▼
for each FormDef:
   render_form(form)
     ├─ for each ItemGroupRef:
     │    render_group(group)
     │      ├─ emit section_header (if group.Name is non-empty)
     │      └─ walk ItemRefs:
     │           ├─ if REDCap checkbox-group candidate → render_checkbox_group(siblings)
     │           └─ else → render_item(item)
     │                 ├─ REDCap mode: dispatch by redcap:FieldType
     │                 │     text / textarea / file / select / radio /
     │                 │     yesno / truefalse / checkbox / slider / calc / descriptive
     │                 └─ Generic mode: codelist-aware (radio vs select by threshold) or text-like
     │
     └─ wrap in page template (links CSS, embeds JS at bottom)
```

### REDCap detection

Triggered if any element in the tree has an attribute in the `https://projectredcap.org` namespace. Once detected, the renderer uses `redcap:FieldType` (mapped via `redcapWidgetMap`) to pick a widget instead of guessing from `DataType` + codelist size.

### Checkbox-group collapsing (REDCap)

REDCap exports a multi-checkbox question as N separate `ItemDef`s with `___0`, `___1`, ..., `___N` suffixes — each with a 2-value codelist (`Checked` / `Unchecked`) and sharing one `redcap:Variable`. The renderer:

1. While walking `ItemRef`s in a group, when it hits an item with `FieldType="checkbox"` and a `redcap:Variable`, scans forward for consecutive items with the same `redcap:Variable` and `FieldType`.
2. If ≥ 2 siblings, emits **one** `<fieldset>` with N `<input type="checkbox">` — one per sibling.
3. Option labels come from `redcap:CheckboxChoices` on the codelist (e.g. `"0, Monday | 1, Tuesday | ..."`). The suffix in the OID (`gym___2`) is matched to the value side of `CheckboxChoices` to pick the label.

A single isolated checkbox item (no siblings) is rendered as a Yes/No radio pair.

### REDCap expression compilation

`redcap:BranchingLogic` and `redcap:Calculation` are REDCap expressions. At HTML-generation time they are translated to JavaScript and stored on the `<div class="odm-item">` (for branching) or `<input>` (for calc) as `data-branch` / `data-calc` attributes. The shared JS reads these at load and on every `input`/`change` event:

- **Branching**: evaluated as a boolean; the item is hidden via `.odm-hidden` and its inputs disabled when false.
- **Calc**: evaluated as a number; the result is written into the `.value` of the calc input.

Evaluation uses `new Function('return ' + expr)` with REDCap helper functions (`red_round`, `red_datediff`, etc.) and `getValue` injected as parameters. No closure access; sandboxed to those names.

### Asset generation

- The default CSS and JS strings are constants inside `odm2html.py`.
- If `rules.assets.cssPath` (resp. `jsPath`) is set, the script reads that file and uses its contents instead.
- Either way, the result is written verbatim to `<prefix>.css` / `<prefix>.js` in the output directory.
- HTML pages reference them by basename (or by `assets.cssFilename` / `assets.jsFilename` if set).

---

## Configuration reference (`rules.json`)

Every key is **optional**. Defaults are baked into the script. User values are deep-merged: setting only `classes.item` overrides the `item` class and leaves every other class unchanged.

Top-level keys:

| Key | Type | Purpose |
|---|---|---|
| `classes` | object | CSS class names per element kind |
| `templates` | object | HTML snippet templates per widget |
| `dataTypeMap` | object | ODM `DataType` → HTML input type (generic mode) |
| `redcapValidationMap` | object | REDCap `TextValidationType` → HTML input type |
| `redcapWidgetMap` | object | REDCap `FieldType` → internal widget name |
| `validation` | object | Validation toggles and `customPatterns` |
| `assets` | object | CSS/JS source paths and output filenames |
| `fieldOverrides` | object | Per-field template/class/attr overrides |
| `codeListAsRadioThreshold` | int | Generic mode: codelists ≤ N → radio, else `<select>` (default `4`) |
| `includeFormTitle` | bool | Emit `<h2>` with form name (default `true`) |
| `includeSectionHeaders` | bool | Emit `<h3>` for each ItemGroupDef.Name (default `true`) |
| `ignoreDefaultLength` | string | If `ItemDef.Length` equals this, skip `maxlength` (default `"999"` — REDCap default) |
| `submitButtonLabel` | string | Text on the submit button (default `"Submit"`) |

### `classes`

CSS class name per element kind. Referenced from templates as `{class_<key>}`. Defaults:

| Key | Default | Used by |
|---|---|---|
| `form` | `odm-form` | `<form>` element |
| `section` | `odm-section` | `<h3>` section divider |
| `item` | `odm-item` | wrapping `<div>` for every field |
| `label` | `odm-label` | `<label>` element |
| `input` | `odm-input` | `<input>` (text, number, date, file, etc.) |
| `select` | `odm-select` | `<select>` element |
| `textarea` | `odm-textarea` | `<textarea>` element |
| `fieldset` | `odm-radio` | radio-group `<fieldset>` |
| `checkboxes` | `odm-checkboxes` | checkbox-group inner `<fieldset>` |
| `checkboxGroup` | `odm-checkbox-group` | checkbox-group outer wrapper |
| `slider` | `odm-slider` | slider wrapper |
| `sliderLabels` | `odm-slider-labels` | div under slider holding labels |
| `calc` | `odm-calc` | calc field wrapper |
| `descriptive` | `odm-descriptive` | descriptive (no-input) field |
| `note` | `odm-note` | field-note `<small>` |
| `required` | `req` | the `*` span next to required labels |
| `submit` | `odm-submit` | submit `<button>` |

### `templates`

Every HTML snippet is a `str.format_map`-style template. Missing placeholders render as empty strings, so templates can omit anything they don't need.

| Template name | Placeholders |
|---|---|
| `page` | `title`, `body`, `css_href`, `js_href` |
| `form` | `oid`, `title_html`, `body`, `submit_html` |
| `form_title` | `name` |
| `section_header` | `oid`, `title`, `class_section` |
| `submit_button` | `label`, `class_submit` |
| `required_marker` | `class_required` |
| `field_note` | `text`, `class_note` |
| `branch_attr` | `expr` (the precompiled JS expression) |
| `item_text` | `oid`, `id`, `name`, `label`, `input_type`, `attrs`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `item_textarea` | `oid`, `id`, `name`, `label`, `rows`, `attrs`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `item_file` | `oid`, `id`, `name`, `label`, `attrs`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `item_select` | `oid`, `id`, `name`, `label`, `attrs`, `options`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `select_option` | `value`, `text` |
| `item_radio` | `oid`, `label`, `options`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `radio_option` | `id`, `name`, `value`, `text`, `attrs` |
| `item_checkbox_group` | `oid`, `label`, `options`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `checkbox_option` | `id`, `name`, `text`, `attrs` |
| `item_slider` | `oid`, `id`, `name`, `label`, `min`, `max`, `attrs`, `slider_labels_html`, `required_marker`, `note_html`, `branch_attr`, `class_*` |
| `slider_labels` | `labels`, `class_sliderLabels` |
| `slider_label` | `text` |
| `item_calc` | `oid`, `id`, `name`, `label`, `calc_js`, `attrs`, `note_html`, `branch_attr`, `class_*` |
| `item_descriptive` | `oid`, `label`, `note_html`, `branch_attr`, `class_*` |

For every template, every `class_<key>` from `classes` is also available as a placeholder.

To override only one template, include only that name in your `rules.json`:

```json
{
  "templates": {
    "submit_button": "<button type=\"submit\" class=\"my-btn\">{label}</button>"
  }
}
```

### `dataTypeMap`

Used in **generic mode** (no REDCap namespace). Maps ODM `DataType` attribute to an HTML `<input type=...>`. Default:

```json
{
  "text": "text", "string": "text",
  "integer": "number", "float": "number", "double": "number",
  "date": "date", "datetime": "datetime-local", "time": "time",
  "boolean": "checkbox"
}
```

Unmapped types fall through to `text`.

### `redcapValidationMap`

REDCap `TextValidationType` → HTML input type. Used in REDCap mode when `FieldType="text"`. Default keys cover all stock REDCap validators:

| TextValidationType | HTML input type |
|---|---|
| `email` | `email` |
| `phone`, `phone_australia`, `phone_uk` | `tel` |
| `int`, `integer` | `number` (with `step="1"`) |
| `float`, `number`, `number_Ndp`, `number_Ndp_comma_decimal` | `number` (with `step="any"`) |
| `zipcode` | `text` (with `inputmode="numeric"` and `pattern`) |
| `time`, `time_hh_mm_ss`, `time_mm_ss` | `time` |
| `date_mdy`, `date_dmy`, `date_ymd` | `date` |
| `datetime_*` | `datetime-local` |

### `redcapWidgetMap`

REDCap `FieldType` → internal widget name (the renderer dispatches on this). Default:

```json
{
  "text": "text", "textarea": "textarea", "notes": "textarea",
  "radio": "radio", "select": "select", "dropdown": "select",
  "checkbox": "checkbox", "yesno": "yesno", "truefalse": "truefalse",
  "file": "file", "slider": "slider", "calc": "calc",
  "descriptive": "descriptive"
}
```

Internal widget names — these are what the renderer dispatches on, so use these as override values:

`text`, `textarea`, `radio`, `select`, `checkbox`, `yesno`, `truefalse`, `file`, `slider`, `calc`, `descriptive`

Unmapped FieldType values fall through to `text`.

### `validation`

```json
{
  "html5": true,
  "javascript": true,
  "branchingLogic": true,
  "calcAutoEval": true,
  "customPatterns": {
    "ssn": "^\\d{3}-?\\d{2}-?\\d{4}$",
    "mrn_10d": "^\\d{10}$",
    "zipcode": "^\\d{5}(-\\d{4})?$"
  }
}
```

| Key | Effect |
|---|---|
| `html5` | (Currently informational.) `required`, `min`, `max`, `maxlength`, `pattern`, and typed inputs are always emitted; the runtime layer can suppress submission if validation fails. |
| `javascript` | If `false`, the renderer omits `data-branch` / `data-calc` and the JS no-ops. The form still validates via HTML5. |
| `branchingLogic` | If `false`, `redcap:BranchingLogic` is ignored at gen time (no `data-branch` emitted). |
| `calcAutoEval` | If `false`, `redcap:Calculation` is ignored (no `data-calc` emitted). |
| `customPatterns` | Maps a REDCap `TextValidationType` (e.g. `ssn`, `mrn_10d`, `zipcode`) to a regex. When that vtype appears on a text field, the regex is emitted as `pattern="..."`. |

Add your own patterns by adding keys; e.g. `"phone_us": "^\\(\\d{3}\\) \\d{3}-\\d{4}$"`.

### `assets`

```json
{
  "cssPath": null,
  "jsPath": null,
  "cssFilename": null,
  "jsFilename": null
}
```

| Key | Effect |
|---|---|
| `cssPath` | Path to a custom CSS file. Its contents are written verbatim to `<prefix>.css`. If `null`, the script's built-in default CSS is used. |
| `jsPath` | Same, for the JS runtime. |
| `cssFilename` | Filename written into the `<link href=...>` in each HTML page. If `null`, defaults to `<prefix>.css`. Useful if you want all forms to reference a shared CSS at a fixed path. |
| `jsFilename` | Same, for the `<script src=...>` href. |

**Caveat for custom JS**: if you replace `jsPath` with your own file, you lose the REDCap calc helpers (`red_round`, `red_datediff`, etc.) and the runtime that evaluates `data-branch` / `data-calc`. Either include those helpers yourself or set `validation.branchingLogic` and `validation.calcAutoEval` to `false` to stop emitting the attributes.

### `fieldOverrides`

Per-field customization. Keyed by — in order of preference — `redcap:Variable`, `ItemDef.Name`, `OID`. The first match wins.

```json
{
  "fieldOverrides": {
    "study_id": {
      "templateOverrides": {
        "item_text": "<div class=\"highlighted\">{label}{required_marker}<input id=\"{id}\" name=\"{name}\" type=\"text\"></div>"
      },
      "classOverrides": {
        "item": "study-id-item",
        "input": "study-id-input"
      },
      "extraAttrs": {
        "data-required-pii": "true",
        "autocomplete": "off"
      }
    }
  }
}
```

- `templateOverrides` — replace any template *for this field only*. Same placeholders.
- `classOverrides` — replace any class names for this field's template; affects `{class_<key>}` substitution.
- `extraAttrs` — extra HTML attributes added to the input/select/textarea inside this item.

---

## REDCap field-type reference

How each REDCap `FieldType` is rendered:

| REDCap FieldType | Default widget | Emits |
|---|---|---|
| `text` | text | `<input type="...">` driven by `TextValidationType` (see map above) |
| `textarea`, `notes` | textarea | `<textarea rows="4">` |
| `radio` | radio | `<fieldset>` of `<input type="radio">` (always; no count threshold) |
| `select`, `dropdown` | select | `<select>` (always; no count threshold) |
| `checkbox` (run of siblings) | checkbox group | one `<fieldset>` of `<input type="checkbox">`, labels from `CheckboxChoices` |
| `checkbox` (isolated) | yesno | Yes/No radio pair |
| `yesno` | yesno | Yes/No radio pair (`1`=Yes, `0`=No) |
| `truefalse` | truefalse | True/False radio pair |
| `file` | file | `<input type="file">` |
| `slider` | slider | `<input type="range">`, min/max from RangeChecks (default `0`–`100`), labels from `SliderLabels` parsed on `\|` |
| `calc` | calc | `<input type="text" readonly>` with `data-calc="<compiled JS>"` |
| `descriptive` | descriptive | `<p>` with the Question text, no input |

REDCap-specific attributes also consumed:

| Attribute | Used for |
|---|---|
| `redcap:FieldNote` | `<small class="odm-note">` under the field |
| `redcap:SectionHeader` | (Currently read but not surfaced; section dividers come from `ItemGroupDef.Name`.) |
| `redcap:Variable` | Stable field name; used to detect checkbox-group siblings and to key `fieldOverrides` |
| `redcap:CheckboxChoices` (on CodeList) | Option labels for multi-checkbox |
| `redcap:SliderLabels` | Pipe-separated labels under the slider |
| `redcap:Calculation` | Compiled to JS, stored as `data-calc` |
| `redcap:BranchingLogic` | Compiled to JS, stored as `data-branch` |

---

## REDCap expression compilation

REDCap expressions are translated to JavaScript at generation time. Token-level rules:

| REDCap | JavaScript |
|---|---|
| `[field]` (branching context) | `getValue('field')` |
| `[field]` (calc context) | `(Number(getValue('field'))\|\|0)` |
| `[field(N)]` (checkbox value) | `getValue('field___N')` |
| `=` | `==` |
| `<>` | `!=` |
| `^` | `**` (exponent) |
| `and`, `AND` | `&&` |
| `or`, `OR` | `\|\|` |

Function names map to runtime helpers defined in the generated JS:

| REDCap function | JS helper |
|---|---|
| `round(x, n)` | `red_round(x, n)` |
| `rounddown(x, n)` | `red_rounddown(x, n)` |
| `roundup(x, n)` | `red_roundup(x, n)` |
| `sum(...)` | `red_sum(...)` |
| `mean(...)` | `red_mean(...)` |
| `min(...)`, `max(...)` | `red_min(...)`, `red_max(...)` |
| `abs(x)`, `sqrt(x)` | `red_abs(x)`, `red_sqrt(x)` |
| `if(cond, a, b)` | `red_if(cond, a, b)` |
| `datediff(d1, d2, unit)` | `red_datediff(d1, d2, unit)` |

Compiled expressions are evaluated in the browser with:

```js
new Function(...helperNames, 'return (' + expr + ');').apply(null, helperValues)
```

This has no closure access and only sees the injected helpers + `getValue`.

**Example.** REDCap source:

```
[sex] = "0" and [given_birth] = "1"
```

Compiled (stored as `data-branch`):

```
getValue('sex') == "0" && getValue('given_birth') == "1"
```

**Example.** REDCap calc:

```
round(([weight]*10000)/(([height])^(2)), 1)
```

Compiled (stored as `data-calc`):

```
red_round(((Number(getValue('weight'))||0)*10000)/(((Number(getValue('height'))||0))**(2)), 1)
```

---

## Customization examples

### 1. Brand colors only — keep all structure

Drop your own CSS in next to the script and point at it:

```json
{
  "assets": { "cssPath": "./my-theme.css" }
}
```

Now `<prefix>.css` is your file. Use the default class names (`.odm-form`, `.odm-section`, `.odm-item`, etc.) — they're in the table above.

### 2. Rename a class globally

```json
{
  "classes": { "item": "field-row", "label": "field-label" }
}
```

Every `{class_item}` in templates resolves to `field-row`. Your CSS needs to target the new names.

### 3. Replace the submit button template

```json
{
  "templates": {
    "submit_button": "<button type=\"submit\" class=\"{class_submit} primary lg\">{label} &rarr;</button>"
  },
  "submitButtonLabel": "Save and continue"
}
```

### 4. Different layout for one specific field

```json
{
  "fieldOverrides": {
    "study_id": {
      "templateOverrides": {
        "item_text": "<div class=\"hero-field\"><label for=\"{id}\">{label}</label><input id=\"{id}\" name=\"{name}\" type=\"text\" autofocus></div>"
      }
    }
  }
}
```

Identifies the field by REDCap `redcap:Variable="study_id"`, `ItemDef Name="study_id"`, or `OID="study_id"` — first match wins.

### 5. Disable JS validation entirely

```json
{
  "validation": {
    "javascript": false,
    "branchingLogic": false,
    "calcAutoEval": false
  }
}
```

The `<prefix>.js` is still written (the runtime is harmless) but no `data-branch` / `data-calc` attributes are emitted, and the runtime falls through to a no-op for them. HTML5 validation still applies.

### 6. Add a custom pattern for a REDCap validator

If your REDCap project uses `TextValidationType="mrn_custom"` for a custom 8-digit MRN:

```json
{
  "validation": {
    "customPatterns": {
      "mrn_custom": "^\\d{8}$"
    }
  }
}
```

Now any text field with that vtype gets `pattern="^\d{8}$"`.

### 7. Map a non-standard REDCap field type

If REDCap returns a `FieldType` we don't recognize and you want it to render as a textarea:

```json
{
  "redcapWidgetMap": { "rich_text": "textarea" }
}
```

---

## Known limitations

See `MEMORY.md` / `known_gaps.md` for the canonical list. Highlights:

- `datediff([dateField], 'today', 'y')` — date field refs in calc context are wrapped in `Number(...)` and won't parse as dates. Fix needs context-aware compilation.
- REDCap `smart()` variables, `[event-name]` cross-event refs, and lookup functions are not handled.
- `redcap:MatrixGroupName` items render as separate questions rather than an HTML `<table>` grid.
- `redcap:Identifier="y"` (PHI marker) is not surfaced visually.
- Repeating `ItemGroupDef Repeating="Yes"` is rendered once with no add/remove UI.
- Multilingual `TranslatedText` — only the first child is used; `xml:lang` is ignored.
- `MeasurementUnitRef` — units defined on ItemDefs are not shown beside inputs.
- Only the first `Study` and `MetaDataVersion` are processed.
- Generic ODM `ConditionalDef`, `DerivedValue`, `ComputationMethod` are not interpreted.

---

## License

See [LICENSE](LICENSE).
