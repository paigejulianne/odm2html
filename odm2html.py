#!/usr/bin/env python3
"""odm2html — convert CDISC ODM (incl. REDCap) into HTML forms.

Architecture:
  - All HTML transformation rules live in rules.json (templates, classes,
    field-type maps, per-field overrides). The script ships defaults that
    rules.json can deep-merge over.
  - One CSS and one JS file are emitted per ODM file and shared across all
    per-form HTML outputs. Custom CSS/JS paths can be configured via
    `assets.cssPath` / `assets.jsPath` in rules.json.
  - REDCap BranchingLogic and Calculation expressions are compiled to
    JS at generation time and stored as `data-branch` / `data-calc`
    attributes; the shared JS evaluates them at runtime.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"
REDCAP_NS = "https://projectredcap.org"


# ============================================================================
# DEFAULTS — every value here can be overridden by rules.json (deep merge)
# ============================================================================

DEFAULT_CLASSES = {
    "form": "odm-form",
    "section": "odm-section",
    "item": "odm-item",
    "label": "odm-label",
    "input": "odm-input",
    "select": "odm-select",
    "textarea": "odm-textarea",
    "fieldset": "odm-radio",
    "checkboxes": "odm-checkboxes",
    "checkboxGroup": "odm-checkbox-group",
    "slider": "odm-slider",
    "sliderLabels": "odm-slider-labels",
    "calc": "odm-calc",
    "descriptive": "odm-descriptive",
    "note": "odm-note",
    "required": "req",
    "submit": "odm-submit",
}

DEFAULT_TEMPLATES = {
    "page": (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<title>{title}</title>\n'
        '<link rel="stylesheet" href="{css_href}">\n'
        '</head>\n'
        '<body>\n'
        '<h1>{title}</h1>\n'
        '{body}\n'
        '<script src="{js_href}"></script>\n'
        '</body>\n'
        '</html>\n'
    ),
    "form": (
        '<form class="{class_form}" id="{oid}" data-oid="{oid}">\n'
        '{title_html}'
        '{body}\n'
        '{submit_html}\n'
        '</form>'
    ),
    "form_title": '  <h2>{name}</h2>\n',
    "section_header": '  <h3 class="{class_section}" data-oid="{oid}">{title}</h3>',
    "submit_button": '  <button type="submit" class="{class_submit}">{label}</button>',
    "required_marker": ' <span class="{class_required}">*</span>',
    "field_note": '    <small class="{class_note}">{text}</small>',
    "branch_attr": ' data-branch="{expr}"',

    "item_text": (
        '    <div class="{class_item}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}{required_marker}</label>\n'
        '      <input type="{input_type}" id="{id}" name="{name}" class="{class_input}"{attrs}>\n'
        '{note_html}'
        '    </div>'
    ),
    "item_textarea": (
        '    <div class="{class_item}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}{required_marker}</label>\n'
        '      <textarea id="{id}" name="{name}" class="{class_textarea}" rows="{rows}"{attrs}></textarea>\n'
        '{note_html}'
        '    </div>'
    ),
    "item_file": (
        '    <div class="{class_item}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}{required_marker}</label>\n'
        '      <input type="file" id="{id}" name="{name}" class="{class_input}"{attrs}>\n'
        '{note_html}'
        '    </div>'
    ),
    "item_select": (
        '    <div class="{class_item}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}{required_marker}</label>\n'
        '      <select id="{id}" name="{name}" class="{class_select}"{attrs}>\n'
        '        <option value=""></option>\n'
        '{options}\n'
        '      </select>\n'
        '{note_html}'
        '    </div>'
    ),
    "select_option": '        <option value="{value}">{text}</option>',
    "item_radio": (
        '    <div class="{class_item}" data-oid="{oid}"{branch_attr}>\n'
        '      <fieldset class="{class_fieldset}">\n'
        '        <legend>{label}{required_marker}</legend>\n'
        '{options}\n'
        '      </fieldset>\n'
        '{note_html}'
        '    </div>'
    ),
    "radio_option": (
        '        <label><input type="radio" id="{id}" name="{name}" '
        'value="{value}"{attrs}> {text}</label>'
    ),
    "item_checkbox_group": (
        '    <div class="{class_item} {class_checkboxGroup}" data-oid="{oid}"{branch_attr}>\n'
        '      <fieldset class="{class_checkboxes}">\n'
        '        <legend>{label}{required_marker}</legend>\n'
        '{options}\n'
        '      </fieldset>\n'
        '{note_html}'
        '    </div>'
    ),
    "checkbox_option": (
        '        <label><input type="checkbox" id="{id}" name="{name}" '
        'value="1"{attrs}> {text}</label>'
    ),
    "item_slider": (
        '    <div class="{class_item} {class_slider}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}{required_marker}</label>\n'
        '      <input type="range" id="{id}" name="{name}" min="{min}" max="{max}"{attrs}>\n'
        '{slider_labels_html}'
        '{note_html}'
        '    </div>'
    ),
    "slider_labels": '      <div class="{class_sliderLabels}">{labels}</div>',
    "slider_label": '<span>{text}</span>',
    "item_calc": (
        '    <div class="{class_item} {class_calc}" data-oid="{oid}"{branch_attr}>\n'
        '      <label for="{id}" class="{class_label}">{label}</label>\n'
        '      <input type="text" id="{id}" name="{name}" readonly data-calc="{calc_js}"{attrs}>\n'
        '{note_html}'
        '    </div>'
    ),
    "item_descriptive": (
        '    <div class="{class_item} {class_descriptive}" data-oid="{oid}"{branch_attr}>\n'
        '      <p>{label}</p>\n'
        '{note_html}'
        '    </div>'
    ),
}

DEFAULT_DATATYPE_MAP = {
    "text": "text", "string": "text",
    "integer": "number", "float": "number", "double": "number",
    "date": "date", "datetime": "datetime-local", "time": "time",
    "boolean": "checkbox",
}

DEFAULT_REDCAP_VALIDATION_MAP = {
    "email": "email",
    "phone": "tel", "phone_australia": "tel", "phone_uk": "tel",
    "int": "number", "integer": "number",
    "float": "number", "number": "number",
    "number_1dp": "number", "number_2dp": "number",
    "number_3dp": "number", "number_4dp": "number",
    "number_1dp_comma_decimal": "number", "number_2dp_comma_decimal": "number",
    "number_3dp_comma_decimal": "number", "number_4dp_comma_decimal": "number",
    "zipcode": "text",
    "time": "time", "time_hh_mm_ss": "time", "time_mm_ss": "time",
    "date_mdy": "date", "date_dmy": "date", "date_ymd": "date",
    "datetime_mdy": "datetime-local", "datetime_dmy": "datetime-local",
    "datetime_ymd": "datetime-local",
    "datetime_seconds_mdy": "datetime-local",
    "datetime_seconds_dmy": "datetime-local",
    "datetime_seconds_ymd": "datetime-local",
}

DEFAULT_REDCAP_WIDGET_MAP = {
    "text": "text", "textarea": "textarea", "notes": "textarea",
    "radio": "radio", "select": "select", "dropdown": "select",
    "checkbox": "checkbox", "yesno": "yesno", "truefalse": "truefalse",
    "file": "file", "slider": "slider", "calc": "calc",
    "descriptive": "descriptive",
}

INTEGER_VTYPES = {"int", "integer"}

DEFAULT_CUSTOM_PATTERNS = {
    "ssn": r"^\d{3}-?\d{2}-?\d{4}$",
    "mrn_10d": r"^\d{10}$",
    "zipcode": r"^\d{5}(-\d{4})?$",
}

DEFAULT_RULES = {
    "classes": DEFAULT_CLASSES,
    "templates": DEFAULT_TEMPLATES,
    "dataTypeMap": DEFAULT_DATATYPE_MAP,
    "redcapValidationMap": DEFAULT_REDCAP_VALIDATION_MAP,
    "redcapWidgetMap": DEFAULT_REDCAP_WIDGET_MAP,
    "codeListAsRadioThreshold": 4,
    "includeFormTitle": True,
    "includeSectionHeaders": True,
    "ignoreDefaultLength": "999",
    "submitButtonLabel": "Submit",
    "validation": {
        "html5": True,
        "javascript": True,
        "branchingLogic": True,
        "calcAutoEval": True,
        "customPatterns": DEFAULT_CUSTOM_PATTERNS,
    },
    "assets": {
        "cssPath": None,
        "jsPath": None,
        "cssFilename": None,
        "jsFilename": None,
    },
    "fieldOverrides": {},
}


# ============================================================================
# Default CSS and JS (used when rules.assets.cssPath / jsPath are not set)
# ============================================================================

DEFAULT_CSS = """body { font-family: system-ui, -apple-system, sans-serif; max-width: 760px;
       margin: 2rem auto; padding: 0 1rem; color: #222; }
h1 { border-bottom: 1px solid #ccc; padding-bottom: 0.4rem; }
.odm-form { border: 1px solid #ddd; padding: 1rem 1.25rem; margin-bottom: 2rem;
            border-radius: 6px; background: #fafafa; }
.odm-section { margin: 1.5rem 0 0.5rem; padding: 0.4rem 0.6rem;
               background: #eef2ff; border-left: 4px solid #6366f1;
               border-radius: 3px; font-size: 1rem; }
.odm-item { margin: 0.85rem 0; }
.odm-item > label { display: block; margin-bottom: 0.25rem; font-weight: 500; }
.odm-item input[type=text], .odm-item input[type=email], .odm-item input[type=tel],
.odm-item input[type=number], .odm-item input[type=date],
.odm-item input[type=datetime-local], .odm-item input[type=time],
.odm-item select, .odm-item textarea {
  width: 100%; padding: 0.4rem; box-sizing: border-box;
  border: 1px solid #bbb; border-radius: 3px; font: inherit;
}
.odm-item textarea { resize: vertical; }
.odm-item input[readonly] { background: #f3f4f6; color: #555; }
.odm-item input:invalid { border-color: #c00; }
.odm-item.odm-error input,
.odm-item.odm-error select,
.odm-item.odm-error textarea { border-color: #c00; box-shadow: 0 0 0 2px rgba(204,0,0,0.15); }
.odm-radio label, .odm-checkboxes label {
  display: inline-block; margin-right: 1rem; font-weight: normal;
}
.odm-checkboxes label { display: block; margin: 0.25rem 0; }
.odm-slider input[type=range] { width: 100%; }
.odm-slider-labels { display: flex; justify-content: space-between;
                     font-size: 0.85em; color: #666; margin-top: 0.2rem; }
.odm-descriptive p { margin: 0.5rem 0; color: #444; font-style: italic; }
.odm-note { display: block; margin-top: 0.2rem; color: #666; font-size: 0.85em; }
.odm-error-msg { color: #c00; font-size: 0.85em; margin-top: 0.2rem; display: block; }
.req { color: #c00; }
.odm-submit { padding: 0.5rem 1.2rem; font: inherit; cursor: pointer;
              background: #2563eb; color: white; border: 0; border-radius: 4px; }
.odm-submit:hover { background: #1d4ed8; }
.odm-hidden { display: none !important; }
"""

DEFAULT_JS = """// odm2html runtime: HTML5 + custom-pattern validation, REDCap calc auto-eval,
// and REDCap BranchingLogic show/hide. Expressions arrive precompiled as
// data-calc and data-branch attributes (REDCap syntax → JS at gen time).
(function () {
  'use strict';

  function getValue(name) {
    const inputs = document.querySelectorAll('[name="' + CSS.escape(name) + '"]');
    if (inputs.length === 0) return '';
    const first = inputs[0];
    if (first.type === 'checkbox') {
      return first.checked ? first.value : '0';
    }
    if (first.type === 'radio') {
      const checked = Array.from(inputs).find(i => i.checked);
      return checked ? checked.value : '';
    }
    return first.value;
  }

  // REDCap calc helpers (referenced by compiled data-calc expressions).
  function red_round(x, n) {
    const f = Math.pow(10, Number(n) || 0);
    return Math.round(Number(x) * f) / f;
  }
  function red_rounddown(x, n) {
    const f = Math.pow(10, Number(n) || 0);
    return Math.floor(Number(x) * f) / f;
  }
  function red_roundup(x, n) {
    const f = Math.pow(10, Number(n) || 0);
    return Math.ceil(Number(x) * f) / f;
  }
  function red_sum() {
    return Array.from(arguments).reduce((a, b) => Number(a) + (Number(b) || 0), 0);
  }
  function red_mean() {
    const a = Array.from(arguments).map(Number).filter(x => !isNaN(x));
    return a.length ? red_sum.apply(null, a) / a.length : 0;
  }
  function red_min() { return Math.min.apply(null, Array.from(arguments).map(Number)); }
  function red_max() { return Math.max.apply(null, Array.from(arguments).map(Number)); }
  function red_abs(x) { return Math.abs(Number(x)); }
  function red_sqrt(x) { return Math.sqrt(Number(x)); }
  function red_if(cond, a, b) { return cond ? a : b; }
  function red_datediff(d1, d2, unit) {
    const t1 = d1 === 'today' ? Date.now() : Date.parse(d1);
    const t2 = d2 === 'today' ? Date.now() : Date.parse(d2);
    if (isNaN(t1) || isNaN(t2)) return '';
    const ms = Math.abs(t2 - t1);
    const days = ms / 86400000;
    switch ((unit || 'd').toLowerCase()) {
      case 'y': return days / 365.25;
      case 'm': return days / 30.4375;
      case 'w': return days / 7;
      case 'd': return days;
      case 'h': return ms / 3600000;
      default:  return days;
    }
  }

  const HELPERS = { red_round, red_rounddown, red_roundup, red_sum, red_mean,
                    red_min, red_max, red_abs, red_sqrt, red_if, red_datediff,
                    getValue };

  function evalExpr(expr) {
    const names = Object.keys(HELPERS);
    const vals = names.map(k => HELPERS[k]);
    return Function.apply(null, names.concat(['return (' + expr + ');']))
                   .apply(null, vals);
  }

  function evaluateBranching() {
    document.querySelectorAll('[data-branch]').forEach(el => {
      const expr = el.getAttribute('data-branch');
      if (!expr) return;
      try {
        const visible = !!evalExpr(expr);
        el.classList.toggle('odm-hidden', !visible);
        // Disable inputs inside hidden items so they don't block submission
        el.querySelectorAll('input,select,textarea').forEach(i => i.disabled = !visible);
      } catch (e) {
        console.warn('Branching error for', el.dataset.oid, e);
      }
    });
  }

  function evaluateCalcs() {
    document.querySelectorAll('[data-calc]').forEach(el => {
      const expr = el.getAttribute('data-calc');
      if (!expr) return;
      try {
        const result = evalExpr(expr);
        if (result === undefined || result === null || (typeof result === 'number' && isNaN(result))) {
          el.value = '';
        } else {
          el.value = result;
        }
      } catch (e) {
        console.warn('Calc error for', el.name, e);
      }
    });
  }

  function showError(item, msg) {
    item.classList.add('odm-error');
    let span = item.querySelector('.odm-error-msg');
    if (!span) {
      span = document.createElement('span');
      span.className = 'odm-error-msg';
      item.appendChild(span);
    }
    span.textContent = msg;
  }

  function clearError(item) {
    item.classList.remove('odm-error');
    const span = item.querySelector('.odm-error-msg');
    if (span) span.remove();
  }

  function setupForm(form) {
    form.setAttribute('novalidate', '');
    form.addEventListener('input', () => {
      evaluateBranching();
      evaluateCalcs();
    });
    form.addEventListener('change', () => {
      evaluateBranching();
      evaluateCalcs();
    });
    form.addEventListener('submit', (ev) => {
      let ok = true;
      form.querySelectorAll('.odm-item').forEach(item => {
        if (item.classList.contains('odm-hidden')) {
          clearError(item);
          return;
        }
        const inputs = item.querySelectorAll('input,select,textarea');
        let itemOk = true;
        let msg = '';
        inputs.forEach(input => {
          if (input.disabled) return;
          if (!input.checkValidity()) {
            itemOk = false;
            msg = input.validationMessage || 'Invalid value';
          }
        });
        if (itemOk) clearError(item); else { showError(item, msg); ok = false; }
      });
      if (!ok) {
        ev.preventDefault();
        const firstErr = form.querySelector('.odm-error');
        if (firstErr) firstErr.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
  }

  function setup() {
    document.querySelectorAll('form.odm-form').forEach(setupForm);
    evaluateBranching();
    evaluateCalcs();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
})();
"""


# ============================================================================
# Helpers
# ============================================================================

class SafeDict(dict):
    def __missing__(self, key):
        return ""


def render(tpl: str, **kwargs) -> str:
    return tpl.format_map(SafeDict(kwargs))


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def q(tag: str) -> str:
    return f"{{{ODM_NS}}}{tag}"


def rc(attr: str) -> str:
    return f"{{{REDCAP_NS}}}{attr}"


def find_all(elem, *path):
    return elem.findall("/".join(q(t) for t in path))


def find_one(elem, *path):
    return elem.find("/".join(q(t) for t in path))


def text_of(elem, *path) -> str:
    found = find_one(elem, *path)
    return (found.text or "").strip() if found is not None and found.text else ""


def redcap_attr(elem, name: str) -> str:
    return elem.get(rc(name), "") if elem is not None else ""


def detect_redcap(root) -> bool:
    if any(k.startswith(f"{{{REDCAP_NS}}}") for k in root.attrib):
        return True
    for elem in root.iter():
        if any(k.startswith(f"{{{REDCAP_NS}}}") for k in elem.attrib):
            return True
    return False


def question_text(item) -> str:
    return text_of(item, "Question", "TranslatedText") or item.get("Name") or item.get("OID") or ""


def codelist_options(codelist) -> list[tuple[str, str]]:
    out = []
    for cli in find_all(codelist, "CodeListItem"):
        code = cli.get("CodedValue", "")
        decode = text_of(cli, "Decode", "TranslatedText") or code
        out.append((code, decode))
    return out


def parse_pipe_choices(s: str) -> list[tuple[str, str]]:
    out = []
    for chunk in s.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            val, label = chunk.split(",", 1)
            out.append((val.strip(), label.strip()))
        else:
            out.append((chunk, chunk))
    return out


def item_codelist(item, model):
    cl_ref = find_one(item, "CodeListRef")
    if cl_ref is None:
        return None
    return model.codelists.get(cl_ref.get("CodeListOID"))


def sanitize_filename(s: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    return cleaned or "form"


# ============================================================================
# REDCap expression → JS compilation
# ============================================================================

REDCAP_FUNC_MAP = {
    "round": "red_round",
    "rounddown": "red_rounddown",
    "roundup": "red_roundup",
    "sum": "red_sum",
    "mean": "red_mean",
    "min": "red_min",
    "max": "red_max",
    "abs": "red_abs",
    "sqrt": "red_sqrt",
    "if": "red_if",
    "datediff": "red_datediff",
}


def _compile_redcap_field_refs(expr: str, as_number: bool = False) -> str:
    """Convert [field] and [field(N)] to getValue() calls."""
    # [field(N)] → getValue('field___N')
    expr = re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\(([A-Za-z0-9_]+)\)\]",
                  r"getValue('\1___\2')", expr)
    # [field] → getValue('field')  (or Number(getValue('field')||0) when as_number)
    if as_number:
        expr = re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\]",
                      r"(Number(getValue('\1'))||0)", expr)
    else:
        expr = re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\]",
                      r"getValue('\1')", expr)
    return expr


def _replace_funcs(expr: str) -> str:
    for redcap_fn, js_fn in REDCAP_FUNC_MAP.items():
        expr = re.sub(rf"\b{redcap_fn}\(", f"{js_fn}(", expr, flags=re.IGNORECASE)
    return expr


def redcap_branch_to_js(expr: str) -> str:
    """Compile a REDCap BranchingLogic expression to a JS boolean expression."""
    if not expr:
        return ""
    expr = _compile_redcap_field_refs(expr, as_number=False)
    expr = expr.replace("^", "**")
    expr = re.sub(r"<>", "!=", expr)
    # = → == when not part of ==, !=, <=, >=, =>=
    expr = re.sub(r"(?<![<>=!])=(?!=)", "==", expr)
    expr = re.sub(r"\bAND\b", "&&", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bOR\b", "||", expr, flags=re.IGNORECASE)
    expr = _replace_funcs(expr)
    return expr.strip()


def redcap_calc_to_js(formula: str) -> str:
    """Compile a REDCap calc formula to a JS numeric expression."""
    if not formula:
        return ""
    expr = _compile_redcap_field_refs(formula, as_number=True)
    expr = expr.replace("^", "**")
    expr = _replace_funcs(expr)
    return expr.strip()


# ============================================================================
# Model
# ============================================================================

class OdmModel:
    def __init__(self, mdv):
        self.mdv = mdv
        self.forms = {e.get("OID"): e for e in find_all(mdv, "FormDef")}
        self.groups = {e.get("OID"): e for e in find_all(mdv, "ItemGroupDef")}
        self.items = {e.get("OID"): e for e in find_all(mdv, "ItemDef")}
        self.codelists = {e.get("OID"): e for e in find_all(mdv, "CodeList")}


# ============================================================================
# Renderer — owns rules, templates, classes, per-field overrides
# ============================================================================

class Renderer:
    def __init__(self, rules: dict, redcap_mode: bool):
        self.rules = rules
        self.redcap_mode = redcap_mode
        self.classes = rules["classes"]
        self.templates = rules["templates"]
        self.overrides = rules.get("fieldOverrides", {})
        self.js_validation = bool(rules.get("validation", {}).get("javascript", True))
        self.branching_enabled = bool(rules.get("validation", {}).get("branchingLogic", True))
        self.calc_enabled = bool(rules.get("validation", {}).get("calcAutoEval", True))
        self.custom_patterns = rules.get("validation", {}).get("customPatterns", {})

    def _class_kwargs(self, override_classes: dict | None = None) -> dict:
        # Always inject `class_<name>` for each class entry
        classes = self.classes if not override_classes else {**self.classes, **override_classes}
        return {f"class_{k}": v for k, v in classes.items()}

    def _override_for(self, item) -> dict:
        variable = redcap_attr(item, "Variable")
        name = item.get("Name") or ""
        oid = item.get("OID") or ""
        for key in (variable, name, oid):
            if key and key in self.overrides:
                return self.overrides[key]
        return {}

    def _render(self, template_name: str, override: dict | None = None, **kwargs) -> str:
        tpl_override = (override or {}).get("templateOverrides", {}).get(template_name)
        tpl = tpl_override if tpl_override is not None else self.templates.get(template_name, "")
        class_kwargs = self._class_kwargs((override or {}).get("classOverrides"))
        return render(tpl, **class_kwargs, **kwargs)

    def required_marker(self, mandatory: bool) -> str:
        if not mandatory:
            return ""
        return self._render("required_marker")

    def field_note(self, note: str) -> str:
        if not note:
            return ""
        return self._render("field_note", text=escape(note)) + "\n"

    def branch_attr(self, item) -> str:
        if not (self.redcap_mode and self.branching_enabled):
            return ""
        expr = redcap_attr(item, "BranchingLogic")
        if not expr:
            return ""
        js = redcap_branch_to_js(expr)
        if not js:
            return ""
        return self._render("branch_attr", expr=escape(js, quote=True))

    # ----- per-widget renderers (REDCap-aware) -----

    def render_text(self, item, oid, name, label, required, vtype, note, override) -> str:
        vmap = self.rules.get("redcapValidationMap", {})
        input_type = vmap.get(vtype, "text") if self.redcap_mode and vtype else self._datatype_input_type(item)
        attrs = self._common_attrs(required, override)

        if input_type == "text":
            length = item.get("Length")
            default_len = str(self.rules.get("ignoreDefaultLength", ""))
            if length and length != default_len:
                attrs.append(f'maxlength="{escape(length, quote=True)}"')
            # Custom pattern via TextValidationType (e.g. zipcode, mrn_10d, ssn)
            pat = self.custom_patterns.get(vtype) if self.redcap_mode else None
            if pat:
                attrs.append(f'pattern="{escape(pat, quote=True)}"')

        if input_type == "number":
            is_int = vtype in INTEGER_VTYPES or (item.get("DataType") or "").lower() == "integer"
            attrs.append('step="1"' if is_int else 'step="any"')
        if vtype == "zipcode":
            attrs.append('inputmode="numeric"')

        for rng in find_all(item, "RangeCheck"):
            comp = rng.get("Comparator", "")
            val = text_of(rng, "CheckValue")
            if not val or input_type != "number":
                continue
            if comp in ("GE", "GT"):
                attrs.append(f'min="{escape(val, quote=True)}"')
            elif comp in ("LE", "LT"):
                attrs.append(f'max="{escape(val, quote=True)}"')

        return self._render(
            "item_text", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            input_type=input_type,
            attrs=self._fmt_attrs(attrs),
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_textarea(self, item, oid, name, label, required, note, override) -> str:
        attrs = self._common_attrs(required, override)
        return self._render(
            "item_textarea", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            rows="4",
            attrs=self._fmt_attrs(attrs),
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_file(self, item, oid, name, label, required, note, override) -> str:
        attrs = self._common_attrs(required, override)
        return self._render(
            "item_file", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            attrs=self._fmt_attrs(attrs),
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_select(self, item, oid, name, label, required, options, note, override) -> str:
        attrs = self._common_attrs(required, override)
        opt_lines = [
            self._render("select_option",
                         value=escape(v, quote=True), text=escape(t))
            for v, t in options
        ]
        return self._render(
            "item_select", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            attrs=self._fmt_attrs(attrs),
            options="\n".join(opt_lines),
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_radio(self, item, oid, name, label, required, options, note, override) -> str:
        req_attr = ' required' if required else ''
        opt_lines = []
        for i, (val, text) in enumerate(options):
            opt_lines.append(self._render(
                "radio_option",
                id=f"item_{escape(oid)}_{i}",
                name=escape(name, quote=True),
                value=escape(val, quote=True),
                text=escape(text),
                attrs=req_attr,
            ))
        return self._render(
            "item_radio", override=override,
            oid=escape(oid),
            label=escape(label),
            options="\n".join(opt_lines),
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_checkbox_group(self, items, model, mandatory, override) -> str:
        first = items[0]
        label = question_text(first)
        note = redcap_attr(first, "FieldNote")
        choices: dict[str, str] = {}
        cl = item_codelist(first, model)
        if cl is not None:
            chs = redcap_attr(cl, "CheckboxChoices")
            if chs:
                choices = dict(parse_pipe_choices(chs))
        group_oid = first.get("OID", "").rsplit("___", 1)[0] or first.get("OID", "")
        opt_lines = []
        for it in items:
            oid = it.get("OID", "")
            name = it.get("Name") or oid
            suffix = oid.rsplit("___", 1)[-1] if "___" in oid else ""
            choice_label = choices.get(suffix) or question_text(it)
            opt_lines.append(self._render(
                "checkbox_option",
                id=f"item_{escape(oid)}",
                name=escape(name, quote=True),
                text=escape(choice_label),
                attrs="",
            ))
        return self._render(
            "item_checkbox_group", override=override,
            oid=escape(group_oid),
            label=escape(label),
            options="\n".join(opt_lines),
            required_marker=self.required_marker(mandatory),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(first),
        )

    def render_slider(self, item, oid, name, label, required, note, override) -> str:
        attrs = self._common_attrs(required, override)
        slider_labels_attr = redcap_attr(item, "SliderLabels")
        labels = [l.strip() for l in slider_labels_attr.split("|") if l.strip()] if slider_labels_attr else []
        min_v, max_v = "0", "100"
        for rng in find_all(item, "RangeCheck"):
            comp = rng.get("Comparator", "")
            val = text_of(rng, "CheckValue")
            if not val:
                continue
            if comp in ("GE", "GT"):
                min_v = val
            elif comp in ("LE", "LT"):
                max_v = val
        labels_html = ""
        if labels:
            spans = "".join(self._render("slider_label", text=escape(l)) for l in labels)
            labels_html = self._render("slider_labels", labels=spans) + "\n"
        return self._render(
            "item_slider", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            min=escape(min_v, quote=True), max=escape(max_v, quote=True),
            attrs=self._fmt_attrs(attrs),
            slider_labels_html=labels_html,
            required_marker=self.required_marker(required),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_calc(self, item, oid, name, label, note, override) -> str:
        formula = redcap_attr(item, "Calculation")
        calc_js = redcap_calc_to_js(formula) if (self.calc_enabled and formula) else ""
        return self._render(
            "item_calc", override=override,
            oid=escape(oid), id=f"item_{escape(oid)}",
            name=escape(name, quote=True),
            label=escape(label),
            calc_js=escape(calc_js, quote=True),
            attrs="",
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    def render_descriptive(self, item, oid, label, note, override) -> str:
        return self._render(
            "item_descriptive", override=override,
            oid=escape(oid),
            label=escape(label),
            note_html=self.field_note(note),
            branch_attr=self.branch_attr(item),
        )

    # ----- dispatch + helpers -----

    def _datatype_input_type(self, item) -> str:
        dt = (item.get("DataType") or "text").lower()
        return self.rules.get("dataTypeMap", {}).get(dt, "text")

    def _common_attrs(self, required: bool, override: dict | None) -> list[str]:
        attrs = []
        if required:
            attrs.append("required")
        extra = (override or {}).get("extraAttrs", {}) if override else {}
        for k, v in extra.items():
            attrs.append(f'{k}="{escape(str(v), quote=True)}"')
        return attrs

    def _fmt_attrs(self, attrs: list[str]) -> str:
        return (" " + " ".join(attrs)) if attrs else ""

    def render_item(self, item, mandatory, model) -> str:
        oid = item.get("OID", "")
        name = item.get("Name") or oid
        label = question_text(item)
        note = redcap_attr(item, "FieldNote") if self.redcap_mode else ""
        override = self._override_for(item)

        if self.redcap_mode:
            ftype = redcap_attr(item, "FieldType")
            vtype = redcap_attr(item, "TextValidationType")
            widget = self.rules.get("redcapWidgetMap", {}).get(ftype, "")

            if widget == "descriptive":
                return self.render_descriptive(item, oid, label, note, override)
            if widget == "file":
                return self.render_file(item, oid, name, label, mandatory, note, override)
            if widget == "slider":
                return self.render_slider(item, oid, name, label, mandatory, note, override)
            if widget == "calc":
                return self.render_calc(item, oid, name, label, note, override)
            if widget == "textarea":
                return self.render_textarea(item, oid, name, label, mandatory, note, override)
            if widget == "yesno":
                return self.render_radio(item, oid, name, label, mandatory,
                                         [("1", "Yes"), ("0", "No")], note, override)
            if widget == "truefalse":
                return self.render_radio(item, oid, name, label, mandatory,
                                         [("1", "True"), ("0", "False")], note, override)
            if widget == "radio":
                cl = item_codelist(item, model)
                opts = codelist_options(cl) if cl is not None else []
                return self.render_radio(item, oid, name, label, mandatory, opts, note, override)
            if widget == "select":
                cl = item_codelist(item, model)
                opts = codelist_options(cl) if cl is not None else []
                return self.render_select(item, oid, name, label, mandatory, opts, note, override)
            if widget == "checkbox":
                return self.render_radio(item, oid, name, label, mandatory,
                                         [("1", "Yes"), ("0", "No")], note, override)
            return self.render_text(item, oid, name, label, mandatory, vtype, note, override)

        # ---- generic (non-REDCap) path ----
        cl = item_codelist(item, model)
        if cl is not None:
            opts = codelist_options(cl)
            threshold = self.rules.get("codeListAsRadioThreshold", 4)
            if len(opts) <= threshold:
                return self.render_radio(item, oid, name, label, mandatory, opts, "", override)
            return self.render_select(item, oid, name, label, mandatory, opts, "", override)
        return self.render_text(item, oid, name, label, mandatory, "", "", override)

    def render_group(self, group, model) -> str:
        gid = group.get("OID", "")
        name = group.get("Name", "")
        out = []
        if name and self.rules.get("includeSectionHeaders", True):
            out.append(self._render("section_header",
                                    oid=escape(gid), title=escape(name)))

        irefs = find_all(group, "ItemRef")
        i = 0
        while i < len(irefs):
            iref = irefs[i]
            item = model.items.get(iref.get("ItemOID"))
            if item is None:
                i += 1
                continue
            mandatory = iref.get("Mandatory") == "Yes"
            if self.redcap_mode:
                ftype = redcap_attr(item, "FieldType")
                variable = redcap_attr(item, "Variable")
                if ftype == "checkbox" and variable:
                    siblings = [item]
                    j = i + 1
                    while j < len(irefs):
                        nxt_iref = irefs[j]
                        nxt_item = model.items.get(nxt_iref.get("ItemOID"))
                        if nxt_item is None:
                            break
                        if (redcap_attr(nxt_item, "FieldType") == "checkbox"
                                and redcap_attr(nxt_item, "Variable") == variable):
                            siblings.append(nxt_item)
                            j += 1
                        else:
                            break
                    if len(siblings) > 1:
                        override = self._override_for(siblings[0])
                        out.append(self.render_checkbox_group(siblings, model, mandatory, override))
                        i = j
                        continue
            out.append(self.render_item(item, mandatory, model))
            i += 1
        return "\n".join(out)

    def render_form(self, form, model) -> str:
        oid = form.get("OID", "")
        name = form.get("Name", oid)
        title_html = ""
        if self.rules.get("includeFormTitle", True):
            title_html = self._render("form_title", name=escape(name))
        body_parts = []
        for igref in find_all(form, "ItemGroupRef"):
            group = model.groups.get(igref.get("ItemGroupOID"))
            if group is None:
                continue
            body_parts.append(self.render_group(group, model))
        submit_html = self._render(
            "submit_button",
            label=escape(self.rules.get("submitButtonLabel", "Submit")),
        )
        return self._render(
            "form",
            oid=escape(oid),
            title_html=title_html,
            body="\n".join(body_parts),
            submit_html=submit_html,
        )

    def render_page(self, title: str, body: str, css_href: str, js_href: str) -> str:
        return self._render(
            "page",
            title=escape(title),
            body=body,
            css_href=escape(css_href, quote=True),
            js_href=escape(js_href, quote=True),
        )


# ============================================================================
# Driver
# ============================================================================

def load_rules(path: Path | None) -> dict:
    if path is None:
        return copy.deepcopy(DEFAULT_RULES)
    user = json.loads(path.read_text(encoding="utf-8"))
    return deep_merge(DEFAULT_RULES, user)


def resolve_asset(custom_path: str | None, default_content: str) -> str:
    if not custom_path:
        return default_content
    return Path(custom_path).read_text(encoding="utf-8")


def convert(odm_path: Path, rules: dict, out_prefix_stem: str
            ) -> tuple[str, list[tuple[str, str, str]], str, str]:
    """Parse ODM and produce per-form HTML pages.

    Returns (study_title, [(form_oid, form_name, html)], css_text, js_text).
    HTML pages reference '<out_prefix_stem>.css' and '<out_prefix_stem>.js'.
    """
    tree = ET.parse(odm_path)
    root = tree.getroot()
    study = root.find(q("Study"))
    if study is None:
        raise SystemExit("No <Study> element found in ODM file")
    mdv = study.find(q("MetaDataVersion"))
    if mdv is None:
        raise SystemExit("No <MetaDataVersion> element found in ODM file")

    redcap_mode = detect_redcap(root)
    renderer = Renderer(rules, redcap_mode)
    model = OdmModel(mdv)
    title = text_of(study, "GlobalVariables", "StudyName") or "ODM Forms"

    assets = rules.get("assets", {})
    css_text = resolve_asset(assets.get("cssPath"), DEFAULT_CSS)
    js_text = resolve_asset(assets.get("jsPath"), DEFAULT_JS)

    css_href = (assets.get("cssFilename") or f"{out_prefix_stem}.css")
    js_href = (assets.get("jsFilename") or f"{out_prefix_stem}.js")

    results = []
    for form in find_all(mdv, "FormDef"):
        oid = form.get("OID")
        name = form.get("Name", oid)
        body = renderer.render_form(form, model)
        page_title = f"{title} — {name}" if title else name
        html = renderer.render_page(page_title, body, css_href, js_href)
        results.append((oid, name, html))
    return title, results, css_text, js_text


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Convert ODM XML to HTML forms (REDCap-aware)")
    p.add_argument("input", type=Path, help="Path to ODM XML file")
    p.add_argument(
        "-o", "--out", type=Path,
        help="Output path. With one form, writes that exact file. With multiple "
             "forms, treats the value as a prefix and writes <prefix>_<form>.html "
             "for each form. Always also writes <prefix>.css and <prefix>.js.",
    )
    p.add_argument("-r", "--rules", type=Path, help="JSON rules file (optional)")
    args = p.parse_args(argv)

    rules = load_rules(args.rules)

    out = args.out
    if out is None:
        raise SystemExit("-o / --out is required (need a path to write CSS/JS assets next to)")

    stem = out.stem if out.suffix.lower() == ".html" else out.name
    parent = out.parent if str(out.parent) else Path(".")

    _, forms, css_text, js_text = convert(args.input, rules, stem)
    if not forms:
        raise SystemExit("No <FormDef> elements found in ODM file")

    css_file = parent / f"{stem}.css"
    js_file = parent / f"{stem}.js"
    css_file.write_text(css_text, encoding="utf-8")
    js_file.write_text(js_text, encoding="utf-8")
    print(f"Wrote {css_file}", file=sys.stderr)
    print(f"Wrote {js_file}", file=sys.stderr)

    if len(forms) == 1 and out.suffix.lower() == ".html":
        out.write_text(forms[0][2], encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)
        return 0

    for oid, name, html in forms:
        suffix = sanitize_filename(name or oid)
        target = parent / f"{stem}_{suffix}.html"
        target.write_text(html, encoding="utf-8")
        print(f"Wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
