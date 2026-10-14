# Stage 3 deterministic evaluator checks

All cases are synthetic. The expected outcomes below were defined from the
source sections documented in [WHO-IITT.md](./clinical-protocols/WHO-IITT.md)
before running the evaluator tests. These tests establish software behavior
against the encoded subset only; they do not establish clinical validity,
sensitivity, specificity, or safety.

## Source-derived expectations

| Synthetic case | Source-derived expected output | Source trace |
| --- | --- | --- |
| Adult HR 49 bpm | `red_escalation`, category `red`, rule `IITT-ADULT-RED-HEART-RATE` met with the HR observation as evidence. | Adult poster, red criteria / Circulation: HR <50 or >150 |
| Adult HR 55 bpm | `clinician_review_required`, no category; adult red HR rule not met, high-risk HR rule met. | Adult poster, high-risk HR <60 or >130; source says high-risk signs need up-triage or supervising-clinician review |
| Adult values HR 75, RR 20, temp 37°C, SpO₂ 98%, AVPU A | No encoded high-risk criterion met; `indeterminate`, no category, unsupported criteria disclosed. | Adult poster, high-risk vital-sign rows; unimplemented red/yellow criteria prohibit inferring green |
| Adult RR 31, temp 35.9°C, SpO₂ 91%, or AVPU V (each evaluated independently) | Matching adult high-risk rule is met, evidence identifies that observation, `clinician_review_required`; no category assigned. | Adult poster, high-risk RR/temp/SpO₂/AVPU rows |
| Child age 1 month, temperature 35.9°C | `red_escalation`, category `red`, with temperature evidence. | Pediatric poster, red criterion: age <2 months and temp <36°C or >39°C |
| Child age 6 months, HR 181 bpm, RR 51, temp 35.9°C, SpO₂ 91%, or AVPU V (each evaluated independently) | Matching child high-risk rule is met, evidence identifies that observation, `clinician_review_required`; no category assigned. | Pediatric poster, HR/RR age-band tables and high-risk temp/SpO₂/AVPU rows |
| Child age 36 months, HR 161 bpm | `clinician_review_required`, no category. | Pediatric poster, high HR cut-point 160 for age 1–4 years; source instructs review for high-risk vital signs |
| Child at printed high/low vital-sign cut-points | The affected rule and overall result remain indeterminate; a threshold equality is not declared normal. | Pediatric poster, high/low tables omit comparator symbols at the exact cut-points |
| Missing observations / complaint | `indeterminate`; each missing supported measurement and absent complaint is identified. | The evaluator does not substitute values or assign green from absent criteria |
| Invalid unit, naive/future timestamp, or duplicate measurement | Input validation error; evaluation does not proceed. | Engineering validation policy; not a clinical IITT criterion |
| Fahrenheit 96.8°F / 96.79°F | First is not below the 36°C cutoff; second meets the adult high-risk temperature rule after exact conversion. | Adult poster, temperature <36°C; exact unit conversion is engineering behavior |

## Test execution record

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_iitt_evaluator.py tests\test_mcp_tools.py tests\test_health.py -q
```

Actual result on 2026-10-15: **36 passed**. Ruff reported **All checks
passed** for the changed evaluator, MCP, and test files. Pylance reported no
errors in those changed Python files. No PostgreSQL repository tests were
run; they require a dedicated `TEST_DATABASE_URL`.

## Limitations and next steps

- The evaluator implements only the criteria enumerated in the protocol
  record. A non-red result never means the patient is low acuity or safe.
- Remaining complaint-based and reference-card criteria require clinically
  governed, source-reviewed structured inputs and separate rule tests.
- No stale-observation interval is claimed because the IITT source does not
  define one.
- WHO's separate reuse terms for the adult and pediatric posters were not
  visible in those documents. Confirm permissions before commercial use or
  redistribution of an adaptation.
- Synthetic unit tests cannot support clinical performance or deployment
  claims. Clinical review, local authorization, validation, and governance
  are prerequisites to any operational use.
